"""Translate clean film narration while preserving its OCR timeline.

Film localization is one logical run: one translation backend owns every
batch.  A backend failure therefore discards all of its in-memory batches and
restarts at batch one on the next backend; successful output is written only
after the complete run passes the cue-id contract.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ncds_opus_core.common import cancel

from ncds_opus_factory.common.agy_cli import call_agy
from ncds_opus_factory.common.opus_cli import (
    DEFAULT_OPUS_MODEL,
    call_opus,
)
from ncds_opus_factory.common.scodex_cli import call_scodex

logger = logging.getLogger(__name__)

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
    [list[dict[str, Any]], str, list[dict[str, Any]]],
    list[dict[str, Any]],
]

TRANSLATION_BATCH_SIZE = 40
FILM_TRANSLATION_TIMEOUT_SECONDS = int(
    os.getenv("NOF_FILM_TRANSLATION_TIMEOUT", "1800")
)
AGY_TRANSLATION_MODEL = "gemini-3.5-flash-high"
CODEX_TRANSLATION_MODEL = "gpt-5.6-terra"

# Keep the runner name separate from the persisted backend id.  ``codex`` is
# the product backend while ``scodex`` is its local account-aware launcher.
TRANSLATION_BACKENDS: tuple[dict[str, str], ...] = (
    {"id": "agy", "runner": "agy", "model": AGY_TRANSLATION_MODEL},
    {"id": "codex", "runner": "scodex", "model": CODEX_TRANSLATION_MODEL},
    {"id": "opus", "runner": "opus", "model": DEFAULT_OPUS_MODEL},
)


def _noop(_text: str) -> None:
    return None


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """Parse the shared translation response contract.

    All three production runners use this parser.  A fenced response is
    accepted for resilience, but every array element still has to be an
    object so malformed rows cannot be silently dropped before cue validation.
    """
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("[")
        end = stripped.rfind("]")
        if start < 0 or end <= start:
            raise RuntimeError("film localization backend returned invalid JSON")
        try:
            value = json.loads(stripped[start:end + 1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("film localization backend returned invalid JSON") from exc
    if not isinstance(value, list):
        raise RuntimeError("film localization backend must return a JSON array")
    if not all(isinstance(item, dict) for item in value):
        raise RuntimeError("film localization backend array contains a non-object row")
    return [dict(item) for item in value]


def _build_translation_prompt(
    segments: list[dict[str, Any]],
    target_language: str,
    entity_glossary: list[dict[str, Any]],
) -> str:
    """Build the one prompt shared by AGY, Codex, and Opus."""
    payload = [
        {
            "cue_id": segment["cue_id"],
            "source_text": segment["text"],
            "available_duration_ms": (
                int(segment.get("end_ms") or 0)
                - int(segment.get("start_ms") or 0)
            ),
        }
        for segment in segments
    ]
    return "\n".join([
        "Translate film narration segments.",
        f"Target language: {_LANGUAGE_NAMES[target_language]} ({target_language}).",
        "Return only a JSON array of objects with cue_id and translated_text.",
        "Keep every segment exactly once. Do not merge, split, omit, or add facts.",
        "Translate source_text, which is Guiguzi's clean narration text.",
        "Use one consistent target-language rendering for each canonical entity "
        "and all of its aliases in the full-film entity glossary.",
        "At normal narration speed, compress each translation to fit its "
        "available_duration_ms while preserving meaning.",
        "Full-film entity glossary:",
        json.dumps(entity_glossary, ensure_ascii=False),
        "Proofread narration segments:",
        json.dumps(payload, ensure_ascii=False),
    ])


def _validate_batch_rows(
    rows: list[dict[str, Any]],
    batch: list[dict[str, Any]],
    *,
    batch_no: int,
) -> dict[str, str]:
    """Validate exact cue-id coverage and return the batch translation map."""
    expected = [str(segment["cue_id"]) for segment in batch]
    expected_set = set(expected)
    actual: list[str] = []
    translated: dict[str, str] = {}
    for row in rows:
        cue_id = str(row.get("cue_id") or "").strip()
        translated_text = str(row.get("translated_text") or "").strip()
        if not cue_id or not translated_text:
            raise RuntimeError(
                f"film localization backend batch contract mismatch: batch={batch_no}, "
                "cue_id and translated_text are required"
            )
        actual.append(cue_id)
        translated[cue_id] = translated_text

    actual_set = set(actual)
    if (
        len(actual) != len(expected)
        or len(actual_set) != len(actual)
        or actual_set != expected_set
    ):
        missing = sorted(expected_set - actual_set)
        extra = sorted(actual_set - expected_set)
        raise RuntimeError(
            f"film localization backend batch contract mismatch: batch={batch_no}, "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )
    return translated


def _call_translation_backend(
    backend: str,
    prompt: str,
    model: str,
) -> str:
    """Dispatch one production backend through the shared CLI helpers."""
    if backend == "agy":
        return call_agy(
            prompt,
            model=model,
            timeout_seconds=FILM_TRANSLATION_TIMEOUT_SECONDS,
        )
    if backend == "codex":
        return call_scodex(
            prompt,
            model=model,
            timeout_seconds=FILM_TRANSLATION_TIMEOUT_SECONDS,
        )
    if backend == "opus":
        return call_opus(
            prompt,
            model=model,
            timeout_seconds=FILM_TRANSLATION_TIMEOUT_SECONDS,
        )
    raise RuntimeError(f"unknown film translation backend: {backend}")


def _call_opus_translation_agent(
    segments: list[dict[str, Any]],
    target_language: str,
    entity_glossary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Backward-compatible Opus-only translation hook."""
    prompt = _build_translation_prompt(segments, target_language, entity_glossary)
    return _parse_json_array(
        _call_translation_backend("opus", prompt, DEFAULT_OPUS_MODEL)
    )


# Existing callers/tests can still inject a deterministic translation agent.
# Production uses the ordered backend chain below when this identity is intact.
TRANSLATION_AGENT: TranslationAgentFn = _call_opus_translation_agent


def _translate_segments_with_backend(
    segments: list[dict[str, Any]],
    target_language: str,
    entity_glossary: list[dict[str, Any]],
    on_progress: ProgressFn,
    *,
    backend: str,
    model: str,
) -> dict[str, str]:
    """Run every batch on one backend, returning nothing until all pass."""
    translated: dict[str, str] = {}
    batch_total = (
        len(segments) + TRANSLATION_BATCH_SIZE - 1
    ) // TRANSLATION_BATCH_SIZE
    for offset in range(0, len(segments), TRANSLATION_BATCH_SIZE):
        cancel.checkpoint()
        batch = segments[offset:offset + TRANSLATION_BATCH_SIZE]
        batch_no = offset // TRANSLATION_BATCH_SIZE + 1
        on_progress(
            f"film localization backend={backend} batch={batch_no}/{batch_total} "
            f"segments={len(batch)}"
        )
        prompt = _build_translation_prompt(batch, target_language, entity_glossary)
        raw = _call_translation_backend(backend, prompt, model)
        cancel.checkpoint()
        rows = _parse_json_array(raw)
        translated.update(_validate_batch_rows(rows, batch, batch_no=batch_no))

    expected = {str(segment["cue_id"]) for segment in segments}
    if set(translated) != expected:
        missing = sorted(expected - set(translated))
        extra = sorted(set(translated) - expected)
        raise RuntimeError(
            "film localization backend contract mismatch: "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )
    return translated


def _translate_segments_with_custom_agent(
    segments: list[dict[str, Any]],
    target_language: str,
    entity_glossary: list[dict[str, Any]],
    on_progress: ProgressFn,
) -> dict[str, str]:
    """Run the legacy injected test seam with the same cue contract."""
    translated: dict[str, str] = {}
    batch_total = (
        len(segments) + TRANSLATION_BATCH_SIZE - 1
    ) // TRANSLATION_BATCH_SIZE
    for offset in range(0, len(segments), TRANSLATION_BATCH_SIZE):
        cancel.checkpoint()
        batch = segments[offset:offset + TRANSLATION_BATCH_SIZE]
        batch_no = offset // TRANSLATION_BATCH_SIZE + 1
        on_progress(
            f"film localization backend=custom batch={batch_no}/{batch_total} "
            f"segments={len(batch)}"
        )
        rows = TRANSLATION_AGENT(
            [dict(segment) for segment in batch],
            target_language,
            [dict(entry) for entry in entity_glossary],
        )
        cancel.checkpoint()
        translated.update(_validate_batch_rows(rows, batch, batch_no=batch_no))
    return translated


def _translate_segments_with_backend_fallback(
    segments: list[dict[str, Any]],
    target_language: str,
    entity_glossary: list[dict[str, Any]],
    on_progress: ProgressFn,
) -> tuple[dict[str, str], str, str]:
    """Run the ordered AGY -> Codex -> Opus chain without mixed output."""
    if TRANSLATION_AGENT is not _call_opus_translation_agent:
        return (
            _translate_segments_with_custom_agent(
                segments,
                target_language,
                entity_glossary,
                on_progress,
            ),
            "custom",
            "custom",
        )

    failures: list[str] = []
    for index, candidate in enumerate(TRANSLATION_BACKENDS):
        backend = candidate["id"]
        model = candidate["model"]
        on_progress(f"film localization backend start: {backend} model={model}")
        try:
            result = _translate_segments_with_backend(
                segments,
                target_language,
                entity_glossary,
                on_progress,
                backend=backend,
                model=model,
            )
        except cancel.TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - backend fallback boundary.
            # No partial map escapes this call.  The next backend starts at
            # offset zero, which prevents mixed-backend localization output.
            failures.append(f"{backend}:{type(exc).__name__}")
            logger.exception(
                "film localization backend failed backend=%s model=%s",
                backend,
                model,
            )
            if index < len(TRANSLATION_BACKENDS) - 1:
                on_progress(
                    f"film localization backend failed: {backend}; trying next backend"
                )
                continue
            raise RuntimeError(
                "film localization failed: all translation backends failed; "
                f"failures={','.join(failures)}"
            )
        on_progress(f"film localization backend done: {backend}")
        return result, backend, model

    raise RuntimeError("film localization failed: no translation backend")


def _translate_segments(
    segments: list[dict[str, Any]],
    target_language: str,
    entity_glossary: list[dict[str, Any]],
    on_progress: ProgressFn,
) -> dict[str, str]:
    """Compatibility wrapper returning only the cue translation map."""
    result, _backend, _model = _translate_segments_with_backend_fallback(
        segments,
        target_language,
        entity_glossary,
        on_progress,
    )
    return result


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
    if (
        not isinstance(guiguzi_doc, dict)
        or guiguzi_doc.get("mode") != "film_commentary"
        or guiguzi_doc.get("status") != "done"
    ):
        raise ValueError("film commentary is not ready")
    entity_glossary = guiguzi_doc.get("entity_glossary")
    if not isinstance(entity_glossary, list):
        raise ValueError(
            "film entity_glossary is missing; rerun Guiguzi classification"
        )
    narration = [
        segment for segment in (guiguzi_doc.get("cues") or [])
        if isinstance(segment, dict)
        and segment.get("kind") == "narration"
    ]
    if not narration:
        raise ValueError("film commentary contains no narration")
    seen_cue_ids: set[str] = set()
    for segment in narration:
        cue_id = str(segment.get("cue_id") or "")
        if not cue_id:
            raise ValueError("film commentary narration is missing cue_id")
        if cue_id in seen_cue_ids:
            raise ValueError(f"film commentary narration has duplicate cue_id={cue_id}")
        seen_cue_ids.add(cue_id)
        if not str(segment.get("text") or "").strip():
            raise ValueError(
                "film commentary narration text is empty: "
                f"cue_id={cue_id}"
            )

    on_progress(
        f"film localization start: segments={len(narration)}, target={language}"
    )
    translations, translation_backend, translation_model = (
        _translate_segments_with_backend_fallback(
            narration,
            language,
            entity_glossary,
            on_progress,
        )
    )
    localized: list[dict[str, Any]] = []
    for segment in narration:
        start_ms = int(segment.get("start_ms") or 0)
        end_ms = max(start_ms, int(segment.get("end_ms") or start_ms))
        translated_text = translations[str(segment["cue_id"])]
        available_duration_ms = end_ms - start_ms
        estimated_duration_ms = _estimated_duration_ms(
            translated_text,
            language,
        )
        localized.append({
            "cue_id": segment["cue_id"],
            "source_work_id": segment.get("source_work_id", ""),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "ocr_text": str(segment.get("ocr_text") or ""),
            "asr_text": str(segment.get("asr_text") or ""),
            "source_text": str(segment.get("text") or ""),
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
        "entity_glossary": entity_glossary,
        "source_mode": "film_commentary",
        "translation_backend": translation_backend,
        "translation_model": translation_model,
        "segments": localized,
    }
    rw_root = root / "02_rw"
    rw_root.mkdir(parents=True, exist_ok=True)
    output_path = rw_root / "film_localization.json"
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, output_path)
    on_progress(f"film localization done: segments={len(localized)}")
    return {
        **output,
        "localization_relpath": "02_rw/film_localization.json",
        "segment_count": len(localized),
        "duration_fit_count": sum(
            1 for segment in localized if segment["duration_fit"] == "ok"
        ),
    }


__all__ = [
    "AGY_TRANSLATION_MODEL",
    "CODEX_TRANSLATION_MODEL",
    "DEFAULT_OPUS_MODEL",
    "FILM_TRANSLATION_TIMEOUT_SECONDS",
    "TRANSLATION_AGENT",
    "TRANSLATION_BACKENDS",
    "TRANSLATION_BATCH_SIZE",
    "localize_film_script",
]
