"""
永久测试 - 情绪评估 + 聚合 + 曲线.

ponytail: 9 个测试覆盖 evaluate/label/record/curve.
"""
from __future__ import annotations

import pytest

from sentiment import (
    Sentiment,
    SentimentStore,
    _bucket_key,
    evaluate,
    label,
)


# ---------- 评估函数 ----------

def test_evaluate_positive_strong():
    assert evaluate("太棒了!") == 1.0
    # ponytail: 多命中累加, score=2.0 被 clip 到 1.0
    assert evaluate("感谢感谢") == 1.0
    assert evaluate("完美") == 1.0


def test_evaluate_positive_light():
    assert evaluate("不错") == 0.5
    assert evaluate("还好") == 0.5


def test_evaluate_negative_strong():
    assert evaluate("你们是骗子!") == -1.0
    assert evaluate("垃圾") == -1.0


def test_evaluate_negative_light():
    assert evaluate("不满") == -0.5
    assert evaluate("慢") == -0.5


def test_evaluate_mixed_cancels():
    """正负抵消, 接近 0."""
    score = evaluate("骗子, 但是太棒了")
    assert -0.5 < score < 0.5  # -1+1=0


def test_evaluate_empty_returns_zero():
    assert evaluate("") == 0.0
    assert evaluate("   ") == 0.0


def test_evaluate_clamps_to_range():
    """大量正面或负面, 不会超过 +/-1.0."""
    assert evaluate("太棒了! 太棒了! 太棒了!") == 1.0
    assert evaluate("垃圾! 骗子! 气死!") == -1.0


# ---------- label ----------

def test_label_thresholds():
    assert label(0.5) == Sentiment.POSITIVE
    assert label(0.31) == Sentiment.POSITIVE  # > 0.3
    assert label(0.3) == Sentiment.NEUTRAL    # == 0.3 不算 positive
    assert label(0.2) == Sentiment.NEUTRAL
    assert label(0.0) == Sentiment.NEUTRAL
    assert label(-0.5) == Sentiment.NEGATIVE
    assert label(-0.7) == Sentiment.NEGATIVE  # -0.7 是 negative, 不是 angry
    assert label(-0.71) == Sentiment.ANGRY
    assert label(-1.0) == Sentiment.ANGRY


# ---------- 桶 key ----------

def test_bucket_key_5s_alignment():
    """桶起始时间 5 秒对齐."""
    assert _bucket_key(0) == 0
    assert _bucket_key(3) == 0
    assert _bucket_key(5) == 5
    assert _bucket_key(7) == 5
    assert _bucket_key(10) == 10
    assert _bucket_key(100.7) == 100


# ---------- 聚合 + 曲线 ----------

@pytest.fixture
def store(tmp_path) -> SentimentStore:
    return SentimentStore(tmp_path / "s.db")


def test_record_merges_same_bucket(store):
    """同一 5 秒桶合并: count=2, avg 加权平均."""
    p1 = store.record("wecom", "u1", "骗子", ts=1000.0)
    p2 = store.record("wecom", "u1", "垃圾", ts=1001.0)
    assert p1.count == 1
    assert p2.count == 2
    assert p2.avg_score == -1.0  # 两负面都 -1.0


def test_record_different_buckets(store):
    """不同桶不合并."""
    store.record("wecom", "u1", "骗子", ts=1000.0)
    store.record("wecom", "u1", "太棒了", ts=1010.0)  # 跨桶
    curve = store.curve("u1")
    assert len(curve) == 2
    assert curve[0].avg_score == -1.0
    assert curve[1].avg_score == 1.0


def test_curve_filters_by_channel(store):
    store.record("wecom", "u1", "骗子", ts=1000.0)
    store.record("feishu", "u1", "骗子", ts=1000.0)
    wecom = store.curve("u1", channel="wecom")
    feishu = store.curve("u1", channel="feishu")
    assert len(wecom) == 1
    assert len(feishu) == 1
    assert wecom[0].channel == "wecom"
    assert feishu[0].channel == "feishu"


def test_curve_filters_by_since_ts(store):
    store.record("wecom", "u1", "x", ts=1000.0)
    store.record("wecom", "u1", "x", ts=2000.0)
    after_1500 = store.curve("u1", since_ts=1500.0)
    assert len(after_1500) == 1
    assert after_1500[0].bucket_ts == 2000.0


def test_curve_isolated_by_user(store):
    store.record("wecom", "u1", "骗子", ts=1000.0)
    store.record("wecom", "u2", "太棒了", ts=1000.0)
    u1 = store.curve("u1")
    u2 = store.curve("u2")
    assert u1[0].avg_score == -1.0
    assert u2[0].avg_score == 1.0
