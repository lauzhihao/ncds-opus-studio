"""Shared ordered LLM fallback for JSON-producing pipeline steps."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Mapping

from ncds_opus_factory.server.pipeline_rw_helpers import (
    DEFAULT_OPUS_MODEL_ID,
    _ModelUnavailable,
    _build_codex_user_prompt,
    _call_agy_for_rw,
    _call_deepseek_for_rw,
    _call_opus_for_rw,
    _call_scodex_for_rw,
    _check_model_available,
)

logger = logging.getLogger(__name__)

JSON_MODEL_FALLBACKS: list[dict[str, str]] = [
    {"id": "agy", "label": "AGY", "runner": "agy", "model": "gemini-3.5-flash"},
    {"id": "ds", "label": "DeepSeek", "runner": "deepseek", "model": "deepseek-v4-pro"},
    {"id": "scodex", "label": "SCodex", "runner": "scodex", "model": "gpt-5.5-codex"},
    {"id": "opus", "label": "Opus", "runner": "opus", "model": DEFAULT_OPUS_MODEL_ID},
]


def parse_llm_json_text(raw: str) -> Any:
    """Parse LLM JSON output, tolerating fenced code blocks."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        inner = re.match(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$", cleaned)
        if inner:
            cleaned = inner.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"模型输出非法 JSON：{exc}；tail={cleaned[-300:]}") from exc


def structure_json_with_model_fallback(
    user_prompt: str,
    system_prompt: str,
    on_progress: Callable[[str], None],
    *,
    parse: Callable[[str], Any] = parse_llm_json_text,
    target_profile: str,
    start_progress: str,
    success_progress: str,
    failover_progress: str,
    final_error: str,
    log_context: str,
    max_parse_attempts: int = 1,
    retry_hint: str = "",
    callers: Mapping[str, Callable[[str, str, str], str]] | None = None,
) -> Any:
    """Call JSON-producing models in product order, with optional parse repair.

    Fallback order is AGY -> DeepSeek -> SCodex -> Opus. Launcher/model/parse
    failures are logged with stack and then the next model is tried. User-facing
    progress and final errors are supplied by the caller so panels can keep
    product language while logs retain the technical detail.
    """
    failures: list[str] = []
    for index, cand in enumerate(JSON_MODEL_FALLBACKS):
        model_key = cand["id"]
        prompt = user_prompt
        try:
            for attempt in range(1, max(1, max_parse_attempts) + 1):
                on_progress(start_progress if attempt == 1 else "输出格式需要修正，正在重试...")
                raw = _call_json_model(
                    cand,
                    prompt,
                    system_prompt,
                    target_profile=target_profile,
                    callers=callers,
                )
                try:
                    parsed = parse(raw)
                    on_progress(success_progress)
                    return parsed
                except (ValueError, RuntimeError) as exc:
                    if attempt >= max(1, max_parse_attempts):
                        raise
                    prompt = _retry_prompt(user_prompt, exc, retry_hint=retry_hint)
        except Exception as exc:  # noqa: BLE001 - fallback boundary, full stack goes to logs
            failures.append(f"{model_key}: {exc}")
            logger.exception(
                "[%s] model %s failed while producing JSON; falling back",
                log_context,
                model_key,
            )
            if index < len(JSON_MODEL_FALLBACKS) - 1:
                on_progress(failover_progress)

    logger.error("[%s] all fallback models failed: %s", log_context, " | ".join(failures))
    raise RuntimeError(final_error)


def _call_json_model(
    cand: dict[str, str],
    user_prompt: str,
    system_prompt: str,
    *,
    target_profile: str,
    callers: Mapping[str, Callable[[str, str, str], str]] | None = None,
) -> str:
    model_key = cand["id"]
    if callers is not None and model_key in callers:
        return callers[model_key](user_prompt, system_prompt, cand["model"])

    available, reason = _check_model_available(cand)
    if not available:
        raise _ModelUnavailable(reason)

    runner = cand["runner"]
    if runner == "agy":
        return _call_agy_for_rw(user_prompt, system_prompt, cand["model"])
    if runner == "deepseek":
        return _call_deepseek_for_rw(user_prompt, system_prompt, cand["model"])
    if runner == "scodex":
        combined = _build_codex_user_prompt(
            system_prompt=system_prompt,
            task_prompt=user_prompt,
            target_profile=target_profile,
            expect_json=True,
        )
        return _call_scodex_for_rw(combined, cand["model"])
    if runner == "opus":
        return _call_opus_for_rw(user_prompt, system_prompt, cand["model"])
    raise _ModelUnavailable(f"unknown runner: {runner}")


def _retry_prompt(user_prompt: str, exc: Exception, *, retry_hint: str) -> str:
    return (
        user_prompt
        + "\n\n[严格要求] 上一次输出无法解析（报错："
        + f"{exc}）。只输出一个合法的 JSON 对象，禁止任何代码块/解释/前后缀；"
        + '字符串值内的英文双引号必须转义为 \\", 反斜杠转义为 \\\\, 换行转义为 \\n。'
        + (("\n" + retry_hint) if retry_hint else "")
    )
