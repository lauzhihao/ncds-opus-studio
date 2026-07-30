"""Classify film audio and proofread replaceable narration for Guiguzi."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ncds_opus_factory.common import works_repo
from ncds_opus_factory.common.opus_cli import call_opus, is_opus_available

ROLE_NARRATION = "replaceable_narration"
ROLE_ORIGINAL = "preserved_original"
ROLE_UNKNOWN = "unknown"

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
ReviewerFn = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
ProgressFn = Callable[[str], None]
FilmTextRevisionAgentFn = Callable[
    [list[dict[str, Any]], ProgressFn],
    dict[str, Any],
]


def _noop(_text: str) -> None:
    return None


def _signal(text: str) -> tuple[str, str, float]:
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    if cjk and not latin:
        return ROLE_NARRATION, "zh", 0.99
    if latin and not cjk:
        return ROLE_ORIGINAL, "en", 0.99
    if cjk and latin:
        if cjk >= latin * 2:
            return ROLE_NARRATION, "zh", 0.72
        if latin >= cjk * 2:
            return ROLE_ORIGINAL, "en", 0.72
        return ROLE_UNKNOWN, "mixed", 0.45
    return ROLE_UNKNOWN, "unknown", 0.25


def _word_role(word: dict[str, Any]) -> str:
    role, _language, _confidence = _signal(str(word.get("text") or ""))
    return role


def _mixed_groups(
    text: str,
    words: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]], int]] | None:
    """Return role runs and exact source-text offsets, preserving every character."""
    located: list[tuple[dict[str, Any], int]] = []
    cursor = 0
    for word in words:
        label = str(word.get("text") or "")
        if not label:
            continue
        start = text.find(label, cursor)
        if start < 0:
            return None
        located.append((word, start))
        cursor = start + len(label)
    if not located:
        return None

    groups: list[tuple[str, list[dict[str, Any]], int]] = []
    for word, offset in located:
        role = _word_role(word)
        if role == ROLE_UNKNOWN:
            role = groups[-1][0] if groups else ROLE_UNKNOWN
        if groups and groups[-1][0] == role:
            groups[-1][1].append(word)
        else:
            groups.append((role, [word], offset))
    if len(groups) < 2:
        return None
    return groups


def classify_timeline(
    timeline: dict[str, Any],
    *,
    source_work_id: str = "",
) -> list[dict[str, Any]]:
    """Classify in timeline order; mixed-language segments split on word timestamps."""
    out: list[dict[str, Any]] = []
    for source in timeline.get("segments") or []:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or f"seg_{len(out) + 1:04d}")
        text = str(source.get("text") or "")
        start_ms = int(source.get("start_ms") or 0)
        end_ms = max(start_ms, int(source.get("end_ms") or start_ms))
        words = [
            word for word in (source.get("words") or [])
            if isinstance(word, dict)
        ]
        role, language, confidence = _signal(text)
        groups = (
            _mixed_groups(text, words)
            if role == ROLE_UNKNOWN and language == "mixed"
            else None
        )
        if groups:
            for index, (group_role, group_words, text_start) in enumerate(groups):
                slice_start = 0 if index == 0 else text_start
                next_text_start = (
                    groups[index + 1][2] if index + 1 < len(groups) else len(text)
                )
                next_time = (
                    int(groups[index + 1][1][0].get("start_ms") or end_ms)
                    if index + 1 < len(groups)
                    else end_ms
                )
                group_start_ms = (
                    start_ms
                    if index == 0
                    else int(group_words[0].get("start_ms") or start_ms)
                )
                group_end_ms = max(group_start_ms, next_time)
                group_text = text[slice_start:next_text_start]
                group_language = {
                    ROLE_NARRATION: "zh",
                    ROLE_ORIGINAL: "en",
                }.get(group_role, "unknown")
                item = {
                    "id": source_id,
                    "source_segment_id": source_id,
                    "source_work_id": source_work_id,
                    "part_index": index + 1,
                    "segment_key": (
                        f"{source_work_id}:{source_id}:part_{index + 1}"
                    ),
                    "start_ms": group_start_ms,
                    "end_ms": group_end_ms,
                    "source_text_raw": group_text,
                    "source_text": group_text,
                    "role": group_role,
                    "language": group_language,
                    "confidence": 0.98,
                }
                if group_role == ROLE_ORIGINAL:
                    item["subtype"] = "unknown"
                out.append(item)
            continue

        item = {
            "id": source_id,
            "source_segment_id": source_id,
            "source_work_id": source_work_id,
            "segment_key": f"{source_work_id}:{source_id}",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "source_text_raw": text,
            "source_text": text,
            "role": role,
            "language": language,
            "confidence": confidence,
        }
        if role == ROLE_ORIGINAL:
            item["subtype"] = "unknown"
        out.append(item)
    return out


def _review_ambiguous_with_opus(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not is_opus_available():
        return []
    prompt = "\n".join([
        "Review only ambiguous film-audio transcript classifications.",
        "Return only a JSON array. Each object may contain segment_key, role, subtype, confidence.",
        "role must be replaceable_narration, preserved_original, or unknown.",
        "subtype may be dialogue, song, ambience, or unknown.",
        "Do not return or rewrite source_text, start_ms, or end_ms.",
        json.dumps([
            {
                "segment_key": segment["segment_key"],
                "source_text": segment["source_text"],
                "language": segment["language"],
                "rule_role": segment["role"],
            }
            for segment in segments
        ], ensure_ascii=False),
    ])
    try:
        value = json.loads(
            call_opus(
                prompt,
                timeout_seconds=120,
                effort="low",
            )
        )
    except Exception:  # noqa: BLE001 - Agent audit is optional; rules remain usable.
        return []
    return value if isinstance(value, list) else []


REVIEW_AGENT: ReviewerFn = _review_ambiguous_with_opus


def audit_ambiguous_segments(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply reviewer labels only; source text and timing remain immutable."""
    # A mixed-language segment with a deterministic dominant role is already
    # usable (for example English dialogue with a Chinese filler sound). Only
    # unresolved rows need an Agent call; reviewing every 0.72-confidence
    # mixed row made the otherwise local classification wait on Opus.
    ambiguous = [
        segment for segment in segments
        if segment.get("role") == ROLE_UNKNOWN
    ]
    if not ambiguous:
        return segments
    allowed_roles = {ROLE_NARRATION, ROLE_ORIGINAL, ROLE_UNKNOWN}
    allowed_subtypes = {"dialogue", "song", "ambience", "unknown"}
    try:
        reviews = REVIEW_AGENT(ambiguous)
    except Exception:  # noqa: BLE001 - reviewer seam must never erase rule output.
        return segments
    by_key = {
        str(review.get("segment_key")): review
        for review in reviews
        if isinstance(review, dict)
    }
    for segment in ambiguous:
        review = by_key.get(str(segment["segment_key"]))
        if review is None:
            continue
        role = str(review.get("role") or "")
        subtype = str(review.get("subtype") or "")
        if role in allowed_roles:
            segment["role"] = role
        if subtype in allowed_subtypes:
            segment["subtype"] = subtype
        try:
            confidence = float(review.get("confidence"))
        except (TypeError, ValueError):
            confidence = float(segment.get("confidence") or 0)
        segment["confidence"] = max(0.0, min(1.0, confidence))
    return segments


def _parse_json_object(text: str, *, stage: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError(
                f"film text revision Agent returned invalid JSON at {stage}"
            )
        value = json.loads(stripped[start:end + 1])
    if not isinstance(value, dict):
        raise RuntimeError(
            f"film text revision Agent must return a JSON object at {stage}"
        )
    return value


def _revision_agent_rows(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "revision_id": index,
            "source_text_raw": str(segment["source_text_raw"]),
        }
        for index, segment in enumerate(segments, start=1)
    ]


def _call_opus_film_text_revision_agent(
    segments: list[dict[str, Any]],
    on_progress: ProgressFn,
) -> dict[str, Any]:
    """Build one full-film glossary, then proofread the full narration once."""
    if not is_opus_available():
        raise RuntimeError(
            "film text revision Agent unavailable: opus launcher is not installed"
        )

    source_rows = _revision_agent_rows(segments)
    on_progress("提取人物术语（Agent 1/2）")
    context_prompt = "\n".join([
        "Analyze the complete Chinese narration transcript of one film work.",
        "The transcript comes from ASR and may contain inconsistent names, "
        "homophones, punctuation errors, and recognition errors.",
        "Return only one JSON object with context_summary and entity_glossary.",
        "context_summary must briefly state the film, characters, and plot "
        "context needed to proofread every later segment.",
        "entity_glossary must be a JSON array. Every entry must contain "
        "canonical, aliases, and category; note is optional.",
        "Use canonical names supported by the full-film context. Put observed "
        "ASR variants in aliases. Do not invent unsupported plot facts.",
        json.dumps(source_rows, ensure_ascii=False),
    ])
    context_doc = _parse_json_object(
        call_opus(
            context_prompt,
            timeout_seconds=600,
            effort="medium",
        ),
        stage="glossary",
    )
    context_summary = str(context_doc.get("context_summary") or "").strip()
    if not context_summary:
        raise RuntimeError(
            "film text revision Agent returned empty context_summary"
        )
    entity_glossary = _normalize_entity_glossary(
        context_doc.get("entity_glossary")
    )

    on_progress("校订解说稿（Agent 2/2）")
    correction_prompt = "\n".join([
        "Proofread the complete Chinese film narration ASR.",
        "Return only one JSON object with segments. segments must contain "
        "every input revision_id exactly once and corrected_text.",
        "Correct recognition errors, inconsistent character/place names, "
        "homophones, wording breaks, and punctuation only.",
        "Keep the narrator's meaning, facts, voice, and segment boundaries. "
        "Do not summarize, translate, merge, split, or add facts.",
        "Use the same canonical entity spelling throughout the film.",
        "Full-film context summary:",
        context_summary,
        "Full-film entity glossary:",
        json.dumps(entity_glossary, ensure_ascii=False),
        "Complete narration to proofread:",
        json.dumps(source_rows, ensure_ascii=False),
    ])
    correction_doc = _parse_json_object(
        call_opus(
            correction_prompt,
            timeout_seconds=1200,
            effort="high",
        ),
        stage="correction",
    )
    rows = correction_doc.get("segments")
    if not isinstance(rows, list):
        raise RuntimeError(
            "film text revision Agent correction response must contain segments"
        )
    expected_ids = set(range(1, len(segments) + 1))
    corrected_by_id: dict[int, str] = {}
    duplicate_ids: list[int] = []
    extra_ids: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            revision_id = int(row.get("revision_id"))
        except (TypeError, ValueError):
            continue
        corrected_text = str(row.get("corrected_text") or "").strip()
        if revision_id not in expected_ids:
            extra_ids.append(revision_id)
            continue
        if not corrected_text:
            continue
        if revision_id in corrected_by_id:
            duplicate_ids.append(revision_id)
            continue
        corrected_by_id[revision_id] = corrected_text
    missing_ids = sorted(expected_ids - set(corrected_by_id))
    if missing_ids or extra_ids or duplicate_ids:
        raise RuntimeError(
            "film text revision Agent correction contract mismatch: "
            f"missing={missing_ids[:5]}, extra={sorted(set(extra_ids))[:5]}, "
            f"duplicates={sorted(set(duplicate_ids))[:5]}"
        )
    corrected_rows = [
        {
            "segment_key": str(segment["segment_key"]),
            "corrected_text": corrected_by_id[index],
        }
        for index, segment in enumerate(segments, start=1)
    ]

    return {
        "entity_glossary": entity_glossary,
        "segments": corrected_rows,
    }


FILM_TEXT_REVISION_AGENT: FilmTextRevisionAgentFn = (
    _call_opus_film_text_revision_agent
)


def _normalize_entity_glossary(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RuntimeError(
            "film text revision Agent entity_glossary must be a JSON array"
        )
    normalized: list[dict[str, Any]] = []
    canonical_names: set[str] = set()
    for index, raw_entry in enumerate(value):
        if not isinstance(raw_entry, dict):
            raise RuntimeError(
                "film text revision Agent glossary entry must be an object: "
                f"index={index}"
            )
        canonical = str(raw_entry.get("canonical") or "").strip()
        category = str(raw_entry.get("category") or "").strip()
        aliases_value = raw_entry.get("aliases")
        if not canonical or not category or not isinstance(aliases_value, list):
            raise RuntimeError(
                "film text revision Agent glossary entry requires "
                f"canonical, aliases, category: index={index}"
            )
        canonical_key = canonical.casefold()
        if canonical_key in canonical_names:
            raise RuntimeError(
                "film text revision Agent returned duplicate glossary canonical: "
                f"{canonical}"
            )
        canonical_names.add(canonical_key)
        aliases = list(dict.fromkeys(
            alias
            for alias in (
                str(alias).strip() for alias in aliases_value
            )
            if alias and alias != canonical
        ))
        entry: dict[str, Any] = {
            "canonical": canonical,
            "aliases": aliases,
            "category": category,
        }
        note = str(raw_entry.get("note") or "").strip()
        if note:
            entry["note"] = note
        normalized.append(entry)
    return normalized


def _apply_film_text_revision(
    segments: list[dict[str, Any]],
    revision_doc: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    narration = [
        segment for segment in segments
        if segment.get("role") == ROLE_NARRATION
    ]
    entity_glossary = _normalize_entity_glossary(
        revision_doc.get("entity_glossary")
    )
    rows = revision_doc.get("segments")
    if not isinstance(rows, list):
        raise RuntimeError(
            "film text revision Agent response must contain segments"
        )

    expected_keys = {
        str(segment["segment_key"])
        for segment in narration
    }
    corrected_by_key: dict[str, str] = {}
    duplicate_keys: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        segment_key = str(row.get("segment_key") or "").strip()
        corrected_text = str(row.get("corrected_text") or "").strip()
        if not segment_key or not corrected_text:
            continue
        if segment_key in corrected_by_key:
            duplicate_keys.append(segment_key)
            continue
        corrected_by_key[segment_key] = corrected_text
    missing = sorted(expected_keys - set(corrected_by_key))
    extra = sorted(set(corrected_by_key) - expected_keys)
    if missing or extra or duplicate_keys:
        raise RuntimeError(
            "film text revision Agent contract mismatch: "
            f"missing={missing[:5]}, extra={extra[:5]}, "
            f"duplicates={sorted(set(duplicate_keys))[:5]}"
        )

    corrected_count = 0
    for segment in segments:
        raw_text = str(segment.get("source_text_raw") or "")
        if segment.get("role") == ROLE_NARRATION:
            corrected_text = corrected_by_key[str(segment["segment_key"])]
            segment["source_text"] = corrected_text
            if corrected_text != raw_text.strip():
                corrected_count += 1
        else:
            segment["source_text"] = raw_text

    revision = {
        "status": "done",
        "corrected_count": corrected_count,
        "narration_count": len(narration),
    }
    return segments, entity_glossary, revision


def _specific_glossary_aliases(
    entity_glossary: list[dict[str, Any]],
) -> list[tuple[int, str]]:
    canonical_values = {
        str(entry["canonical"]).casefold()
        for entry in entity_glossary
    }
    alias_targets: dict[str, set[str]] = {}
    alias_values: dict[str, str] = {}
    alias_indexes: dict[str, int] = {}
    for index, entry in enumerate(entity_glossary):
        canonical = str(entry["canonical"])
        for raw_alias in entry["aliases"]:
            alias = str(raw_alias).strip()
            alias_key = alias.casefold()
            if (
                not alias
                or alias_key in canonical_values
                or alias in canonical
                or canonical in alias
            ):
                continue
            compact = re.sub(r"\s+", "", alias)
            if _CJK_RE.search(compact):
                if len(compact) < 2:
                    continue
            elif len(re.sub(r"[^A-Za-z0-9]", "", compact)) < 4:
                continue
            alias_targets.setdefault(alias_key, set()).add(canonical.casefold())
            alias_values[alias_key] = alias
            alias_indexes[alias_key] = index
    return [
        (alias_indexes[alias_key], alias_values[alias_key])
        for alias_key, targets in alias_targets.items()
        if len(targets) == 1
    ]


def _check_revision_consistency(
    segments: list[dict[str, Any]],
    entity_glossary: list[dict[str, Any]],
) -> None:
    aliases = _specific_glossary_aliases(entity_glossary)
    seen_keys: set[str] = set()
    for segment in segments:
        segment_key = str(segment.get("segment_key") or "")
        if not segment_key or segment_key in seen_keys:
            raise RuntimeError(
                f"film timeline has duplicate or empty segment_key={segment_key}"
            )
        seen_keys.add(segment_key)
        if "source_text_raw" not in segment:
            raise RuntimeError(
                f"film timeline segment missing source_text_raw={segment_key}"
            )
        raw_text = str(segment.get("source_text_raw") or "")
        source_text = str(segment.get("source_text") or "")
        if segment.get("role") == ROLE_ORIGINAL and source_text != raw_text:
            raise RuntimeError(
                f"film original audio was rewritten: segment_key={segment_key}"
            )
        if segment.get("role") == ROLE_NARRATION and not source_text.strip():
            raise RuntimeError(
                f"film revised narration is empty: segment_key={segment_key}"
            )
        if segment.get("role") != ROLE_NARRATION:
            continue
        source_casefold = source_text.casefold()
        for glossary_index, alias in aliases:
            if alias.casefold() in source_casefold:
                raise RuntimeError(
                    "film revised narration contains a non-canonical glossary "
                    f"alias: segment_key={segment_key}, "
                    f"glossary_index={glossary_index}"
                )


def _timeline_path(entry: dict[str, Any], job_dir: Path) -> Path | None:
    platform = str(entry.get("platform") or "douyin")
    aweme_id = str(entry.get("aweme_id") or "")
    if aweme_id:
        candidate = works_repo.work_dir(platform, aweme_id) / "asr.timeline.json"
        if candidate.is_file():
            return candidate
    value = entry.get("timeline")
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    for root in (Path(__file__).resolve().parents[3], job_dir):
        resolved = root / candidate
        if resolved.is_file():
            return resolved
    return None


def classify_collected_timelines(
    job_dir: str | Path,
    collected: list[dict[str, Any]],
    *,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Load沈括 timeline artifacts without copying the full timeline into job state."""
    root = Path(job_dir)
    classified: list[dict[str, Any]] = []
    on_progress("分类解说与原声")
    for entry in collected:
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        path = _timeline_path(entry, root)
        if path is None:
            continue
        try:
            timeline = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(timeline, dict):
            continue
        prefix = str(entry.get("aweme_id") or path.parent.name)
        classified.extend(classify_timeline(timeline, source_work_id=prefix))
    classified = audit_ambiguous_segments(classified)
    narration = [
        segment for segment in classified
        if segment.get("role") == ROLE_NARRATION
    ]
    if narration:
        revision_doc = FILM_TEXT_REVISION_AGENT(
            [dict(segment) for segment in narration],
            on_progress,
        )
        if not isinstance(revision_doc, dict):
            raise RuntimeError(
                "film text revision Agent must return a JSON object"
            )
        segments, entity_glossary, revision = _apply_film_text_revision(
            classified,
            revision_doc,
        )
    else:
        on_progress("提取人物术语")
        on_progress("校订解说稿")
        segments = classified
        entity_glossary = []
        revision = {
            "status": "done",
            "corrected_count": 0,
            "narration_count": 0,
        }
    on_progress("一致性检查")
    _check_revision_consistency(segments, entity_glossary)
    return {
        "segments": segments,
        "entity_glossary": entity_glossary,
        "revision": revision,
    }
