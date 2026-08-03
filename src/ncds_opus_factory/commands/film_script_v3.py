"""ASR-first film commentary script extraction with OCR as visual evidence.

Version 3 intentionally replaces the OCR-first contract.  Speech recognition
produces candidate narration segments.  OCR is restricted to validating the
stable commentary subtitle track and attaching review suggestions for textual
conflicts; OCR-only text is never promoted into the commentary script.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ncds_opus_factory.commands import film_ocr_executor, shenkuo
from ncds_opus_factory.common import capabilities, works_repo

ProgressFn = Callable[[str], None]

VERSION = 3
PROFILE = "commentary_only"
FRAME_SAMPLING_FPS = 0.5
OCR_BACKEND = film_ocr_executor.OCR_BACKEND
TRACK_CLASSIFIER_VERSION = 1
LAYOUT_DISCOVERY_FRAMES = 12
DEFAULT_ROI = film_ocr_executor.DEFAULT_ROI
OCR_SCALE_WIDTH = 1280
OCR_WORKERS = max(1, int(os.environ.get("NOF_FILM_OCR_WORKERS", "4")))

_ROOT = Path(__file__).resolve().parents[3]
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_FILLER_RE = re.compile(
    r"^(?:啊+|嗯+|呃+|额+|哦+|噢+|哎+|唉+|呀+|哇+|嘿+|哈+|哈哈+|呵呵+|哼+|喂+)[!！?？,.，。~～…]*$"
)


def _noop(_text: str) -> None:
    return None


def _artifact_ref(path: Path) -> str:
    try:
        return str(path.relative_to(_ROOT))
    except ValueError:
        return str(path)


def _video_duration_ms(path: Path) -> int:
    import subprocess

    proc = subprocess.run(  # noqa: S603 - argv uses a resolved executable.
        [
            film_ocr_executor._required_binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return max(0, int(round(float(proc.stdout.strip()) * 1000)))


def _video_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _algorithm_signature(roi: dict[str, float]) -> dict[str, Any]:
    return film_ocr_executor.algorithm_signature(
        roi,
        backend=OCR_BACKEND,
        frame_sampling_fps=FRAME_SAMPLING_FPS,
        layout_discovery_frames=LAYOUT_DISCOVERY_FRAMES,
        scale_width=OCR_SCALE_WIDTH,
    )


OCR_ENGINE_FACTORY: Callable[[], Any] = film_ocr_executor._new_ocr_engine
# Compatibility hook for callers that customize local colour classification.
_color_signature = film_ocr_executor.color_signature


def _default_ocr_executor_factory() -> film_ocr_executor.FilmOcrExecutor:
    return film_ocr_executor.build_configured_film_ocr_executor(
        local_ocr_engine_factory=OCR_ENGINE_FACTORY,
        local_color_signature_fn=_color_signature,
        frame_sampling_fps=FRAME_SAMPLING_FPS,
        workers=OCR_WORKERS,
        backend=OCR_BACKEND,
        layout_discovery_frames=LAYOUT_DISCOVERY_FRAMES,
        scale_width=OCR_SCALE_WIDTH,
    )


OCR_EXECUTOR_FACTORY: Callable[[], film_ocr_executor.FilmOcrExecutor] = _default_ocr_executor_factory



def _normalize_text(text: str) -> str:
    return "".join(_CJK_RE.findall(str(text or "")))


def _similarity(left: str, right: str) -> float:
    a = _normalize_text(left)
    b = _normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    return SequenceMatcher(None, a, b).ratio()


def _load_timeline(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("film v3 requires a valid ASR timeline") from exc
    segments = value.get("segments") if isinstance(value, dict) else None
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("film v3 requires non-empty ASR segments")
    return [dict(segment) for segment in segments if isinstance(segment, dict)]


def _ensure_timeline(
    video: Path,
    *,
    platform: str,
    work_id: str,
    provided: str | Path | None,
    on_progress: ProgressFn,
) -> Path:
    if provided is not None:
        path = Path(provided)
        if path.is_file():
            return path
    work_dir = works_repo.work_dir(platform, work_id)
    timeline_path = work_dir / "asr.timeline.json"
    if timeline_path.is_file():
        return timeline_path
    on_progress("Film v3 ASR: transcribing source audio")
    raw, text = capabilities.transcribe(video, on_progress)
    if not isinstance(raw, dict) or not str(text or "").strip():
        raise RuntimeError("film v3 ASR did not produce a transcript")
    para_path = work_dir / "asr.paraformer.json"
    txt_path = work_dir / "asr.txt"
    para_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(str(text).strip(), encoding="utf-8")
    timeline = shenkuo.normalize_asr_timeline(raw)
    timeline_path.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return timeline_path


def _classify_observations(
    observations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    cjk_texts = [_normalize_text(str(row["text"])) for row in observations]
    repeated = Counter(text for text in cjk_texts if text)
    assignments: dict[str, str] = {}
    audit: list[dict[str, Any]] = []
    for observation, normalized in zip(observations, cjk_texts):
        text = str(observation["text"])
        color = str(observation["color_signature"].get("label") or "unknown")
        has_latin = bool(observation.get("has_latin_companion"))
        source_class = "non_chinese"
        reason = "latin_only"
        if normalized:
            if text.startswith("@") or (len(normalized) <= 12 and repeated[normalized] >= 8):
                source_class, reason = "watermark", "repeated_overlay"
            elif len(normalized) < 3 or float(observation["confidence"]) < 0.45:
                source_class, reason = "unknown", "short_or_low_confidence"
            elif color == "yellow" or has_latin:
                source_class, reason = "film_dialogue", "yellow_or_bilingual"
            else:
                source_class, reason = "commentary", "bottom_single_chinese_line"
        observation_id = str(observation["observation_id"])
        assignments[observation_id] = source_class
        audit.append({
            "observation_id": observation_id,
            "source_class": source_class,
            "reason": reason,
        })
    return audit, assignments


def _candidate_reason(segment: dict[str, Any]) -> str | None:
    text = str(segment.get("text") or "").strip()
    normalized = _normalize_text(text)
    if len(normalized) < 3:
        return "short_speech_or_filler"
    if _FILLER_RE.fullmatch(text):
        return "short_speech_or_filler"
    latin_count = len(_LATIN_RE.findall(text))
    if latin_count and len(normalized) / (len(normalized) + latin_count) < 0.70:
        return "non_chinese_source_audio"
    return None


def _build_script(
    segments: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    assignments: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    commentary = [
        row for row in observations
        if assignments.get(str(row["observation_id"])) == "commentary"
    ]
    film_dialogue = [
        row for row in observations
        if assignments.get(str(row["observation_id"])) == "film_dialogue"
    ]
    records: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        reason = _candidate_reason(segment)
        segment_id = str(segment.get("id") or f"seg_{index + 1:04d}")
        start_ms = int(segment.get("start_ms") or 0)
        end_ms = max(start_ms, int(segment.get("end_ms") or start_ms))
        if reason is not None:
            dropped.append({"source_asr_segment_id": segment_id, "reason": reason})
            continue
        nearby_commentary = [
            row for row in commentary
            if start_ms - 750 <= int(row["time_ms"]) <= end_ms + 750
        ]
        nearby_dialogue = [
            row for row in film_dialogue
            if start_ms - 750 <= int(row["time_ms"]) <= end_ms + 750
        ]
        ranked = sorted(
            (
                (_similarity(str(segment.get("text") or ""), str(row["text"])), row)
                for row in nearby_commentary
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best = ranked[0] if ranked else (0.0, None)
        dialogue_score = max(
            (_similarity(str(segment.get("text") or ""), str(row["text"])) for row in nearby_dialogue),
            default=0.0,
        )
        records.append({
            "source_index": index,
            "source_asr_segment_id": segment_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "asr_text": str(segment.get("text") or "").strip(),
            "best_observation": best,
            "support_score": best_score,
            "dialogue_score": dialogue_score,
            "supported": best is not None and best_score >= 0.34,
        })

    supported_source_indexes = {
        int(record["source_index"]) for record in records if record["supported"]
    }
    provisional: list[dict[str, Any]] = []
    for record in records:
        source_index = int(record["source_index"])
        supported = bool(record["supported"])
        previous_supported = source_index - 1 in supported_source_indexes
        next_supported = source_index + 1 in supported_source_indexes
        contextual = not supported and (previous_supported or next_supported)
        if float(record["dialogue_score"]) >= max(0.48, float(record["support_score"]) + 0.12):
            dropped.append({
                "source_asr_segment_id": record["source_asr_segment_id"],
                "reason": "film_dialogue_visual_track",
            })
            continue
        if not supported and not contextual:
            dropped.append({
                "source_asr_segment_id": record["source_asr_segment_id"],
                "reason": "ocr_commentary_track_unverified",
            })
            continue
        best = record["best_observation"]
        text = str(record["asr_text"])
        source_observation_ids: list[str] = []
        ocr_suggestions: list[str] = []
        confidence = 0.64
        review_reasons: list[str] = []
        if best is not None:
            source_observation_ids = [str(best["observation_id"])]
            confidence = min(0.99, 0.55 * float(record["support_score"]) + 0.45 * float(best["confidence"]))
            suggestion = str(best["text"]).strip()
            if (
                float(best["confidence"]) >= 0.86
                and float(record["support_score"]) >= 0.78
                and _normalize_text(suggestion) != _normalize_text(text)
            ):
                # OCR is supporting evidence, never an authority that silently rewrites ASR.
                # A visually plausible conflict remains attached to the cue for human review.
                ocr_suggestions.append(suggestion)
                review_reasons.append("ocr_text_conflict")
        else:
            review_reasons.append("ocr_support_missing_context_kept")
        provisional.append({
            "cue_id": "",
            "start_ms": int(record["start_ms"]),
            "end_ms": int(record["end_ms"]),
            "text": text,
            "source_asr_segment_ids": [str(record["source_asr_segment_id"])],
            "source_observation_ids": source_observation_ids,
            "ocr_suggestions": ocr_suggestions,
            "confidence": round(confidence, 4),
            "decision": "review" if review_reasons else "keep",
            "review_reasons": review_reasons,
        })

    merged: list[dict[str, Any]] = []
    for cue in provisional:
        if merged:
            previous = merged[-1]
            similarity = _similarity(str(previous["text"]), str(cue["text"]))
            gap_ms = int(cue["start_ms"]) - int(previous["end_ms"])
            if gap_ms <= 2_000 and similarity >= 0.84:
                if len(_normalize_text(str(cue["text"]))) > len(_normalize_text(str(previous["text"]))):
                    previous["text"] = cue["text"]
                previous["end_ms"] = max(int(previous["end_ms"]), int(cue["end_ms"]))
                previous["source_asr_segment_ids"].extend(cue["source_asr_segment_ids"])
                previous["source_observation_ids"].extend(cue["source_observation_ids"])
                previous["ocr_suggestions"] = sorted(set(previous["ocr_suggestions"] + cue["ocr_suggestions"]))
                previous["review_reasons"] = sorted(set(previous["review_reasons"] + cue["review_reasons"]))
                previous["confidence"] = round(min(float(previous["confidence"]), float(cue["confidence"])), 4)
                previous["decision"] = "merge"
                continue
        merged.append(dict(cue))
    for index, cue in enumerate(merged, start=1):
        cue["cue_id"] = f"commentary_{index:04d}"

    near_duplicates = sum(
        1
        for left, right in zip(merged, merged[1:])
        if _similarity(str(left["text"]), str(right["text"])) >= 0.84
        and int(right["start_ms"]) - int(left["end_ms"]) <= 2_000
    )
    short_noise = sum(1 for cue in merged if len(_normalize_text(str(cue["text"]))) < 3)
    unsupported = sum(1 for cue in merged if not cue["source_observation_ids"])
    review_ids = [cue["cue_id"] for cue in merged if cue["review_reasons"]]
    support_ratio = (
        sum(1 for cue in merged if cue["source_observation_ids"]) / len(merged)
        if merged else 0.0
    )
    if not merged or support_ratio < 0.30:
        quality_status = "reject"
    elif review_ids or near_duplicates or short_noise or unsupported:
        quality_status = "review"
    else:
        quality_status = "pass"
    report = {
        "version": VERSION,
        "quality_status": quality_status,
        "publishable": quality_status == "pass",
        "asr_segment_count": len(segments),
        "candidate_segment_count": len(records),
        "commentary_cue_count": len(merged),
        "dropped_segment_count": len(dropped),
        "drop_reason_counts": dict(Counter(row["reason"] for row in dropped)),
        "review_cue_ids": review_ids,
        "review_cue_count": len(review_ids),
        "unsupported_cue_count": unsupported,
        "short_noise_count": short_noise,
        "near_duplicate_count": near_duplicates,
        "visual_support_ratio": round(support_ratio, 4),
    }
    return merged, report, dropped


def _srt_timestamp(value_ms: int) -> str:
    hours, remainder = divmod(max(0, int(value_ms)), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _render_srt(cues: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        "\n".join([
            str(index),
            f"{_srt_timestamp(int(cue['start_ms']))} --> {_srt_timestamp(int(cue['end_ms']))}",
            str(cue["text"]),
        ])
        for index, cue in enumerate(cues, start=1)
    ) + ("\n" if cues else "")


def extract_video_subtitles_v3(
    video_path: str | Path,
    *,
    platform: str,
    work_id: str,
    asr_timeline: str | Path | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    video = Path(video_path)
    if not video.is_file():
        raise ValueError(f"film video missing: {video}")
    work_dir = works_repo.work_dir(platform, work_id)
    output_dir = work_dir / "film_subtitles"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "v3.raw_observations.json"
    tracks_path = output_dir / "v3.tracks.json"
    script_path = output_dir / "v3.commentary.json"
    srt_path = output_dir / "v3.commentary.srt"
    txt_path = output_dir / "v3.commentary.txt"
    report_path = output_dir / "v3.commentary.report.json"

    timeline_path = _ensure_timeline(
        video,
        platform=platform,
        work_id=work_id,
        provided=asr_timeline,
        on_progress=on_progress,
    )
    segments = _load_timeline(timeline_path)
    duration_ms = _video_duration_ms(video)
    video_sha256 = _video_sha256(video)

    reusable = False
    observations: list[dict[str, Any]] = []
    roi: dict[str, float]
    ocr_backend = OCR_BACKEND
    algorithm_signature: dict[str, Any] = {}
    execution: dict[str, Any] = {}
    executor_name = "unknown"
    request: dict[str, Any] | None = None
    if raw_path.is_file():
        try:
            raw_doc = json.loads(raw_path.read_text(encoding="utf-8"))
            roi = dict(raw_doc["algorithm_signature"]["roi"])
            reusable = (
                raw_doc.get("version") == VERSION
                and raw_doc.get("video_sha256") == video_sha256
                and raw_doc.get("algorithm_signature") == _algorithm_signature(roi)
                and isinstance(raw_doc.get("observations"), list)
            )
            if reusable:
                observations = [dict(row) for row in raw_doc["observations"]]
                ocr_backend = str(raw_doc.get("backend") or OCR_BACKEND)
                algorithm_signature = dict(raw_doc["algorithm_signature"])
                cached_execution = raw_doc.get("execution")
                execution = (
                    dict(cached_execution)
                    if isinstance(cached_execution, dict)
                    else {"executor": "legacy_local_cache"}
                )
                executor_name = str(execution.get("executor") or "unknown")
                cached_request = raw_doc.get("request")
                request = dict(cached_request) if isinstance(cached_request, dict) else None
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            reusable = False
    if reusable:
        on_progress(
            "Film v3 OCR: "
            f"reusing observations={len(observations)} executor={execution.get('executor', 'unknown')}"
        )
    else:
        job = film_ocr_executor.FilmOcrJob(
            video_path=video,
            video_sha256=video_sha256,
            duration_ms=duration_ms,
            source_size_bytes=video.stat().st_size,
            source_media_type=film_ocr_executor.media_type_for_video(video),
        )
        ocr_result = OCR_EXECUTOR_FACTORY().execute(job, on_progress=on_progress)
        roi = dict(ocr_result.roi)
        observations = [dict(row) for row in ocr_result.observations]
        ocr_backend = ocr_result.backend
        algorithm_signature = dict(ocr_result.algorithm_signature)
        execution = dict(ocr_result.execution)
        executor_name = str(execution.get("executor") or "unknown")
        request = dict(ocr_result.request) if isinstance(ocr_result.request, dict) else None
        observations_sha256 = (
            ocr_result.observations_sha256
            or film_ocr_executor.canonical_observations_sha256(observations)
        )
        raw_doc = {
            "version": VERSION,
            "profile": PROFILE,
            "video": _artifact_ref(video),
            "video_sha256": video_sha256,
            "backend": ocr_backend,
            "executor": executor_name,
            "algorithm_signature": algorithm_signature,
            "execution": execution,
            "observations_sha256": observations_sha256,
            "integrity": {"observations_sha256": observations_sha256},
            "observations": observations,
        }
        if request is not None:
            raw_doc["request"] = request
        raw_path.write_text(json.dumps(raw_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    track_audit, assignments = _classify_observations(observations)
    tracks_doc = {
        "version": VERSION,
        "classifier_version": TRACK_CLASSIFIER_VERSION,
        "assignments": track_audit,
        "source_class_counts": dict(Counter(assignments.values())),
    }
    tracks_path.write_text(json.dumps(tracks_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    cues, report, dropped = _build_script(segments, observations, assignments)
    script_doc = {
        "version": VERSION,
        "profile": PROFILE,
        "language": "zh-CN",
        "quality_status": report["quality_status"],
        "publishable": report["publishable"],
        "cues": cues,
        "discarded": dropped,
    }
    draft_text = "\n".join(str(cue["text"]) for cue in cues) + ("\n" if cues else "")
    script_path.write_text(json.dumps(script_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    srt_path.write_text(_render_srt(cues), encoding="utf-8")
    txt_path.write_text(draft_text, encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    film_source = {
        "mode": "film_script_source",
        "version": VERSION,
        "profile": PROFILE,
        "language": "zh-CN",
        "video": _artifact_ref(video),
        "video_sha256": video_sha256,
        "asr_timeline": _artifact_ref(timeline_path),
        "raw_observations": {
            "json": _artifact_ref(raw_path),
            "count": len(observations),
            "backend": ocr_backend,
            "executor": executor_name,
            "roi": roi,
            "algorithm_signature": algorithm_signature,
            "execution": execution,
        },
        "tracks": {
            "json": _artifact_ref(tracks_path),
            "counts": tracks_doc["source_class_counts"],
        },
        "commentary_script": {
            "json": _artifact_ref(script_path),
            "srt": _artifact_ref(srt_path),
            "txt": _artifact_ref(txt_path),
            "report": _artifact_ref(report_path),
            "cue_count": len(cues),
            "quality_status": report["quality_status"],
            "publishable": report["publishable"],
        },
        "quality_status": report["quality_status"],
        "publishable": report["publishable"],
        "draft_text": draft_text,
    }
    manifest = works_repo.load_manifest(platform, work_id) or {}
    products = dict(manifest.get("products") or {})
    products["film_subtitles"] = dict(film_source["commentary_script"])
    works_repo.merge(platform, work_id, products=products)
    on_progress(
        "Film v3 script: "
        f"status={report['quality_status']} cues={len(cues)} dropped={len(dropped)}"
    )
    return film_source
