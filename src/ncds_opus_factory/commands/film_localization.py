"""Translate only replaceable film narration while preserving timeline identity."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ncds_opus_core.common import cancel

from ncds_opus_factory.common.opus_cli import call_opus, is_opus_available

SUPPORTED_TARGET_LANGUAGES = {"en", "ja", "ko", "es", "fr", "de"}
_LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
}

ProgressFn = Callable[[str], None]
TranslationAgentFn = Callable[
    [list[dict[str, Any]], str],
    list[dict[str, Any]],
]
TRANSLATION_BATCH_SIZE = 40


def _noop(_text: str) -> None:
    return None


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("[")
        end = stripped.rfind("]")
        if start < 0 or end <= start:
            raise RuntimeError("film localization Agent returned invalid JSON")
        value = json.loads(stripped[start:end + 1])
    if not isinstance(value, list):
        raise RuntimeError("film localization Agent must return a JSON array")
    return [item for item in value if isinstance(item, dict)]


def _call_opus_translation_agent(
    segments: list[dict[str, Any]],
    target_language: str,
) -> list[dict[str, Any]]:
    if not is_opus_available():
        raise RuntimeError(
            "film localization Agent unavailable: opus launcher is not installed"
        )
    payload = [
        {
            "segment_id": segment["segment_key"],
            "source_text": segment["source_text"],
            "available_duration_ms": (
                int(segment.get("end_ms") or 0)
                - int(segment.get("start_ms") or 0)
            ),
        }
        for segment in segments
    ]
    prompt = "\n".join([
        "Translate film narration segments.",
        f"Target language: {_LANGUAGE_NAMES[target_language]} ({target_language}).",
        "Return only a JSON array of objects with segment_id and translated_text.",
        "Keep every segment exactly once. Do not merge, split, omit, summarize, or add facts.",
        "At normal narration speed, compress each translation to fit its "
        "available_duration_ms while preserving meaning.",
        json.dumps(payload, ensure_ascii=False),
    ])
    response = call_opus(prompt, timeout_seconds=1800)
    return _parse_json_array(response)


TRANSLATION_AGENT: TranslationAgentFn = _call_opus_translation_agent


def _translate_segments(
    segments: list[dict[str, Any]],
    target_language: str,
    on_progress: ProgressFn,
) -> dict[str, str]:
    translated: dict[str, str] = {}
    for offset in range(0, len(segments), TRANSLATION_BATCH_SIZE):
        cancel.checkpoint()
        batch = segments[offset:offset + TRANSLATION_BATCH_SIZE]
        batch_no = offset // TRANSLATION_BATCH_SIZE + 1
        batch_total = (
            len(segments) + TRANSLATION_BATCH_SIZE - 1
        ) // TRANSLATION_BATCH_SIZE
        on_progress(
            f"film localization batch {batch_no}/{batch_total}: "
            f"segments={len(batch)}"
        )
        rows = TRANSLATION_AGENT(batch, target_language)
        cancel.checkpoint()
        batch_translated: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            segment_id = str(row.get("segment_id") or "")
            translated_text = str(row.get("translated_text") or "").strip()
            if (
                segment_id
                and translated_text
                and segment_id not in batch_translated
            ):
                batch_translated[segment_id] = translated_text
        expected_batch = {str(segment["segment_key"]) for segment in batch}
        if set(batch_translated) != expected_batch:
            missing = sorted(expected_batch - set(batch_translated))
            extra = sorted(set(batch_translated) - expected_batch)
            raise RuntimeError(
                "film localization Agent batch contract mismatch: "
                f"batch={batch_no}, missing={missing[:5]}, extra={extra[:5]}"
            )
        translated.update(batch_translated)

    expected = {str(segment["segment_key"]) for segment in segments}
    if set(translated) != expected:
        missing = sorted(expected - set(translated))
        extra = sorted(set(translated) - expected)
        raise RuntimeError(
            "film localization Agent contract mismatch: "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )
    return translated


def _estimated_duration_ms(text: str, target_language: str) -> int:
    compact = text.strip()
    if not compact:
        return 0
    if target_language in {"ja", "ko"}:
        chars_per_second = 7.0 if target_language == "ja" else 6.0
        units = len(re.sub(r"\s+", "", compact))
        return int(round(units / chars_per_second * 1000))
    words = re.findall(r"\b[\w'-]+\b", compact, flags=re.UNICODE)
    return int(round(max(1, len(words)) / 2.6 * 1000))


def localize_film_script(
    job_dir: str | Path,
    *,
    target_language: str = "en",
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Read film Guiguzi output, translate narration, and persist timing metadata."""
    language = str(target_language or "en").strip().lower()
    if language not in SUPPORTED_TARGET_LANGUAGES:
        allowed = ",".join(sorted(SUPPORTED_TARGET_LANGUAGES))
        raise ValueError(
            f"unsupported film target_language={language}; allowed={allowed}"
        )
    root = Path(job_dir)
    guiguzi_path = root / "guiguzi.json"
    if not guiguzi_path.is_file():
        raise ValueError("film guiguzi.json missing; run Guiguzi classification first")
    try:
        guiguzi_doc = json.loads(guiguzi_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("film guiguzi.json is invalid") from exc
    if guiguzi_doc.get("mode") != "film_script_split":
        raise ValueError("film Guiguzi classification is not ready")
    narration = [
        segment for segment in (guiguzi_doc.get("segments") or [])
        if isinstance(segment, dict)
        and segment.get("role") == "replaceable_narration"
    ]
    if not narration:
        raise ValueError("film Guiguzi found no replaceable narration")

    on_progress(
        f"film localization start: segments={len(narration)}, target={language}"
    )
    translations = _translate_segments(narration, language, on_progress)
    localized: list[dict[str, Any]] = []
    for segment in narration:
        start_ms = int(segment.get("start_ms") or 0)
        end_ms = max(start_ms, int(segment.get("end_ms") or start_ms))
        translated_text = translations[str(segment["segment_key"])]
        available_duration_ms = end_ms - start_ms
        estimated_duration_ms = _estimated_duration_ms(
            translated_text,
            language,
        )
        localized.append({
            "segment_id": segment["id"],
            "source_segment_id": segment.get("source_segment_id", segment["id"]),
            "source_work_id": segment.get("source_work_id", ""),
            "segment_key": segment["segment_key"],
            "part_index": segment.get("part_index"),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "source_text": str(segment.get("source_text") or ""),
            "translated_text": translated_text,
            "target_language": language,
            "available_duration_ms": available_duration_ms,
            "estimated_duration_ms": estimated_duration_ms,
            "duration_fit": (
                "ok"
                if estimated_duration_ms <= available_duration_ms
                else "too_long"
            ),
            "duration_ratio": (
                round(estimated_duration_ms / available_duration_ms, 3)
                if available_duration_ms > 0
                else None
            ),
        })

    output = {
        "version": 1,
        "mode": "film_localization",
        "target_language": language,
        "segments": localized,
    }
    rw_root = root / "02_rw"
    rw_root.mkdir(parents=True, exist_ok=True)
    output_path = rw_root / "film_localization.json"
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    on_progress(f"film localization done: segments={len(localized)}")
    return {
        **output,
        "localization_relpath": "02_rw/film_localization.json",
        "segment_count": len(localized),
        "duration_fit_count": sum(
            1 for segment in localized if segment["duration_fit"] == "ok"
        ),
    }
