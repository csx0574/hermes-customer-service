"""
永久测试 - 持久记忆层.

ponytail: 用 tempfile 隔离, 4 个测试覆盖 save/history/recent_context/thread-safety.
"""
from __future__ import annotations

import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest

from channel_gateway import ChannelMessage
from message_store import MessageStore, StoredMessage


@pytest.fixture
def store(tmp_path) -> MessageStore:
    # ponytail: 每个测试 fresh DB, 避免相互污染
    return MessageStore(tmp_path / "test.db")


def test_save_and_history(store):
    """存 2 条, 能按 user_id 拉回来."""
    store.save(ChannelMessage(channel="wecom", user_id="u1", text="退款"))
    store.save(ChannelMessage(channel="wecom", user_id="u1", text="订单 123"))
    h = store.history("u1")
    assert len(h) == 2
    assert h[0].text == "订单 123"  # 最新在前
    assert h[1].text == "退款"
    assert all(isinstance(m, StoredMessage) for m in h)


def test_history_isolated_by_user(store):
    """不同 user_id 的消息不串."""
    store.save(ChannelMessage(channel="wecom", user_id="u1", text="a"))
    store.save(ChannelMessage(channel="feishu", user_id="u2", text="b"))
    assert len(store.history("u1")) == 1
    assert len(store.history("u2")) == 1
    assert store.history("u3") == []


def test_history_limit(store):
    """limit 生效."""
    for i in range(20):
        store.save(ChannelMessage(channel="wecom", user_id="u1", text=f"msg{i}"))
    h = store.history("u1", limit=5)
    assert len(h) == 5
    assert h[0].text == "msg19"  # 最新


def test_recent_context_format(store):
    """recent_context 返回按时间正序的拼串."""
    store.save(ChannelMessage(channel="wecom", user_id="u1", text="first"))
    store.save(ChannelMessage(channel="wecom", user_id="u1", text="second"))
    ctx = store.recent_context("u1")
    assert ctx.index("first") < ctx.index("second")  # 正序
    assert "[wecom]" in ctx


def test_recent_context_empty_user(store):
    """无历史用户返回空串."""
    assert store.recent_context("ghost") == ""


def test_thread_safety(tmp_path):
    """10 线程并发写 100 条, 总数必须 = 1000."""
    s = MessageStore(tmp_path / "concurrent.db")
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for i in range(100):
                s.save(ChannelMessage(channel="wecom", user_id=f"u{tid}", text=f"t{tid}-{i}"))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    # 每个 user 100 条 — 显式 limit=200 盖过默认 10
    for tid in range(10):
        assert len(s.history(f"u{tid}", limit=200)) == 100


def test_db_file_persists(tmp_path):
    """DB 文件落盘, 重新 open 能读到."""
    p = tmp_path / "persist.db"
    s1 = MessageStore(p)
    s1.save(ChannelMessage(channel="wecom", user_id="u1", text="persist"))
    s2 = MessageStore(p)
    assert len(s2.history("u1")) == 1
