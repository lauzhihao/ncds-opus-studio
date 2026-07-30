"""Rule-first film timeline classification for Guiguzi."""

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
        value = json.loads(call_opus(prompt, timeout_seconds=300))
    except Exception:  # noqa: BLE001 - Agent audit is optional; rules remain usable.
        return []
    return value if isinstance(value, list) else []


REVIEW_AGENT: ReviewerFn = _review_ambiguous_with_opus


def audit_ambiguous_segments(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply reviewer labels only; source text and timing remain immutable."""
    ambiguous = [
        segment for segment in segments
        if segment.get("role") == ROLE_UNKNOWN
        or segment.get("language") == "mixed"
        or float(segment.get("confidence") or 0) < 0.9
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
) -> list[dict[str, Any]]:
    """Load沈括 timeline artifacts without copying the full timeline into job state."""
    root = Path(job_dir)
    classified: list[dict[str, Any]] = []
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
    return audit_ambiguous_segments(classified)
