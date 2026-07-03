"""
W8 - 异常告警 + 邮件推送.

ponytail:
  - AlertDetector: 纯内存, 状态不持久化 (重启丢, 避免误报风暴)
  - Mailer: smtplib stdlib + 邮件队列
  - cooldown: 同 (user, kind) 5 分钟内只推 1 次, 避免刷屏
ceiling: 单进程, 无重试, 无 DLQ.
add when: 100 客户/天 → 加 redis 状态; 邮件失败重试 → 加 retry 队列.
"""
from __future__ import annotations

import logging
import smtplib
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from email.mime.text import MIMEText
from enum import Enum
from pathlib import Path
from typing import Iterator, List, Optional

log = logging.getLogger("hermes.cs.alert")


class AlertKind(str, Enum):
    ANGRY_BURST = "angry_burst"          # 1 桶 angry
    NEGATIVE_TREND = "negative_trend"    # 连续 3 桶负
    INTENT_HOT = "intent_hot"            # 短时大量同类意图 (留口子)


@dataclass(frozen=True)
class Alert:
    kind: AlertKind
    user_id: str
    channel: str
    score: float
    extra: str = ""


class AlertDetector:
    """内存异常检测, 喂情绪分数流, 返回 Alert 列表."""

    def __init__(self, *, angry_threshold: float = -0.7, neg_window: int = 3) -> None:
        self.angry_threshold = angry_threshold
        self.neg_window = neg_window
        # ponytail: per-(user, channel) deque, 简单 list 实现
        self._scores: dict[tuple, list[float]] = {}
        self._cooldowns: dict[tuple, float] = {}
        self._cooldown_sec = 300  # 5 分钟
        self._lock = threading.Lock()

    def _cooldown_active(self, key: tuple) -> bool:
        last = self._cooldowns.get(key, 0.0)
        return (time.time() - last) < self._cooldown_sec

    def feed(self, user_id: str, channel: str, score: float) -> List[Alert]:
        """喂一个分数, 返回本轮触发的 alerts (空 list = 正常)."""
        with self._lock:
            k = (user_id, channel)
            buf = self._scores.setdefault(k, [])
            buf.append(score)
            if len(buf) > 20:  # ponytail: cap deque, 内存保护
                buf.pop(0)
            alerts: list[Alert] = []
            # 1) angry burst
            if score <= self.angry_threshold:
                ak = (k, AlertKind.ANGRY_BURST)
                if not self._cooldown_active(ak):
                    alerts.append(Alert(AlertKind.ANGRY_BURST, user_id, channel, score))
                    self._cooldowns[ak] = time.time()
            # 2) 连续 N 桶负
            recent = buf[-self.neg_window:]
            if len(recent) == self.neg_window and all(s < 0 for s in recent):
                ak = (k, AlertKind.NEGATIVE_TREND)
                if not self._cooldown_active(ak):
                    alerts.append(Alert(AlertKind.NEGATIVE_TREND, user_id, channel, sum(recent) / len(recent), extra=f"{self.neg_window} buckets"))
                    self._cooldowns[ak] = time.time()
        return alerts


# ponytail: Mailer 接口最小, 真发邮件留口子, 默认 mock
class Mailer:
    def __init__(self, *, smtp_host: str = "localhost", smtp_port: int = 25,
                 username: Optional[str] = None, password: Optional[str] = None,
                 from_addr: str = "alerts@hermes.local") -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self._sent: list[tuple[str, str, str]] = []  # (to, subject, body)
        self._lock = threading.Lock()

    def _record(self, to: str, subject: str, body: str) -> None:
        with self._lock:
            self._sent.append((to, subject, body))

    @property
    def sent(self) -> list[tuple[str, str, str]]:
        with self._lock:
            return list(self._sent)

    def send(self, to: str, subject: str, body: str) -> bool:
        self._record(to, subject, body)
        # ponytail: 默认不真发, 只 record. 真发时解注释下面.
        # if not self.username:
        #     return self._send_plain(to, subject, body)
        # return self._send_auth(to, subject, body)
        log.info("mail recorded (mock): to=%s subj=%s", to, subject[:40])
        return True

    def _send_plain(self, to: str, subject: str, body: str) -> bool:
        msg = MIMEText(body, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = to
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as s:
                s.sendmail(self.from_addr, [to], msg.as_string())
            return True
        except Exception as e:
            log.error("smtp send failed: %s", e)
            return False


# ponytail: 告警日志落盘, 便于审计 + 历史回查
class AlertLog:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or (Path.home() / ".hermes" / "customer_service" / "alerts.db")
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
                """CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    kind TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    score REAL NOT NULL,
                    extra TEXT,
                    mailed INTEGER NOT NULL DEFAULT 0
                )"""
            )

    def record(self, a: Alert) -> int:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO alerts (ts, kind, user_id, channel, score, extra) VALUES (?,?,?,?,?,?)",
                (time.time(), a.kind.value, a.user_id, a.channel, a.score, a.extra),
            )
            return cur.lastrowid

    def mark_mailed(self, alert_id: int) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE alerts SET mailed=1 WHERE id=?", (alert_id,))

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        det = AlertDetector()
        m = Mailer()
        log_db = AlertLog(Path(d) / "a.db")
        # 1) angry burst
        alerts = det.feed("u1", "wecom", -0.9)
        assert len(alerts) == 1
        assert alerts[0].kind == AlertKind.ANGRY_BURST
        # 2) cooldown 同类不重推
        alerts2 = det.feed("u1", "wecom", -0.95)
        assert alerts2 == []
        # 3) 连续 3 桶负
        det2 = AlertDetector()
        a1 = det2.feed("u2", "wecom", -0.4)
        a2 = det2.feed("u2", "wecom", -0.5)
        a3 = det2.feed("u2", "wecom", -0.6)
        # 第 1 个 trigger angry? -0.4 > -0.7 不 trigger
        # 第 3 个进入 neg_window=[-0.4, -0.5, -0.6] 全部 < 0 → trigger
        kinds = [a.kind for batch in (a1, a2, a3) for a in batch]
        assert AlertKind.NEGATIVE_TREND in kinds
        # 4) 邮件 + 落盘
        for a in alerts:
            aid = log_db.record(a)
            assert m.send("sup@hermes.local", f"[{a.kind.value}] {a.user_id}", f"score={a.score}")
            log_db.mark_mailed(aid)
        assert len(m.sent) == 1
        assert log_db.recent()[0]["mailed"] == 1
        print(f"OK: alert detector + mailer + log 跑通 (邮件 {len(m.sent)} 封, alerts {len(log_db.recent())} 条)")
