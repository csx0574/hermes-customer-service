"""
永久测试 - 工单 Ticket + TicketStore.

ponytail: 11 个测试覆盖 CRUD + 状态机 + reopen 兜底.
"""
from __future__ import annotations

import pytest

from ticket import Ticket, TicketStatus, TicketStore


@pytest.fixture
def store(tmp_path) -> TicketStore:
    return TicketStore(tmp_path / "t.db")


# ---------- create / get ----------

def test_create_returns_ticket_with_defaults(store):
    t = store.create("u1", "wecom", "refund")
    assert t.user_id == "u1"
    assert t.channel == "wecom"
    assert t.intent == "refund"
    assert t.status == TicketStatus.NEW
    assert t.id  # uuid 非空
    assert isinstance(t, Ticket)


def test_create_persists(store):
    t = store.create("u1", "wecom", "refund", history_dump="<ctx>")
    loaded = store.get(t.id)
    assert loaded.id == t.id
    assert loaded.history_dump == "<ctx>"


def test_get_unknown_returns_none(store):
    assert store.get("nonexistent") is None


# ---------- 状态机 ----------

def test_lifecycle_new_open_pending_closed(store):
    t = store.create("u1", "wecom", "refund")
    assert store.update_status(t.id, TicketStatus.OPEN)
    assert store.get(t.id).status == TicketStatus.OPEN
    assert store.update_status(t.id, TicketStatus.PENDING)
    assert store.get(t.id).status == TicketStatus.PENDING
    assert store.update_status(t.id, TicketStatus.CLOSED)
    assert store.get(t.id).status == TicketStatus.CLOSED


def test_closed_cannot_be_reopened_via_update(store):
    """CLOSED 工单不能通过 update_status 退回 OPEN, 必须用 reopen()."""
    t = store.create("u1", "wecom", "refund")
    store.update_status(t.id, TicketStatus.OPEN)
    store.update_status(t.id, TicketStatus.CLOSED)
    assert not store.update_status(t.id, TicketStatus.OPEN)
    assert store.get(t.id).status == TicketStatus.CLOSED  # 没变


def test_reopen_works_only_for_closed(store):
    """reopen() 只接受 CLOSED 工单."""
    t = store.create("u1", "wecom", "refund")
    # NEW 不能 reopen
    assert not store.reopen(t.id)
    # OPEN 不能 reopen
    store.update_status(t.id, TicketStatus.OPEN)
    assert not store.reopen(t.id)
    # CLOSED 可以
    store.update_status(t.id, TicketStatus.CLOSED)
    assert store.reopen(t.id)
    assert store.get(t.id).status == TicketStatus.OPEN


def test_update_unknown_ticket_returns_false(store):
    """不存在的工单 update_status 返回 False."""
    assert store.update_status("ghost", TicketStatus.OPEN) is False


# ---------- 查询 ----------

def test_list_by_user(store):
    store.create("u1", "wecom", "refund")
    store.create("u1", "feishu", "complaint")
    store.create("u2", "wecom", "consult")
    u1_tickets = store.list_by_user("u1")
    assert len(u1_tickets) == 2
    assert all(t.user_id == "u1" for t in u1_tickets)


def test_list_by_user_filter_status(store):
    t1 = store.create("u1", "wecom", "refund")
    t2 = store.create("u1", "wecom", "consult")
    store.update_status(t1.id, TicketStatus.CLOSED)
    open_tickets = store.list_by_user("u1", TicketStatus.NEW)
    assert len(open_tickets) == 1
    assert open_tickets[0].id == t2.id


def test_find_open_for_user(store):
    """转人工时复用同用户的 OPEN 工单."""
    store.create("u1", "wecom", "refund")  # NEW
    t2 = store.create("u1", "wecom", "consult")
    store.update_status(t2.id, TicketStatus.OPEN)
    # 找到 OPEN (优先级 NEW < OPEN)
    found = store.find_open_for_user("u1")
    # 按 list_by_user DESC 时间排, 后建的在前面
    assert found.id == t2.id


def test_find_open_for_user_none(store):
    store.create("u1", "wecom", "refund")
    t = store.list_by_user("u1")[0]
    store.update_status(t.id, TicketStatus.CLOSED)
    assert store.find_open_for_user("u1") is None


def test_history_dump_persists(store):
    t = store.create("u1", "wecom", "refund", history_dump="[wecom] 你好\n[wecom] 退款")
    assert "[wecom] 你好" in t.history_dump
    assert "退款" in t.history_dump
