"""
W4 端到端 demo — Gateway 收消息 → MessageStore 存 → Intent 分类 → Reply.

ponytail: 5 类意图 + 1 个长尾 + 1 个强负面, 走完三件套.
不接真实 LLM, llm_fallback 走 mock.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from channel_gateway import ChannelMessage, Gateway, MockFeishuAdapter, MockWecomAdapter
from intent_classifier import HandoffTracker, Intent, classify
from message_store import MessageStore


# ponytail: LLM 兑底 mock — 返回 CONS+0.5 (触发转人工, 主公能直接看到效果)
def mock_llm(text: str):
    return (Intent.CONSULT, 0.5)


async def main() -> None:
    # 1) 三件套 setup
    with tempfile.TemporaryDirectory() as d:
        store = MessageStore(Path(d) / "e2e.db")
        gw = Gateway()
        gw.register(MockWecomAdapter())
        gw.register(MockFeishuAdapter())
        await gw.start()

        tracker = HandoffTracker(window=2, threshold=0.6)

        cases = [
            ("wecom", "u1", "我要退款, 订单 #12345"),
            ("wecom", "u1", "我改主意了, 申请退款"),
            ("feishu", "u2", "你们是骗子! 垃圾!"),  # 强负面
            ("feishu", "u2", "气死了!"),  # 强负面续
            ("wecom", "u3", "emm 那啥"),  # 长尾
            ("wecom", "u3", "那个"),  # 续, 触发连续低 conf 转人工
        ]
        for ch, uid, text in cases:
            msg = ChannelMessage(channel=ch, user_id=uid, text=text)
            await gw.ingest(msg)
            store.save(msg)

            r = classify(text, llm_fallback=mock_llm)
            handoff_fired = tracker.observe(r.confidence) or r.should_handoff

            # 4) reply — 真实链路走 Mock adapter
            if handoff_fired:
                reply = f"[转人工] {r.intent.value} conf={r.confidence:.2f}"
            else:
                reply = f"[{r.intent.value}] 已记录"

            await gw.reply(ch, uid, reply)
            print(f"  {ch:7s} {uid}: {text!r}")
            print(f"    → intent={r.intent.value:12s} conf={r.confidence:.2f} "
                  f"rule={r.rule_matched} handoff={handoff_fired}")
            print(f"    → reply: {reply}")
            print(f"    → history len: {len(store.history(uid))}")

        # 5) 验证: u1 看到自己 2 条历史, u3 第 2 条触发转人工
        ctx_u1 = store.recent_context("u1")
        ctx_u3 = store.recent_context("u3")
        assert "我要退款" in ctx_u1
        assert "emm" in ctx_u3
        await gw.stop()
        print("\nOK: 端到端跑通 — 6 条消息, 规则+LLM+转人工+持久化全验证")


if __name__ == "__main__":
    asyncio.run(main())
