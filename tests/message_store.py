"""
Hermes 智能客服 - 持久记忆层 (W3 任务最小实现)

ponytail: 用 stdlib sqlite3, 不引入 chroma/qdrant. 一个表三列:
  - channel_messages (channel, user_id, text, ts)
理由: W3 任务 PRD 写的是"持久记忆 + 跨会话上下文", MVP 阶段单进程单机能
扛住 1000 用户 × 100 消息. 向量检索/语义召回 V1.1 再接.
ceiling: 单文件 SQLite, 无 FTS5, 无向量.
add when: (a) 单用户消息 > 1000, 或 (b) 需要语义检索 → 切 sqlite-vec / qdrant.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

from channel_gateway import ChannelMessage

log = logging.getLogger("hermes.cs.memory")

# 默认库路径 — 沿用 HERMES_HOME 约定, 找不到就 ~/.hermes
_DEFAULT_DB = Path.home() / ".hermes" / "customer_service" / "messages.db"


@dataclass(frozen=True)
class StoredMessage:
    channel: str
    user_id: str
    text: str
    ts: float


class MessageStore:
    """线程安全的本地消息存储."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    ts REAL NOT NULL
                )
                """
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_ts "
                "ON channel_messages (user_id, ts DESC)"
            )

    def save(self, msg: ChannelMessage) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO channel_messages (channel, user_id, text, ts) "
                "VALUES (?, ?, ?, ?)",
                (msg.channel, msg.user_id, msg.text, time.time()),
            )
            log.info("saved %s/%s: %s", msg.channel, msg.user_id, msg.text[:60])

    def history(self, user_id: str, limit: int = 10) -> List[StoredMessage]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT channel, user_id, text, ts FROM channel_messages "
                "WHERE user_id = ? ORDER BY ts DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [StoredMessage(r["channel"], r["user_id"], r["text"], r["ts"]) for r in rows]

    def recent_context(self, user_id: str, limit: int = 5) -> str:
        """拉最近 N 条拼成上下文串 — 给 LLM prompt 用."""
        msgs = list(reversed(self.history(user_id, limit)))
        if not msgs:
            return ""
        return "\n".join(f"[{m.channel}] {m.text}" for m in msgs)


if __name__ == "__main__":
    # ponytail: 自检 — 写 2 条, 拉 1 个 user 的 history, 验最近 5 条拼串
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        store = MessageStore(Path(d) / "test.db")
        store.save(ChannelMessage(channel="wecom", user_id="u1", text="我想退款"))
        store.save(ChannelMessage(channel="wecom", user_id="u1", text="订单 #12345"))
        store.save(ChannelMessage(channel="feishu", user_id="u2", text="查一下"))
        h1 = store.history("u1")
        h2 = store.history("u2")
        assert len(h1) == 2
        assert len(h2) == 1
        assert h1[0].text == "订单 #12345"  # 最新
        ctx = store.recent_context("u1")
        assert "我想退款" in ctx and "订单 #12345" in ctx
        assert h2[0].channel == "feishu"
        print(f"OK: stored 3, u1 history=2, u2 history=1, ctx len={len(ctx)}")
