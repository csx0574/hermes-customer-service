"""
永久测试 - 渠道网关骨架.

覆盖: 4 个 mock adapter + Gateway 路由 + ingest/reply/health.
ponytail: 单文件, 无 fixture, 无参数化 — 够用就行.
"""
from __future__ import annotations

import asyncio

import pytest

from channel_gateway import (
    ChannelMessage,
    Gateway,
    MockFeishuAdapter,
    MockWecomAdapter,
    build_default_gateway,
)


def test_mock_wecom_adapter_send():
    """Mock 企微发消息能写进 _sent."""
    async def _run() -> None:
        a = MockWecomAdapter()
        ok = await a.send("u_test", "hello")
        assert ok is True
        assert a._sent == [("u_test", "hello")]

    asyncio.run(_run())


def test_mock_feishu_adapter_send():
    async def _run() -> None:
        a = MockFeishuAdapter()
        ok = await a.send("u_f2", "hi")
        assert ok is True
        assert a._sent == [("u_f2", "hi")]

    asyncio.run(_run())


def test_gateway_registers_and_starts():
    """注册 2 个 adapter, start 后 health 都 ok."""
    async def _run() -> None:
        g = Gateway()
        g.register(MockWecomAdapter())
        g.register(MockFeishuAdapter())
        await g.start()
        h = await g.health()
        assert h["wecom"]["status"] == "ok"
        assert h["feishu"]["status"] == "ok"
        assert h["wecom"]["mock"] is True
        assert h["feishu"]["mock"] is True
        await g.stop()

    asyncio.run(_run())


def test_gateway_ingest_and_reply():
    """入站 2 路, 出站 2 路 — inbox 长度对, reply 都成功."""
    async def _run() -> None:
        # ponytail: 不用 build_default_gateway (会复用上次的 _sent).
        # 显式 fresh instances, 测试隔离.
        g = Gateway()
        g.register(MockWecomAdapter())
        g.register(MockFeishuAdapter())
        await g.start()
        await g.ingest(ChannelMessage(channel="wecom", user_id="u_w", text="退款"))
        await g.ingest(ChannelMessage(channel="feishu", user_id="u_f", text="查单"))
        assert len(g.inbox) == 2
        assert g.inbox[0].channel == "wecom"
        assert g.inbox[1].channel == "feishu"
        assert await g.reply("wecom", "u_w", "请提供订单号")
        assert await g.reply("feishu", "u_f", "已发货")
        # mock 各自只收到 1 条 (fresh instance)
        assert g.adapters["wecom"]._sent == [("u_w", "请提供订单号")]
        assert g.adapters["feishu"]._sent == [("u_f", "已发货")]
        await g.stop()

    asyncio.run(_run())


def test_gateway_reply_unknown_channel():
    """reply 不存在的 channel 返回 False — 不抛."""
    async def _run() -> None:
        g = Gateway()
        g.register(MockWecomAdapter())
        await g.start()
        ok = await g.reply("h5", "u_unknown", "hi")
        assert ok is False
        await g.stop()

    asyncio.run(_run())


def test_channel_message_default_fields():
    """ChannelMessage 默认值 (images=[], reply_to=None, raw={})."""
    m = ChannelMessage(channel="feishu", user_id="u1", text="x")
    assert m.images == []
    assert m.reply_to is None
    assert m.raw == {}
