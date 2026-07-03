"""
W6 端到端 demo — Gateway + MessageStore + Intent + Ticket + Sentiment 4 件套.

ponytail: 1 个用户 6 轮, 情绪从 angry → positive, 工单从 NEW → CLOSED, 意图转人工.
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from channel_gateway import ChannelMessage, Gateway, MockWecomAdapter
from intent_classifier import HandoffTracker, Intent, classify
from message_store import MessageStore
from sentiment import SentimentStore
from ticket import TicketStatus, TicketStore


def mock_llm(text: str):
    return (Intent.CONSULT, 0.5)


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        db = Path(d)
        store = MessageStore(db / "msg.db")
        tickets = TicketStore(db / "t.db")
        sent = SentimentStore(db / "s.db")
        gw = Gateway()
        gw.register(MockWecomAdapter())
        await gw.start()
        tracker = HandoffTracker(window=2, threshold=0.6)

        # 1 个用户 6 轮, 情绪 angry → positive
        cases = [
            (1000.0, "你们是骗子! 垃圾! 投诉!"),    # angry, complaint, handoff
            (1002.0, "我等了一周还没发货, 失望"),   # negative, complaint
            (1010.0, "好的, 我提供订单号 #12345"),  # neutral
            (1012.0, "哦原来如此, 谢谢客服"),       # positive
            (1020.0, "太棒了! 解决了!"),            # positive
            (1022.0, "非常感谢!"),                  # positive
        ]
        ticket_id = None
        for ts, text in cases:
            # 1) Gateway 收
            msg = ChannelMessage(channel="wecom", user_id="u_vip", text=text)
            await gw.ingest(msg)
            store.save(msg)
            # 2) Intent
            r = classify(text, llm_fallback=mock_llm)
            # 3) Sentiment 入桶
            sp = sent.record("wecom", "u_vip", text, ts=ts)
            # 4) Ticket — 转人工时创建
            if r.should_handoff and ticket_id is None:
                history = store.recent_context("u_vip", limit=5)
                t = tickets.create("u_vip", "wecom", r.intent.value, history_dump=history)
                ticket_id = t.id
                tickets.update_status(t.id, TicketStatus.OPEN)
            # 5) Reply
            if ticket_id and r.should_handoff:
                reply = f"[转人工 #{ticket_id[:6]}] {sp.label.value}"
            else:
                reply = f"[{r.intent.value}] {sp.label.value}"
            await gw.reply("wecom", "u_vip", reply)
            print(f"  t={ts:7.1f}  {text!r}")
            print(f"    → intent={r.intent.value:12s} conf={r.confidence:.2f} "
                  f"sentiment={sp.label.value:8s} score={sp.avg_score:+.2f} "
                  f"handoff={r.should_handoff}")

        # 收尾: 工单关
        if ticket_id:
            tickets.update_status(ticket_id, TicketStatus.PENDING)
            tickets.update_status(ticket_id, TicketStatus.CLOSED)
            assert tickets.get(ticket_id).status == TicketStatus.CLOSED

        # 验证
        curve = sent.curve("u_vip")
        assert len(curve) >= 2, f"expected ≥2 buckets, got {len(curve)}"
        first_score = curve[0].avg_score
        last_score = curve[-1].avg_score
        assert first_score < last_score, f"曲线应该 angry→positive, got {first_score}→{last_score}"
        # ponytail: 直接 list_by_user 查 u_vip 的工单数, 不编不存在的方法
        user_tickets = tickets.list_by_user("u_vip")
        assert len(user_tickets) >= 1
        # 转人工时 dump 的 history 应包含用户原话
        dump = user_tickets[0].history_dump
        assert "骗子" in dump
        print(f"\nOK: 4 件套端到端 — 曲线 {len(curve)} 桶, score {first_score:+.2f}→{last_score:+.2f}")
        print(f"    工单 #{ticket_id[:6]} 状态: NEW→OPEN→PENDING→CLOSED, dump 含 {len(dump)} 字")
        await gw.stop()


if __name__ == "__main__":
    asyncio.run(main())
