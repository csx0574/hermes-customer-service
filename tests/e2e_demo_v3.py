"""
W8 端到端 — 5 件套: Gateway + Store + Intent + Sentiment + Alert/Mail.
1 用户连续发 6 条负向消息, 触发 angry_burst + negative_trend, 邮件发送.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from alert import AlertDetector, AlertLog, Mailer
from channel_gateway import ChannelMessage, Gateway, MockWecomAdapter
from intent_classifier import HandoffTracker, Intent, classify
from message_store import MessageStore
from sentiment import SentimentStore
from ticket import TicketStatus, TicketStore


def mock_llm(text: str):
    return (Intent.CONSULT, 0.5)


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        store = MessageStore(p / "m.db")
        tickets = TicketStore(p / "t.db")
        sent = SentimentStore(p / "s.db")
        alerts = AlertLog(p / "a.db")
        det = AlertDetector()
        mail = Mailer()
        gw = Gateway()
        gw.register(MockWecomAdapter())
        await gw.start()
        tracker = HandoffTracker(window=2, threshold=0.6)

        cases = [
            (1000.0, "你们是骗子! 垃圾!"),       # angry 触发
            (1010.0, "太慢了一周还没发货"),       # negative
            (1020.0, "气死, 差劲"),              # negative
            (1030.0, "骗子骗子"),                 # angry (cooldown 不再触发)
            (1040.0, "失望透了"),                 # negative 触发 trend
            (1050.0, "不行不要了"),               # 连续 trend
        ]
        ticket_id = None
        mail_count = 0
        for ts, text in cases:
            msg = ChannelMessage(channel="wecom", user_id="u_vip", text=text)
            await gw.ingest(msg)
            store.save(msg)
            r = classify(text, llm_fallback=mock_llm)
            sp = sent.record("wecom", "u_vip", text, ts=ts)

            # 工单 (第 1 次 handoff 时创建)
            if r.should_handoff and ticket_id is None:
                t = tickets.create("u_vip", "wecom", r.intent.value,
                                   history_dump=store.recent_context("u_vip", limit=5))
                ticket_id = t.id
                tickets.update_status(t.id, TicketStatus.OPEN)

            # 告警 + 邮件
            fired = det.feed("u_vip", "wecom", sp.avg_score)
            for a in fired:
                aid = alerts.record(a)
                mail.send("sup@hermes.local", f"[{a.kind.value}] {a.user_id}",
                          f"score={a.score:.2f} extra={a.extra}")
                alerts.mark_mailed(aid)
                mail_count += 1

            await gw.reply("wecom", "u_vip", f"[{sp.label.value}]")
            print(f"  t={ts:7.1f} score={sp.avg_score:+.2f}  alerts={len(fired)}  mail_total={mail_count}")

        # 验证
        assert mail_count >= 1, f"应该至少 1 封邮件, 实际 {mail_count}"
        assert len(alerts.recent()) == mail_count
        kinds_sent = {a.kind.value for a in []}  # 已发送的 alert kinds
        log_kinds = {r["kind"] for r in alerts.recent()}
        assert "angry_burst" in log_kinds, f"应触发 angry_burst, 实际 {log_kinds}"
        # 邮件内容抽查
        assert any("angry_burst" in s for s, _, _ in mail.sent) or any("negative_trend" in s for _, s, _ in mail.sent)
        # 工单状态
        if ticket_id:
            tickets.update_status(ticket_id, TicketStatus.CLOSED)
            assert tickets.get(ticket_id).status == TicketStatus.CLOSED
        print(f"\nOK: 5 件套端到端 — {mail_count} 封邮件, alert kinds={log_kinds}, 工单 CLOSED")
        await gw.stop()


if __name__ == "__main__":
    asyncio.run(main())
