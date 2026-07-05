"""ASR 能力：Bcut 原始听写 + 听悟 fallback + 内部清洗后处理。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from . import bcut, tingwu
from ._base import ProgressFn, noop, read_dashscope_key


def transcribe(
    video_path: Path,
    on_progress: ProgressFn = noop,
    *,
    engine: str | None = None,
    language: str | None = None,
) -> tuple[dict | None, str]:
    """转写视频音轨，返回 (raw_response, raw_text)。

    默认 engine=bcut：先用必剪/Bcut 云端识别，失败后回退听悟/Paraformer。
    engine=tingwu 只使用听悟/Paraformer；engine=whisper 使用本地 Whisper 原文转写。
    """
    resolved = (engine or os.getenv("NOF_ASR_ENGINE") or "bcut").strip().lower()
    if resolved in {"whisper", "openai-whisper", "openai_whisper"}:
        raw, text = _transcribe_whisper(video_path, on_progress, language=language)
        if _has_text(text):
            return raw, text
        if raw is not None and raw.get("empty") is True:
            on_progress("Whisper ASR 空稿,尝试 Bcut/听悟 fallback...")
            fallback_raw, fallback_text = _transcribe_bcut_with_tingwu_fallback(video_path, on_progress)
            return _merge_fallback_empty(raw, fallback_raw, fallback_text, fallback_from="whisper")
        return raw, text
    if resolved in {"tingwu", "dashscope", "paraformer"}:
        return _transcribe_tingwu(video_path, on_progress)
    return _transcribe_bcut_with_tingwu_fallback(video_path, on_progress)


def _has_text(text: str | None) -> bool:
    return bool((text or "").strip())


def _empty_result(backend: str, reason: str, *, raw_response: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "backend": backend,
        "empty": True,
        "emptyReason": reason,
        "rawResponse": raw_response or {},
    }


def _is_empty_transcript_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "without transcript text" in msg or "empty transcript" in msg or "no transcript text" in msg


def _merge_fallback_empty(
    primary_raw: dict[str, Any] | None,
    fallback_raw: dict[str, Any] | None,
    fallback_text: str,
    *,
    fallback_from: str,
) -> tuple[dict | None, str]:
    if fallback_raw is None:
        return None, ""
    fallback_raw["fallbackFrom"] = fallback_from
    if _has_text(fallback_text):
        fallback_raw.setdefault("fallbackReason", "primary ASR returned empty transcript")
        return fallback_raw, fallback_text
    fallback_raw["empty"] = True
    if primary_raw is not None:
        fallback_raw["primaryEmpty"] = primary_raw
    fallback_raw.setdefault("fallbackReason", "primary ASR returned empty transcript")
    return fallback_raw, ""


def _transcribe_bcut_with_tingwu_fallback(video_path: Path, on_progress: ProgressFn = noop) -> tuple[dict | None, str]:
    """先调 Bcut；Bcut 不可用时保留现有听悟链路作为 fallback。"""
    try:
        on_progress("Bcut ASR 听写中...")
        result = bcut.transcribe_file(video_path, on_progress=on_progress)
    except Exception as exc:  # noqa: BLE001 - Bcut 是非公开接口，失败后交给听悟兜底
        if _is_empty_transcript_error(exc):
            reason = f"Bcut ASR 空稿: {type(exc).__name__}: {exc}"
        else:
            reason = f"Bcut ASR 失败: {type(exc).__name__}: {exc}"
        on_progress(reason)
        fallback_raw, fallback_text = _transcribe_tingwu(video_path, on_progress)
        if fallback_raw is not None:
            fallback_raw["fallbackFrom"] = "bcut"
            fallback_raw["fallbackReason"] = reason
        return fallback_raw, fallback_text

    if not _has_text(result.text):
        reason = "Bcut ASR 空稿"
        on_progress(reason)
        fallback_raw, fallback_text = _transcribe_tingwu(video_path, on_progress)
        return _merge_fallback_empty(_empty_result("bcut", reason), fallback_raw, fallback_text, fallback_from="bcut")

    result_dict = {
        "backend": result.backend,
        "model": result.model,
        "taskId": result.task_id,
        "rawResponse": result.raw_response,
    }
    return json.loads(json.dumps(result_dict, ensure_ascii=False)), result.text


def _transcribe_tingwu(video_path: Path, on_progress: ProgressFn = noop) -> tuple[dict | None, str]:
    """调用唯一听悟 adapter，返回 (raw_response, raw_text)。"""
    try:
        on_progress("听悟 ASR 听写中...")
        result = tingwu.transcribe_file(video_path)
    except Exception as exc:  # noqa: BLE001 - 单条采集失败由上层降级/记录
        if _is_empty_transcript_error(exc):
            on_progress("听悟 ASR 空稿")
            return _empty_result("tingwu", f"{type(exc).__name__}: {exc}"), ""
        on_progress(f"听悟 ASR 失败: {type(exc).__name__}")
        return None, ""
    if not _has_text(result.text):
        on_progress("听悟 ASR 空稿")
        return _empty_result("tingwu", "empty transcript", raw_response=result.raw_response), ""
    result_dict = {
        "backend": result.backend,
        "model": result.model,
        "dataId": result.data_id,
        "rawResponse": result.raw_response,
    }
    return json.loads(json.dumps(result_dict, ensure_ascii=False)), result.text


def _transcribe_whisper(
    video_path: Path,
    on_progress: ProgressFn = noop,
    *,
    language: str | None = None,
) -> tuple[dict | None, str]:
    """本地 Whisper 原文转写。task=transcribe，不做翻译。"""
    model_name = os.getenv("NOF_WHISPER_MODEL", "base").strip() or "base"
    try:
        import whisper

        on_progress(f"Whisper ASR 原文听写中({model_name})...")
        model = whisper.load_model(model_name)
        result = model.transcribe(
            str(video_path),
            language=language,
            task="transcribe",
            fp16=False,
            verbose=False,
            temperature=0,
            condition_on_previous_text=False,
            no_speech_threshold=0.8,
            logprob_threshold=-0.5,
            compression_ratio_threshold=2.4,
        )
    except Exception as exc:  # noqa: BLE001 - 单条采集失败由上层降级/记录
        on_progress(f"Whisper ASR 失败: {type(exc).__name__}")
        return None, ""

    text = str(result.get("text") or "").strip()
    raw = {
        "language": result.get("language"),
        "text": text,
        "segments": [
            {
                "start": seg.get("start"),
                "end": seg.get("end"),
                "text": seg.get("text"),
                "avg_logprob": seg.get("avg_logprob"),
                "no_speech_prob": seg.get("no_speech_prob"),
                "compression_ratio": seg.get("compression_ratio"),
            }
            for seg in (result.get("segments") or [])
            if isinstance(seg, dict)
        ],
    }
    result_dict = {
        "backend": "whisper",
        "model": model_name,
        "rawResponse": raw,
    }
    if not text:
        result_dict["empty"] = True
        result_dict["emptyReason"] = "empty transcript"
    return json.loads(json.dumps(result_dict, ensure_ascii=False)), text


def clean_transcript(raw: str, on_progress: ProgressFn = noop) -> str | None:
    """听写稿清洗:保留原始语言,只做纠错/断句/标点。

    首选 qwen(同音错别字/语义断句的长尾规则穷举不了),失败/无 key 时回退本地规则兜底。
    如果模型把英文稿明显翻成中文,直接拒绝该结果,避免污染 asr.clean.txt。
    """
    text = (raw or "").strip()
    if len(text) < 20:
        return None
    cleaned = _clean_with_qwen(text, on_progress)
    if cleaned:
        if _dominant_script_changed(text, cleaned):
            on_progress("清洗结果疑似改变原始语言,已丢弃")
        else:
            return cleaned
    return _clean_local(text, on_progress)


def _dominant_script(text: str) -> str | None:
    latin = sum(1 for ch in text if ("A" <= ch <= "Z") or ("a" <= ch <= "z"))
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    if latin >= 3 and cjk == 0:
        return "latin"
    if cjk >= 3 and latin == 0:
        return "cjk"
    if latin >= 20 and latin >= cjk * 2:
        return "latin"
    if cjk >= 10 and cjk >= latin * 2:
        return "cjk"
    return None


def _dominant_script_changed(raw: str, cleaned: str) -> bool:
    raw_script = _dominant_script(raw)
    cleaned_script = _dominant_script(cleaned)
    return bool(raw_script and cleaned_script and raw_script != cleaned_script)


def _clean_with_qwen(raw: str, on_progress: ProgressFn = noop) -> str | None:
    """qwen 清洗校对:纠同音错别字、去口头语、按语义断句加标点;不得增删事实。"""
    key = read_dashscope_key()
    if not key:
        return None
    import requests

    prompt = (
        "下面是一份语音听写稿(ASR 原文)。请在保持原始语言的前提下清洗校对:"
        "纠正同音/近音错别字,去除口头语和无意义语气词,按语义断句、补全标点、适当分段。"
        "必须保留原文语言;英文就输出英文,中文就输出中文,中英混合就保持中英混合。"
        "严禁翻译,不得增删事实,不得改写表达风格,不得编造原文没有的内容。"
        "直接输出清洗后的正文,不要任何解释或前后缀。\n\n" + raw[:6000]
    )
    try:
        on_progress("听写稿清洗中(qwen)…")
        resp = requests.post(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "qwen-plus", "temperature": 0.2,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=(15, 120),
        )
        resp.raise_for_status()
        cleaned = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        if not cleaned:
            return None
        on_progress(f"清洗完成(qwen): {len(raw)} -> {len(cleaned)} 字")
        return cleaned
    except Exception as e:  # noqa: BLE001 — qwen 失败交给本地兜底
        on_progress(f"qwen 清洗失败,本地规则兜底: {type(e).__name__}")
        return None


def _clean_local(raw: str, on_progress: ProgressFn = noop) -> str | None:
    """本地规则兜底:去孤立语气词、规整空白与重复标点(无网络/无 key 时至少可读)。"""
    text = re.sub(r"(?:(?<=^)|(?<=[,，。!！?？、\s]))(呃+|嗯+|哎+|诶+)(?=[,，。!！?？、\s]|$)", "", raw)
    if _dominant_script(raw) == "latin":
        text = re.sub(r"[ \t]+", " ", text)
    else:
        text = re.sub(r"[ \t]+", "", text)
    text = re.sub(r"，{2,}", "，", text)
    text = re.sub(r"。{2,}", "。", text)
    cleaned = text.strip()
    if not cleaned or cleaned == raw.strip():
        return None
    on_progress(f"本地清洗(兜底): {len(raw)} -> {len(cleaned)} 字")
    return cleaned
