"""
Hermes 智能客服 - 渠道网关最小骨架

ponytail: 不依赖 plugins/platforms/{feishu,wecom} 的 BasePlatformAdapter,
          自己定义 4 个方法抽象 (connect/send/receive/health).
          理由: BasePlatformAdapter 嵌入在 plugin 加载链里, 单独 import
          会拖入一坨 OpenClaw 状态. 后续接入时再 thin-wrapper.
          add when: 真实企微/飞书凭据就位, 准备替换 mock.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

log = logging.getLogger("hermes.cs.gateway")


@dataclass(frozen=True)
class ChannelMessage:
    """渠道无关的统一消息格式.

    所有渠道 (企微/飞书/钉钉/H5) 入站时必须转成这个, 然后才能进 Hermes context.
    """
    channel: str               # "wecom" | "feishu" | "h5" | ...
    user_id: str               # 渠道内唯一用户 ID
    text: str
    images: list[str] = field(default_factory=list)   # url 或 base64
    reply_to: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict) # 原始 payload 留痕


class ChannelAdapter(ABC):
    """最小 4 方法抽象 — 后续接企微/飞书时, 包一层 BasePlatformAdapter."""

    name: str = "abstract"

    @abstractmethod
    async def connect(self) -> bool: ...
    @abstractmethod
    async def disconnect(self) -> None: ...
    @abstractmethod
    async def send(self, user_id: str, text: str) -> bool: ...
    @abstractmethod
    async def health(self) -> dict[str, Any]: ...


class MockWecomAdapter(ChannelAdapter):
    """企微 mock — 跑测试用, 不接真实凭据.

    真实接入时替换为 plugins/platforms/wecom/adapter.py 的 WeComAdapter.
    """

    name = "wecom"

    def __init__(self) -> None:
        # ponytail: 之前是 class-level list, 测试间会污染. instance-level 更安全.
        self._sent: list[tuple[str, str]] = []

    async def connect(self) -> bool:
        log.info("wecom mock connected")
        return True

    async def disconnect(self) -> None:
        log.info("wecom mock disconnected")

    async def send(self, user_id: str, text: str) -> bool:
        self._sent.append((user_id, text))
        log.info("wecom mock send -> %s: %s", user_id, text[:60])
        return True

    async def health(self) -> dict[str, Any]:
        return {"channel": self.name, "status": "ok", "mock": True, "sent_count": len(self._sent)}


class MockFeishuAdapter(ChannelAdapter):
    """飞书 mock — 同上."""

    name = "feishu"

    def __init__(self) -> None:
        # ponytail: 同 wecom, instance-level 避免测试污染.
        self._sent: list[tuple[str, str]] = []

    async def connect(self) -> bool:
        log.info("feishu mock connected")
        return True

    async def disconnect(self) -> None:
        log.info("feishu mock disconnected")

    async def send(self, user_id: str, text: str) -> bool:
        self._sent.append((user_id, text))
        log.info("feishu mock send -> %s: %s", user_id, text[:60])
        return True

    async def health(self) -> dict[str, Any]:
        return {"channel": self.name, "status": "ok", "mock": True, "sent_count": len(self._sent)}


class Gateway:
    """多渠道路由 — 收到的 ChannelMessage 全部入 hermes context.

    ponytail: 当前只做 in-memory 路由. 真实接 LLM/记忆时再 thin-wrapper.
    """

    def __init__(self) -> None:
        self.adapters: dict[str, ChannelAdapter] = {}
        self.inbox: list[ChannelMessage] = []

    def register(self, adapter: ChannelAdapter) -> None:
        self.adapters[adapter.name] = adapter
        log.info("gateway registered: %s", adapter.name)

    async def start(self) -> None:
        for a in self.adapters.values():
            ok = await a.connect()
            if not ok:
                log.error("adapter connect failed: %s", a.name)

    async def stop(self) -> None:
        for a in self.adapters.values():
            await a.disconnect()

    async def ingest(self, msg: ChannelMessage) -> None:
        """入站钩子 — Hermes context engine 后续接这里."""
        self.inbox.append(msg)
        log.info("ingest %s from %s: %s", msg.channel, msg.user_id, msg.text[:60])

    async def reply(self, channel: str, user_id: str, text: str) -> bool:
        a = self.adapters.get(channel)
        if a is None:
            log.error("unknown channel: %s", channel)
            return False
        return await a.send(user_id, text)

    async def health(self) -> dict[str, Any]:
        return {name: await a.health() for name, a in self.adapters.items()}


async def build_default_gateway() -> Gateway:
    """默认 2 路: 企微 + 飞书 (mock)."""
    g = Gateway()
    g.register(MockWecomAdapter())
    g.register(MockFeishuAdapter())
    await g.start()
    return g


if __name__ == "__main__":
    # ponytail: 自检 — 不接 LLM, 只跑通道收/发
    async def _demo() -> None:
        g = await build_default_gateway()
        # 模拟 2 路入站
        await g.ingest(ChannelMessage(channel="wecom", user_id="u_w1", text="我想退款"))
        await g.ingest(ChannelMessage(channel="feishu", user_id="u_f1", text="订单查一下"))
        # 模拟回 2 路
        assert await g.reply("wecom", "u_w1", "请提供订单号")
        assert await g.reply("feishu", "u_f1", "您的订单已发货")
        # health
        h = await g.health()
        print("health:", h)
        assert h["wecom"]["status"] == "ok"
        assert h["feishu"]["status"] == "ok"
        assert len(g.inbox) == 2
        await g.stop()
        print("OK: gateway mock self-check passed")

    asyncio.run(_demo())
