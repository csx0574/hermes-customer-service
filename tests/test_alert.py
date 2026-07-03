"""
永久测试 - 告警 + 邮件 + 落盘.
ponytail: 11 测试覆盖 detector / mailer / log.
"""
from __future__ import annotations

import time

import pytest

from alert import AlertDetector, AlertKind, AlertLog, Mailer


# ---------- AlertDetector ----------

def test_angry_burst_triggers_once():
    d = AlertDetector()
    a = d.feed("u1", "wecom", -0.9)
    assert len(a) == 1
    assert a[0].kind == AlertKind.ANGRY_BURST
    assert a[0].user_id == "u1"


def test_angry_cooldown_suppresses_duplicate():
    d = AlertDetector()
    d.feed("u1", "wecom", -0.9)  # 第 1 次 trigger
    a2 = d.feed("u1", "wecom", -0.95)  # cooldown 内, 不 trigger
    assert a2 == []


def test_angry_cooldown_isolated_by_user():
    d = AlertDetector()
    d.feed("u1", "wecom", -0.9)
    a = d.feed("u2", "wecom", -0.9)
    assert len(a) == 1  # 不同 user, 不在 cooldown


def test_negative_trend_triggers_at_3rd_bucket():
    d = AlertDetector()
    a1 = d.feed("u1", "wecom", -0.4)  # 单独负, 不 trigger neg trend
    a2 = d.feed("u1", "wecom", -0.5)
    a3 = d.feed("u1", "wecom", -0.6)
    # 第 3 个 feed 完成后 buf = [-0.4, -0.5, -0.6] 全部 < 0
    all_kinds = [k for batch in (a1, a2, a3) for a in batch for k in [a.kind]]
    assert AlertKind.NEGATIVE_TREND in all_kinds


def test_positive_scores_no_alert():
    d = AlertDetector()
    for s in (0.5, 0.8, 1.0):
        assert d.feed("u1", "wecom", s) == []


def test_mixed_breaks_trend():
    d = AlertDetector()
    d.feed("u1", "wecom", -0.4)
    d.feed("u1", "wecom", -0.5)
    a3 = d.feed("u1", "wecom", 0.5)  # 正面打破连续
    assert a3 == []  # 连续窗口被破坏


# ---------- Mailer (mock) ----------

def test_mailer_records_send():
    m = Mailer()
    assert m.send("a@b.com", "subj", "body") is True
    assert len(m.sent) == 1
    assert m.sent[0] == ("a@b.com", "subj", "body")


def test_mailer_thread_safe():
    import threading
    m = Mailer()
    errs = []

    def w() -> None:
        try:
            for i in range(50):
                m.send(f"u{i}@b.com", "s", "b")
        except Exception as e:
            errs.append(e)

    threads = [threading.Thread(target=w) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errs == []
    assert len(m.sent) == 250


# ---------- AlertLog ----------

@pytest.fixture
def log_db(tmp_path):
    return AlertLog(tmp_path / "a.db")


def test_log_records_alert(log_db):
    from alert import Alert
    a = Alert(AlertKind.ANGRY_BURST, "u1", "wecom", -0.9, "burst")
    aid = log_db.record(a)
    assert aid > 0
    rec = log_db.recent()[0]
    assert rec["kind"] == "angry_burst"
    assert rec["mailed"] == 0


def test_log_mark_mailed(log_db):
    from alert import Alert
    a = Alert(AlertKind.NEGATIVE_TREND, "u2", "feishu", -0.5)
    aid = log_db.record(a)
    log_db.mark_mailed(aid)
    assert log_db.recent()[0]["mailed"] == 1


def test_log_recent_limit(log_db):
    from alert import Alert
    for i in range(20):
        log_db.record(Alert(AlertKind.ANGRY_BURST, f"u{i}", "wecom", -0.9))
    assert len(log_db.recent(limit=5)) == 5
