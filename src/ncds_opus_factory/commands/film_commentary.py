"""Build Guiguzi's clean film commentary from Shenkuo OCR cues."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from ncds_opus_core.common import cancel

from ncds_opus_factory.common.opus_cli import call_opus, is_opus_available

ProgressFn = Callable[[str], None]
FilmCueKind = Literal["narration", "dialogue", "noise"]
FilmCommentaryAgentFn = Callable[
    [list[dict[str, Any]], ProgressFn],
    dict[str, Any] | None,
]

_ROOT = Path(__file__).resolve().parents[3]
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_NOISE_RE = re.compile(
    r"^(抖音|关注|点赞|评论|转发|完整版|未完待续|点击头像|第[一二三四五六七八九十0-9]+集)"
)
_SHORT_DIALOGUE_COMMANDS = {
    "快跑",
    "救命",
    "住手",
    "闭嘴",
    "等等",
    "别动",
    "小心",
    "不要",
    "回来",
    "走开",
}
_ALLOWED_KINDS = {"narration", "dialogue", "noise"}
_AGENT_BATCH_SIZE = 80


def _noop(_text: str) -> None:
    return None


def _resolve_artifact(value: Any, job_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.is_absolute() and path.is_file():
        return path
    for root in (_ROOT, job_dir):
        candidate = root / path
        if candidate.is_file():
            return candidate
    return None


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _compact(text: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()《》【】\[\]-]", "", text)


def _deterministic_kind(text: str) -> tuple[FilmCueKind, float]:
    """Provide an offline-safe minimum classification before optional Agent review."""
    compact = _compact(text)
    if not compact or not _CJK_RE.search(compact) or _NOISE_RE.search(compact):
        return "noise", 0.95
    if (
        len(compact) % 2 == 0
        and compact[: len(compact) // 2] == compact[len(compact) // 2 :]
        and compact[: len(compact) // 2] in _SHORT_DIALOGUE_COMMANDS
    ):
        return "dialogue", 0.95
    if compact in _SHORT_DIALOGUE_COMMANDS:
        return "dialogue", 0.85
    if (
        (text.strip().startswith(("“", "「", "『", "\"")))
        and text.strip().endswith(("”", "」", "』", "\""))
    ):
        return "dialogue", 0.85
    return "narration", 0.8


def _timeline_segments(
    source: dict[str, Any],
    *,
    job_dir: Path,
) -> list[dict[str, Any]]:
    path = _resolve_artifact(source.get("asr_timeline"), job_dir)
    if path is None:
        return []
    doc = _load_json(path, label="film ASR timeline")
    return [
        segment
        for segment in (doc.get("segments") or [])
        if isinstance(segment, dict)
    ]


def _aligned_asr_text(
    cue: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> str:
    start_ms = int(cue.get("start_ms") or 0)
    end_ms = max(start_ms, int(cue.get("end_ms") or start_ms))
    candidates: list[tuple[int, str]] = []
    for segment in timeline:
        segment_start = int(segment.get("start_ms") or 0)
        segment_end = max(
            segment_start,
            int(segment.get("end_ms") or segment_start),
        )
        overlap = min(end_ms, segment_end) - max(start_ms, segment_start)
        text = str(segment.get("text") or "").strip()
        if overlap > 0 and text:
            candidates.append((segment_start, text))
    candidates.sort(key=lambda item: item[0])
    return "".join(text for _start, text in candidates)


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
                f"film commentary Agent returned invalid JSON at {stage}"
            )
        value = json.loads(stripped[start:end + 1])
    if not isinstance(value, dict):
        raise RuntimeError(
            f"film commentary Agent must return a JSON object at {stage}"
        )
    return value


def _normalize_glossary(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical") or "").strip()
        category = str(item.get("category") or "").strip()
        aliases_value = item.get("aliases")
        if not canonical or not category or not isinstance(aliases_value, list):
            continue
        key = canonical.casefold()
        if key in seen:
            continue
        seen.add(key)
        entry: dict[str, Any] = {
            "canonical": canonical,
            "aliases": list(dict.fromkeys(
                alias
                for alias in (str(value).strip() for value in aliases_value)
                if alias and alias != canonical
            )),
            "category": category,
        }
        note = str(item.get("note") or "").strip()
        if note:
            entry["note"] = note
        normalized.append(entry)
    return normalized


def _call_opus_commentary_agent(
    cues: list[dict[str, Any]],
    on_progress: ProgressFn,
) -> dict[str, Any] | None:
    """Optionally improve deterministic labels while keeping OCR immutable."""
    if not is_opus_available():
        return None
    source_rows = [
        {
            "cue_id": cue["cue_id"],
            "ocr_text": cue["ocr_text"],
            "asr_text": cue["asr_text"],
            "rule_kind": cue["kind"],
        }
        for cue in cues
    ]
    on_progress("Film commentary: building full-film glossary")
    context = _parse_json_object(
        call_opus(
            "\n".join([
                "Analyze all burned-in Chinese subtitles from one film commentary.",
                "OCR text is the source of truth. ASR text is auxiliary evidence only.",
                "Return only a JSON object with context_summary and entity_glossary.",
                "entity_glossary entries contain canonical, aliases, category, and optional note.",
                "Do not invent plot facts.",
                json.dumps(source_rows, ensure_ascii=False),
            ]),
            timeout_seconds=600,
            effort="medium",
        ),
        stage="glossary",
    )
    context_summary = str(context.get("context_summary") or "").strip()
    glossary = _normalize_glossary(context.get("entity_glossary"))
    reviewed: list[dict[str, Any]] = []
    for offset in range(0, len(source_rows), _AGENT_BATCH_SIZE):
        cancel.checkpoint()
        batch = source_rows[offset:offset + _AGENT_BATCH_SIZE]
        number = offset // _AGENT_BATCH_SIZE + 1
        total = (len(source_rows) + _AGENT_BATCH_SIZE - 1) // _AGENT_BATCH_SIZE
        on_progress(f"Film commentary: review batch {number}/{total}")
        response = _parse_json_object(
            call_opus(
                "\n".join([
                    "Classify and proofread burned-in film commentary subtitles.",
                    "Return only a JSON object with cues.",
                    "Return every input cue exactly once, in the same order.",
                    "Each cue must contain cue_id, kind, text, confidence.",
                    "kind must be narration, dialogue, or noise.",
                    "For narration, text is a proofread Chinese narration line.",
                    "For dialogue/noise, preserve OCR wording in text.",
                    "OCR text is immutable evidence and must not be replaced by ASR.",
                    "ASR text may only help resolve homophones and punctuation.",
                    "Do not merge, split, translate, summarize, or add facts.",
                    f"Full-film context: {context_summary}",
                    "Full-film glossary:",
                    json.dumps(glossary, ensure_ascii=False),
                    "Cues:",
                    json.dumps(batch, ensure_ascii=False),
                ]),
                timeout_seconds=600,
                effort="medium",
            ),
            stage=f"batch-{number}",
        )
        rows = response.get("cues")
        if not isinstance(rows, list):
            raise RuntimeError(
                f"film commentary Agent batch {number} omitted cues"
            )
        reviewed.extend(row for row in rows if isinstance(row, dict))
    return {"entity_glossary": glossary, "cues": reviewed}


FILM_COMMENTARY_AGENT: FilmCommentaryAgentFn = _call_opus_commentary_agent


def _source_rows(
    job_dir: Path,
    sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_docs: list[dict[str, Any]] = []
    cues: list[dict[str, Any]] = []
    for entry in sources:
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        film_source = (
            entry.get("film_source")
            if isinstance(entry.get("film_source"), dict)
            else entry
        )
        if film_source.get("mode") != "film_subtitle_source":
            raise ValueError("Guiguzi requires film_subtitle_source input")
        ocr = film_source.get("ocr")
        if not isinstance(ocr, dict):
            raise ValueError("film_source.ocr is missing")
        raw_path = _resolve_artifact(ocr.get("raw_cues"), job_dir)
        if raw_path is None:
            raise ValueError("film_source OCR raw.json is missing")
        raw_doc = _load_json(raw_path, label="film OCR raw cues")
        timeline = _timeline_segments(film_source, job_dir=job_dir)
        source_work_id = str(
            entry.get("aweme_id")
            or raw_doc.get("source_work_id")
            or raw_path.parent.parent.name
        )
        platform = str(
            entry.get("platform")
            or raw_doc.get("platform")
            or "unknown"
        )
        source_docs.append({
            "source_work_id": source_work_id,
            "platform": platform,
            **film_source,
        })
        for raw_cue in raw_doc.get("cues") or []:
            if not isinstance(raw_cue, dict):
                continue
            ocr_text = str(raw_cue.get("text") or "").strip()
            if not ocr_text:
                continue
            kind, rule_confidence = _deterministic_kind(ocr_text)
            ocr_confidence = max(
                0.0,
                min(1.0, float(raw_cue.get("confidence") or 0.0)),
            )
            cues.append({
                "cue_id": "",
                "source_work_id": source_work_id,
                "start_ms": int(raw_cue.get("start_ms") or 0),
                "end_ms": max(
                    int(raw_cue.get("start_ms") or 0),
                    int(raw_cue.get("end_ms") or 0),
                ),
                "ocr_text": ocr_text,
                "asr_text": _aligned_asr_text(raw_cue, timeline),
                "text": ocr_text,
                "kind": kind,
                "confidence": round(min(ocr_confidence, rule_confidence), 4),
            })
    cues.sort(
        key=lambda cue: (
            str(cue["source_work_id"]),
            int(cue["start_ms"]),
            int(cue["end_ms"]),
        )
    )
    for index, cue in enumerate(cues, start=1):
        cue["cue_id"] = f"cue_{index:04d}"
    return source_docs, cues


def _apply_agent_review(
    cues: list[dict[str, Any]],
    agent_doc: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not agent_doc:
        return cues, []
    glossary = _normalize_glossary(agent_doc.get("entity_glossary"))
    rows = agent_doc.get("cues")
    if not isinstance(rows, list):
        raise RuntimeError("film commentary Agent response must contain cues")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        cue_id = str(row.get("cue_id") or "")
        if cue_id and cue_id not in by_id:
            by_id[cue_id] = row
    expected = {str(cue["cue_id"]) for cue in cues}
    if set(by_id) != expected:
        missing = sorted(expected - set(by_id))
        extra = sorted(set(by_id) - expected)
        raise RuntimeError(
            "film commentary Agent cue contract mismatch: "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )
    for cue in cues:
        row = by_id[str(cue["cue_id"])]
        kind = str(row.get("kind") or "")
        if kind not in _ALLOWED_KINDS:
            raise RuntimeError(
                f"film commentary Agent returned invalid kind={kind}"
            )
        text = str(row.get("text") or "").strip()
        if not text:
            text = str(cue["ocr_text"])
        # OCR is always retained verbatim as evidence. Only narration may be
        # semantically proofread; dialogue/noise stays source-faithful.
        cue["text"] = text if kind == "narration" else cue["ocr_text"]
        cue["kind"] = kind
        try:
            confidence = float(row.get("confidence"))
        except (TypeError, ValueError):
            confidence = float(cue["confidence"])
        cue["confidence"] = round(max(0.0, min(1.0, confidence)), 4)
    return cues, glossary


def _srt_timestamp(value_ms: int) -> str:
    value_ms = max(0, int(value_ms))
    hours, remainder = divmod(value_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _render_srt(cues: list[dict[str, Any]]) -> str:
    blocks = [
        "\n".join([
            str(index),
            f"{_srt_timestamp(int(cue['start_ms']))} --> "
            f"{_srt_timestamp(int(cue['end_ms']))}",
            str(cue["text"]),
        ])
        for index, cue in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def build_film_commentary(
    job_dir: str | Path,
    sources: list[dict[str, Any]],
    *,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Align OCR/ASR, classify cues, proofread narration, and persist scripts."""
    root = Path(job_dir)
    on_progress("Film commentary: loading OCR cues")
    source_docs, cues = _source_rows(root, sources)
    if not cues:
        raise ValueError("film subtitle sources contain no OCR cues")
    agent_doc: dict[str, Any] | None = None
    try:
        agent_doc = FILM_COMMENTARY_AGENT(
            [dict(cue) for cue in cues],
            on_progress,
        )
    except cancel.TaskCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        # Deterministic classification is a complete offline baseline. Agent
        # failure only lowers semantic confidence and creates QA work.
        on_progress(
            "Film commentary: Agent unavailable; "
            f"using deterministic review ({type(exc).__name__})"
        )
    cues, glossary = _apply_agent_review(cues, agent_doc)
    narration = [cue for cue in cues if cue["kind"] == "narration"]
    if not narration:
        raise ValueError("film commentary contains no narration cues")

    review_items = [
        {
            "cue_id": cue["cue_id"],
            "source_work_id": cue["source_work_id"],
            "ocr_text": cue["ocr_text"],
            "asr_text": cue["asr_text"],
            "text": cue["text"],
            "kind": cue["kind"],
            "confidence": cue["confidence"],
        }
        for cue in cues
        if float(cue["confidence"]) < 0.75
    ]
    qa = {
        "raw_cues": len(cues),
        "narration_cues": len(narration),
        "dialogue_filtered": sum(
            1 for cue in cues if cue["kind"] == "dialogue"
        ),
        "noise_filtered": sum(
            1 for cue in cues if cue["kind"] == "noise"
        ),
        "needs_review": len(review_items),
    }
    script_dir = root / "film_script"
    script_dir.mkdir(parents=True, exist_ok=True)
    txt_path = script_dir / "narration.txt"
    srt_path = script_dir / "narration.srt"
    json_path = script_dir / "narration.json"
    qa_path = script_dir / "qa.json"
    txt_path.write_text(
        "\n".join(str(cue["text"]) for cue in narration) + "\n",
        encoding="utf-8",
    )
    srt_path.write_text(_render_srt(narration), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "version": 1,
                "mode": "film_commentary",
                "cues": narration,
                "entity_glossary": glossary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    qa_path.write_text(
        json.dumps(
            {**qa, "review_items": review_items},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    result = {
        "mode": "film_commentary",
        "status": "done",
        "sources": source_docs,
        "cues": cues,
        "script": {
            "txt": "film_script/narration.txt",
            "srt": "film_script/narration.srt",
            "json": "film_script/narration.json",
        },
        "entity_glossary": glossary,
        "qa": qa,
    }
    on_progress(
        "Film commentary: done "
        f"raw={qa['raw_cues']} narration={qa['narration_cues']} "
        f"dialogue={qa['dialogue_filtered']} noise={qa['noise_filtered']} "
        f"review={qa['needs_review']}"
    )
    return result
