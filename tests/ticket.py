"""
Hermes 智能客服 - 工单 (W5 任务)

ponytail: 工单 = 状态机 (NEW/OPEN/PENDING/CLOSED), 字段 6 个, 一个 sqlite 表.
转人工上下文 dump 直接调 MessageStore.recent_context, 不重写.
ceiling: 单进程, 无 worker 抢单, 无 SLA 计时.
add when: 多坐席协同 → 加 worker 池 + Redis Stream; SLA 报告 → 加定时 cron.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterator, List, Optional

log = logging.getLogger("hermes.cs.ticket")


class TicketStatus(str, Enum):
    NEW = "new"           # 刚创建, 未分配
    OPEN = "open"         # 已分配给坐席/AI
    PENDING = "pending"   # 等待用户回复
    CLOSED = "closed"     # 关闭


@dataclass
class Ticket:
    id: str
    user_id: str
    channel: str
    intent: str
    status: TicketStatus = TicketStatus.NEW
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    history_dump: str = ""  # 转人工时 dump 的最近上下文

    def to_row(self) -> tuple:
        return (
            self.id, self.user_id, self.channel, self.intent,
            self.status.value, self.created_at, self.updated_at,
            self.history_dump,
        )


class TicketStore:
    """工单状态机 + 持久化."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or (Path.home() / ".hermes" / "customer_service" / "tickets.db")
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
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    history_dump TEXT
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_user ON tickets (user_id, status)")

    def create(self, user_id: str, channel: str, intent: str, history_dump: str = "") -> Ticket:
        t = Ticket(
            id=uuid.uuid4().hex[:12],
            user_id=user_id,
            channel=channel,
            intent=intent,
            history_dump=history_dump,
        )
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?)",
                t.to_row(),
            )
        log.info("ticket created: %s %s/%s", t.id, channel, intent)
        return t

    def get(self, ticket_id: str) -> Optional[Ticket]:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        return self._row_to_ticket(row) if row else None

    def update_status(self, ticket_id: str, new_status: TicketStatus) -> bool:
        """状态机: NEW→OPEN→PENDING→CLOSED, 任意状态 → CLOSED.
        不允许 CLOSED → 其它 (要 reopen 用 reopen()).
        """
        t = self.get(ticket_id)
        if t is None:
            return False
        if t.status == TicketStatus.CLOSED and new_status != TicketStatus.CLOSED:
            log.warning("refuse to reopen via update_status: %s", ticket_id)
            return False
        with self._lock, self._conn() as c:
            c.execute(
                "UPDATE tickets SET status=?, updated_at=? WHERE id=?",
                (new_status.value, time.time(), ticket_id),
            )
        return True

    def reopen(self, ticket_id: str) -> bool:
        """CLOSED 工单重新打开 — 同用户再次来问.
        ponytail: 直接 UPDATE, 绕过 update_status 的 CLOSED 限制.
        """
        t = self.get(ticket_id)
        if t is None or t.status != TicketStatus.CLOSED:
            return False
        with self._lock, self._conn() as c:
            c.execute(
                "UPDATE tickets SET status=?, updated_at=? WHERE id=?",
                (TicketStatus.OPEN.value, time.time(), ticket_id),
            )
        log.info("ticket reopened: %s", ticket_id)
        return True

    def list_by_user(self, user_id: str, status: Optional[TicketStatus] = None) -> List[Ticket]:
        if status:
            sql = "SELECT * FROM tickets WHERE user_id=? AND status=? ORDER BY updated_at DESC"
            params = (user_id, status.value)
        else:
            sql = "SELECT * FROM tickets WHERE user_id=? ORDER BY updated_at DESC"
            params = (user_id,)
        with self._lock, self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [self._row_to_ticket(r) for r in rows]

    def find_open_for_user(self, user_id: str) -> Optional[Ticket]:
        """查用户当前 OPEN/PENDING 工单, 转人工时复用."""
        for s in (TicketStatus.OPEN, TicketStatus.PENDING, TicketStatus.NEW):
            lst = self.list_by_user(user_id, s)
            if lst:
                return lst[0]
        return None

    def _row_to_ticket(self, row: sqlite3.Row) -> Ticket:
        return Ticket(
            id=row["id"],
            user_id=row["user_id"],
            channel=row["channel"],
            intent=row["intent"],
            status=TicketStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            history_dump=row["history_dump"] or "",
        )


if __name__ == "__main__":
    # 自检: 创建→转 OPEN→PENDING→CLOSED→试图 reopen via update_status (拒绝)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        s = TicketStore(Path(d) / "t.db")
        t = s.create("u1", "wecom", "refund", history_dump="<dump>")
        assert t.status == TicketStatus.NEW
        assert s.update_status(t.id, TicketStatus.OPEN)
        assert s.update_status(t.id, TicketStatus.PENDING)
        assert s.update_status(t.id, TicketStatus.CLOSED)
        # CLOSED 后 update_status 不能改
        assert not s.update_status(t.id, TicketStatus.OPEN)
        # 但 reopen() 可以
        assert s.reopen(t.id)
        assert s.get(t.id).status == TicketStatus.OPEN
        # find_open_for_user
        assert s.find_open_for_user("u1").id == t.id
        # 没 OPEN 用户的返回 None
        s.update_status(t.id, TicketStatus.CLOSED)
        assert s.find_open_for_user("u1") is None
        print(f"OK: ticket {t.id} lifecycle 跑通 (NEW→OPEN→PENDING→CLOSED→reopen)")
