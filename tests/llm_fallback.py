"""
W10 - 真实 LLM 接入 (DeepSeek / 智谱, 任选一).

ponytail: stdlib urllib.request, 不引 openai SDK.
  - DeepSeek 兼容 OpenAI 协议, base_url=https://api.deepseek.com/v1/chat/completions
  - 智谱 OpenAI 兼容, base_url=https://open.bigmodel.cn/api/paas/v4/chat/completions
  - 两者都吃 {"model":..., "messages":[...]} + Bearer token.
  - 输出 JSON: {intent, confidence} — 用 system prompt 强约束.
ceiling: 同步阻塞调用, 无流式, 无 retry, 无 token 计数.
add when: 100+ QPS → 切 httpx + asyncio; token 限额 → 加 retry/backoff.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Tuple

from intent_classifier import Intent

log = logging.getLogger("hermes.cs.llm")

# ponytail: 2 个 provider 共享 OpenAI 协议, 一个函数搞定
_PROVIDERS = {
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "env": "DEEPSEEK_API_KEY",
    },
    "zhipu": {
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4-flash",
        "env": "ZHIPU_API_KEY",
    },
}

_SYSTEM_PROMPT = """你是客服意图分类器. 输入用户消息, 严格输出 JSON:
{"intent": "consult|complaint|aftersales|refund|praise|unknown", "confidence": 0.0~1.0}
只输出 JSON, 不解释, 不 markdown, 不多余字符."""


def make_llm_fallback(provider: str = "deepseek", timeout: int = 10):
    """工厂: 返回一个 LlmFallbackFn. 失败时降级 UNKNOWN+0.0 (触发转人工).
    ponytail: 闭包捕获 provider 配置, 调用方无感.
    """
    cfg = _PROVIDERS[provider]
    api_key = os.environ.get(cfg["env"], "")
    if not api_key:
        log.warning("[%s] %s 未设置, 工厂仍创建, 调用时会降级", provider, cfg["env"])

    def _call(text: str) -> Tuple[Intent, float]:
        if not api_key:
            return (Intent.UNKNOWN, 0.0)
        body = json.dumps({
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.0,
            "max_tokens": 50,
        }).encode("utf-8")
        req = urllib.request.Request(
            cfg["url"],
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode("utf-8"))
            content = resp["choices"][0]["message"]["content"].strip()
            # ponytail: 容错 — LLM 可能包 ```json, 剥掉
            content = content.strip("`").strip()
            if content.startswith("json"):
                content = content[4:].strip()
            data = json.loads(content)
            intent_str = data.get("intent", "unknown")
            conf = float(data.get("confidence", 0.0))
            try:
                intent = Intent(intent_str)
            except ValueError:
                intent = Intent.UNKNOWN
            return (intent, max(0.0, min(1.0, conf)))
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, json.JSONDecodeError) as e:
            log.error("LLM call failed [%s]: %s", provider, e)
            return (Intent.UNKNOWN, 0.0)

    return _call


# ponytail: 自检 — 不发真请求 (没 key), 测工厂 + 降级路径
if __name__ == "__main__":
    fn = make_llm_fallback("deepseek")
    # 无 key, 必须降级到 UNKNOWN+0.0
    intent, conf = fn("我要退款")
    assert intent == Intent.UNKNOWN
    assert conf == 0.0
    # 智谱也跑通
    fn2 = make_llm_fallback("zhipu")
    assert fn2("test") == (Intent.UNKNOWN, 0.0)
    # provider 错误
    try:
        make_llm_fallback("invalid")
        assert False, "应该抛 KeyError"
    except KeyError:
        pass
    print("OK: llm_fallback 工厂 + 降级路径跑通 (无 key, 必降级)")
