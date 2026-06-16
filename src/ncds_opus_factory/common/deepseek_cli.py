"""共享 DeepSeek CLI helper：OpenAI 兼容协议的 chat completion，单点实现。

原实现散在 ``server/pipeline_runner._call_deepseek_for_rw``（rw 改写候选）。鬼谷子选题
也要调 DeepSeek，按「同一第三方接口只允许一处实现」收口到 common：pipeline_runner 与
guiguzi 都 import 本函数，避免两套 HTTP 调用同构代码漂移。

需 ``DEEPSEEK_API_KEY``（.env）。``thinking.enabled + reasoning_effort=high`` 吃 reasoner 模型。
"""

from __future__ import annotations

import json
import os

DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"


def call_deepseek(
    user_prompt: str,
    *,
    system_prompt: str = "",
    model: str = DEFAULT_DEEPSEEK_MODEL,
    timeout_seconds: float = 900.0,
) -> str:
    """走 DeepSeek HTTP API（OpenAI 兼容协议），返回首个 choice 的文本。

    沿用远程 runDeepSeekChat 口径：thinking.enabled=true + reasoning_effort=high。
    ``DEEPSEEK_API_KEY`` 未设 / 4xx / 空 content 均抛 RuntimeError（失败即抛）。
    """
    import httpx

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY missing")

    messages: list[dict[str, str]] = []
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": user_prompt})

    body = {
        "model": model,
        "messages": messages,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    try:
        # DeepSeek 是域内 API：trust_env=False 显式绕过环境里的 SOCKS/HTTP 代理（如 10808），
        # 既不依赖 httpx 的 socksio 扩展，也避免域内流量被绕到国外出口（慢/被拒）。
        with httpx.Client(trust_env=False, timeout=timeout_seconds) as client:
            resp = client.post(
                "https://api.deepseek.com/chat/completions",
                json=body,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"deepseek HTTP error: {exc}") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"deepseek http {resp.status_code}: {resp.text[:500]}")
    try:
        payload = resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"deepseek non-json response: {resp.text[:500]}") from exc

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"deepseek empty choices; payload tail={resp.text[-300:]}")
    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        # 极端情况看 reasoning_content
        reasoning = (message.get("reasoning_content") or "").strip()
        if not reasoning:
            raise RuntimeError(f"deepseek returned empty content; message keys={list(message.keys())}")
        return reasoning
    return content
