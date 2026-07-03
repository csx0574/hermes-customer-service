"""
永久测试 - 意图识别.

ponytail: 7 个测试覆盖 5 类 + 长尾 + 负面情绪 + 转人工阈值 + LLM 兑底 mock.
"""
from __future__ import annotations

import pytest

from intent_classifier import (
    HandoffTracker,
    Intent,
    IntentResult,
    classify,
)


# ---------- 规则层 ----------

def test_classify_refund():
    r = classify("我要退款, 订单 #12345")
    assert r.intent == Intent.REFUND
    assert r.confidence >= 0.9
    assert r.rule_matched is True


def test_classify_complaint():
    r = classify("你们这服务态度太差了, 投诉!")
    assert r.intent == Intent.COMPLAINT
    assert r.rule_matched is True


def test_classify_aftersales():
    r = classify("产品坏了, 帮我换货")
    assert r.intent == Intent.AFTERSALES
    assert r.rule_matched is True


def test_classify_consult():
    r = classify("请问这个怎么用")
    assert r.intent == Intent.CONSULT
    assert r.rule_matched is True


def test_classify_praise():
    r = classify("谢谢客服小姐姐")
    assert r.intent == Intent.PRAISE
    assert r.rule_matched is True


# ---------- 长尾 + LLM 兑底 ----------

def test_classify_unknown_uses_llm_fallback():
    """长尾走 LLM — 配 mock 兑底, 验证调用过."""
    called = []

    def fake_llm(text: str):
        called.append(text)
        return (Intent.CONSULT, 0.7)

    # 任意无规则词文本 → 走 LLM
    r = classify("emm 那啥", llm_fallback=fake_llm)
    assert r.intent == Intent.CONSULT
    assert r.confidence == 0.7
    assert r.rule_matched is False
    assert called == ["emm 那啥"]


def test_classify_empty_text():
    r = classify("")
    assert r.intent == Intent.UNKNOWN
    assert r.confidence == 0.0
    assert r.should_handoff is False


# ---------- 转人工决策 ----------

def test_low_confidence_triggers_handoff():
    """LLM 兑底返回 low conf → 转人工."""
    def weak_llm(text: str):
        return (Intent.UNKNOWN, 0.3)

    r = classify("emmm 那啥", llm_fallback=weak_llm)
    assert r.confidence < 0.6
    assert r.should_handoff is True


def test_strong_negative_triggers_handoff():
    """强负面情绪 → 转人工, 即使 intent 命中规则."""
    r = classify("你们是骗子! 垃圾! 投诉! 强烈不满!")
    # intent 命中 COMPLAINT, 负面分 >= 0.7
    assert r.intent == Intent.COMPLAINT
    assert r.should_handoff is True


def test_polite_message_no_handoff():
    """普通咨询 → 不转人工."""
    r = classify("请问发货时间")
    assert r.should_handoff is False


def test_llm_fallback_failure_no_crash():
    """LLM 挂了不能崩, 走兜底 (UNKNOWN, 0.0)."""

    def bad_llm(text: str):
        raise RuntimeError("API timeout")

    r = classify("emmm", llm_fallback=bad_llm)
    assert r.intent == Intent.UNKNOWN
    assert r.confidence == 0.0
    assert r.should_handoff is True  # conf=0 < 0.6


# ---------- HandoffTracker ----------

def test_handoff_tracker_fires_after_window():
    tr = HandoffTracker(window=2, threshold=0.6)
    assert tr.observe(0.5) is False  # streak=1
    assert tr.observe(0.4) is True   # streak=2 → 触发
    assert tr.observe(0.3) is True   # streak=3 → 继续触发


def test_handoff_tracker_resets_on_high_conf():
    tr = HandoffTracker(window=2, threshold=0.6)
    tr.observe(0.4)  # streak=1
    tr.observe(0.9)  # streak=0
    assert tr.observe(0.4) is False  # streak=1, 不触发


def test_handoff_tracker_reset():
    tr = HandoffTracker(window=2, threshold=0.6)
    tr.observe(0.4)
    tr.observe(0.4)
    tr.reset()
    assert tr._low_streak == 0
    assert tr.observe(0.4) is False
