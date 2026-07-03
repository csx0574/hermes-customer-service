"""
Hermes 智能客服 - 意图识别 (W4 任务)

ponytail: 5 类意图 (咨询/投诉/售后/退款/表扬), 规则层用 stdlib re.
LLM 兑底写函数签名 + 一个 mock 兑底, 不真调 — 留给主公填 API key.
转人工阈值: 连续 2 轮 confidence < 0.6 或 负面情绪 > 0.7.
ceiling: 规则层中文简体 only, 繁体/英文走 LLM.
add when: 客户上线后看不到 fallback → 加 LLM 实时调用.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Tuple

log = logging.getLogger("hermes.cs.intent")


class Intent(str, Enum):
    CONSULT = "consult"   # 咨询
    COMPLAINT = "complaint"  # 投诉
    AFTERSALES = "aftersales"  # 售后
    REFUND = "refund"    # 退款
    PRAISE = "praise"    # 表扬
    UNKNOWN = "unknown"  # 长尾


# 规则层 — 关键词 → 意图 + 基础 confidence
# ponytail: 简单关键词覆盖 70-80% 流量, LLM 兑底剩下的.
_RULES: List[Tuple[Intent, float, re.Pattern]] = [
    (Intent.REFUND, 0.9, re.compile(r"退(款|钱)|退货|不要了|申请退款|订单取消", re.I)),
    (Intent.COMPLAINT, 0.85, re.compile(r"投诉|差评|垃圾|骗子|坑|气死|失望|骗人|骗我", re.I)),
    (Intent.AFTERSALES, 0.8, re.compile(r"售后|维修|换货|保修|坏了|故障|不工作|不能用", re.I)),
    (Intent.CONSULT, 0.7, re.compile(r"怎么(用|操作)|如何|请问|咨询|想了解|有没有|多少钱|价格", re.I)),
    (Intent.PRAISE, 0.85, re.compile(r"谢谢|感谢|好评|太棒了|辛苦了|(?<![今明后昨每天])赞[!?。]?$", re.I)),
]

# 负面情绪词 — 转人工阈值用
_NEGATIVE_WORDS = re.compile(r"气死|垃圾|骗子|差评|投诉|骗人|坑|失望|生气|愤怒|投诉")


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    confidence: float   # 0~1
    rule_matched: bool  # True=规则层命中, False=LLM 兑底返回
    should_handoff: bool  # True=触发转人工


# LLM 兑底签名 — 主公填实际 API
LlmFallbackFn = Callable[[str], Tuple[Intent, float]]


def _mock_llm_fallback(text: str) -> Tuple[Intent, float]:
    """默认 mock — 当没填 LLM 兑底时使用, 永远返回 UNKNOWN+0.5.
    ponytail: 触发转人工, 主公一定能看到效果.
    """
    log.warning("LLM fallback not configured, returning UNKNOWN")
    return (Intent.UNKNOWN, 0.5)


def classify(
    text: str,
    *,
    llm_fallback: Optional[LlmFallbackFn] = None,
    handoff_threshold: float = 0.6,
    negative_threshold: float = 0.7,
) -> IntentResult:
    """单条消息分类.

    Returns:
        IntentResult with intent / confidence / handoff flag.
    """
    text = text.strip()
    if not text:
        return IntentResult(Intent.UNKNOWN, 0.0, False, False)

    # 1) 规则层
    best = (Intent.UNKNOWN, 0.0)
    for intent, base_conf, pat in _RULES:
        if pat.search(text):
            if base_conf > best[1]:
                best = (intent, base_conf)

    rule_matched = best[0] != Intent.UNKNOWN

    # 2) LLM 兑底
    if not rule_matched:
        fn = llm_fallback or _mock_llm_fallback
        try:
            intent, conf = fn(text)
        except Exception as e:  # ponytail: 信任边界 — LLM 挂了不能崩
            log.error("LLM fallback failed: %s", e)
            intent, conf = Intent.UNKNOWN, 0.0
        result_intent, result_conf = intent, conf
    else:
        result_intent, result_conf = best

    # 3) 转人工决策
    negative_score = _calc_negative_score(text)
    should_handoff = (
        result_conf < handoff_threshold
        or negative_score >= negative_threshold
    )

    return IntentResult(
        intent=result_intent,
        confidence=result_conf,
        rule_matched=rule_matched,
        should_handoff=should_handoff,
    )


def _calc_negative_score(text: str) -> float:
    """负面情绪粗算 — 命中关键词数 / 3, 上限 1.0.
    ponytail: 简化版, 实际情绪曲线 W6 才接 LLM 流式评估.
    """
    hits = len(_NEGATIVE_WORDS.findall(text))
    return min(hits / 3.0, 1.0)


# 连续 N 轮 confidence < threshold 触发转人工
class HandoffTracker:
    """追踪连续低 confidence 轮数, 触发转人工.

    用法: tracker = HandoffTracker(window=2, threshold=0.6)
           for r in turns: if tracker.observe(r.confidence): handoff()
    """

    def __init__(self, window: int = 2, threshold: float = 0.6) -> None:
        self.window = window
        self.threshold = threshold
        self._low_streak: int = 0

    def observe(self, confidence: float) -> bool:
        """记录一轮 confidence, 返回 True=本轮触发转人工."""
        if confidence < self.threshold:
            self._low_streak += 1
        else:
            self._low_streak = 0
        return self._low_streak >= self.window

    def reset(self) -> None:
        self._low_streak = 0


if __name__ == "__main__":
    # 自检 — 5 类各 1 + 长尾 1 + 负面情绪 1
    cases = [
        ("我要退款, 订单 #12345", Intent.REFUND, 0.9),
        ("你们这服务态度太差了, 投诉!", Intent.COMPLAINT, 0.85),
        ("产品坏了, 帮我换货", Intent.AFTERSALES, 0.8),
        ("请问这个怎么用", Intent.CONSULT, 0.7),
        ("谢谢客服小姐姐", Intent.PRAISE, 0.85),
        ("今天天气很好", Intent.UNKNOWN, 0.5),  # 长尾 — "很好" 已被排除
        ("你们是骗子! 垃圾!", Intent.COMPLAINT, 0.85),  # 强负面
    ]
    fail = 0
    for text, exp_intent, exp_conf in cases:
        r = classify(text)
        ok = r.intent == exp_intent
        if not ok:
            fail += 1
        print(
            f"{'OK ' if ok else 'FAIL'}  intent={r.intent.value:12s} "
            f"conf={r.confidence:.2f} handoff={r.should_handoff}  | {text}"
        )
    # HandoffTracker 演示
    tr = HandoffTracker(window=2, threshold=0.6)
    turns = [0.5, 0.4, 0.3, 0.8]  # 前两轮低 conf 应触发
    for i, c in enumerate(turns):
        fired = tr.observe(c)
        print(f"  turn {i} conf={c} streak={tr._low_streak} handoff={fired}")
    print(f"\n{len(cases) - fail}/{len(cases)} pass")
