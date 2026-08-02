"""Deterministic film subtitle collection for Shenkuo.

The film domain treats burned-in subtitles as the source of truth. Existing
Shenkuo download and ASR artifacts are reused, while OCR is performed from the
cached video at a fixed 2 fps. ASR remains an optional alignment aid for the
semantic Guiguzi stage.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ncds_opus_core.common import cancel

from ncds_opus_factory.commands import shenkuo
from ncds_opus_factory.common import tikhub_client, works_repo
from ncds_opus_factory.common.agy_cli import call_agy
from ncds_opus_factory.common.opus_cli import DEFAULT_OPUS_MODEL, call_opus
from ncds_opus_factory.common.scodex_cli import DEFAULT_CODEX_MODEL, call_scodex

FRAME_SAMPLING_FPS = 2
OCR_BACKEND = "rapidocr-onnxruntime-ppocrv6-small"
ProgressFn = Callable[[str], None]
CleanScriptAgentFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]], ProgressFn],
    tuple[dict[str, dict[str, Any]], str | None, list[str]],
]

CORRECTION_BATCH_SIZE = 60
CORRECTION_TIMEOUT_SECONDS = 1_800
CORRECTION_BACKENDS: tuple[dict[str, str], ...] = (
    {"id": "agy", "model": "gemini-3.5-flash-high"},
    {"id": "scodex", "model": DEFAULT_CODEX_MODEL},
    {"id": "opus", "model": DEFAULT_OPUS_MODEL},
)

_ROOT = Path(__file__).resolve().parents[3]
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _noop(_text: str) -> None:
    return None


def _artifact_ref(path: Path) -> str:
    try:
        return str(path.relative_to(_ROOT))
    except ValueError:
        return str(path)


def _stable_local_id(path: Path) -> str:
    stat = path.stat()
    identity = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    stem = _SAFE_ID_RE.sub("_", path.stem).strip("._-") or "video"
    return f"{stem[:48]}-{digest}"


def _required_binary(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise RuntimeError(f"required executable is unavailable: {name}")
    return binary


def _video_duration_ms(path: Path) -> int:
    proc = subprocess.run(  # noqa: S603 - argv list with a resolved executable.
        [
            _required_binary("ffprobe"),
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
    try:
        return max(0, int(round(float(proc.stdout.strip()) * 1000)))
    except ValueError as exc:
        raise RuntimeError("ffprobe returned an invalid video duration") from exc


def _extract_subtitle_frames(video_path: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Keep the lower-middle band where Douyin commentary subtitles normally
    # live, while excluding the bottom UI/watermark strip.
    video_filter = (
        f"fps={FRAME_SAMPLING_FPS},"
        "crop=iw:floor(ih*0.45):0:floor(ih*0.48)"
    )
    subprocess.run(  # noqa: S603 - argv list with a resolved executable.
        [
            _required_binary("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vf",
            video_filter,
            "-q:v",
            "3",
            str(output_dir / "frame_%08d.jpg"),
        ],
        check=True,
    )
    return sorted(output_dir.glob("frame_*.jpg"))


def _new_ocr_engine() -> Any:
    try:
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import EngineType, ModelType, OCRVersion
    except ImportError as exc:
        raise RuntimeError(
            "film subtitle OCR requires rapidocr>=3.9.0 and onnxruntime"
        ) from exc
    return RapidOCR(
        params={
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.lang_type": "ch",
            "Det.model_type": ModelType.SMALL,
            "Det.ocr_version": OCRVersion.PPOCRV6,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": "ch",
            "Rec.model_type": ModelType.SMALL,
            "Rec.ocr_version": OCRVersion.PPOCRV6,
        }
    )


# Dependency seam for bounded integration tests and alternate packaged models.
OCR_ENGINE_FACTORY: Callable[[], Any] = _new_ocr_engine


def _ocr_lines(result: Any) -> tuple[list[str], list[float]]:
    """Normalize the RapidOCR v3 result object into ordered text lines."""
    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    boxes = getattr(result, "boxes", None)
    if txts is None and isinstance(result, tuple) and result:
        rows = result[0] if isinstance(result[0], list) else []
        txts = [
            str(row[1])
            for row in rows
            if isinstance(row, (list, tuple)) and len(row) >= 2
        ]
        scores = [
            float(row[2])
            for row in rows
            if isinstance(row, (list, tuple)) and len(row) >= 3
        ]
        boxes = [
            row[0]
            for row in rows
            if isinstance(row, (list, tuple)) and len(row) >= 1
        ]
    text_values = [str(value or "").strip() for value in (txts or [])]
    score_values = [
        max(0.0, min(1.0, float(value)))
        for value in (scores or [])
    ]
    if len(score_values) < len(text_values):
        score_values.extend([0.0] * (len(text_values) - len(score_values)))

    indexes = list(range(len(text_values)))
    if boxes is not None:
        try:
            indexes.sort(
                key=lambda index: (
                    min(float(point[1]) for point in boxes[index]),
                    min(float(point[0]) for point in boxes[index]),
                )
            )
        except (IndexError, TypeError, ValueError):
            pass
    lines: list[str] = []
    confidences: list[float] = []
    for index in indexes:
        text = re.sub(r"\s+", "", text_values[index])
        if not text or not _CJK_RE.search(text):
            continue
        lines.append(text)
        confidences.append(score_values[index])
    return lines, confidences


def _ocr_frame(engine: Any, frame_path: Path) -> tuple[str, float]:
    result = engine(str(frame_path), use_cls=False)
    lines, confidences = _ocr_lines(result)
    if not lines:
        return "", 0.0
    return "".join(lines), (
        sum(confidences) / len(confidences) if confidences else 0.0
    )


def _text_key(text: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()《》【】\[\]-]", "", text)


def _same_subtitle(left: str, right: str) -> bool:
    a = _text_key(left)
    b = _text_key(right)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.72


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """Accept the narrow JSON-array contract shared by script cleaners."""
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("["), value.rfind("]")
        if start < 0 or end <= start:
            raise RuntimeError("film script cleaner returned invalid JSON")
        decoded = json.loads(value[start:end + 1])
    if not isinstance(decoded, list) or not all(isinstance(row, dict) for row in decoded):
        raise RuntimeError("film script cleaner must return a JSON array")
    return [dict(row) for row in decoded]


def _asr_context(cue: dict[str, Any], timeline: list[dict[str, Any]]) -> str:
    start_ms = int(cue.get("start_ms") or 0)
    end_ms = max(start_ms, int(cue.get("end_ms") or start_ms))
    parts: list[tuple[int, str]] = []
    for segment in timeline:
        segment_start = int(segment.get("start_ms") or 0)
        segment_end = max(segment_start, int(segment.get("end_ms") or segment_start))
        if min(end_ms, segment_end) > max(start_ms, segment_start):
            text = str(segment.get("text") or "").strip()
            if text:
                parts.append((segment_start, text))
    return "".join(text for _start, text in sorted(parts))


def _cleaner_prompt(batch: list[dict[str, Any]], timeline: list[dict[str, Any]]) -> str:
    rows = [
        {
            "cue_id": cue["cue_id"],
            "ocr_text": cue["text"],
            "asr_hint": _asr_context(cue, timeline),
        }
        for cue in batch
    ]
    return "\n".join([
        "Proofread Chinese burned-in film commentary subtitles.",
        "Return only a JSON array. Every input cue exactly once, in the same order.",
        "Each item must contain cue_id, text, confidence (0..1).",
        "text must be clean zh-CN Chinese. Correct OCR typos, homophones and punctuation only.",
        "OCR is the evidence; ASR is optional context. Do not translate, summarize, classify, merge, split, or invent facts.",
        json.dumps(rows, ensure_ascii=False),
    ])


def _call_cleaner_backend(backend: str, prompt: str, model: str) -> str:
    if backend == "agy":
        return call_agy(prompt, model=model, timeout_seconds=CORRECTION_TIMEOUT_SECONDS)
    if backend == "scodex":
        return call_scodex(prompt, model=model, timeout_seconds=CORRECTION_TIMEOUT_SECONDS)
    if backend == "opus":
        return call_opus(prompt, model=model, timeout_seconds=CORRECTION_TIMEOUT_SECONDS)
    raise RuntimeError(f"unknown film script cleaner backend: {backend}")


def _validate_cleaner_rows(
    rows: list[dict[str, Any]], batch: list[dict[str, Any]], *, batch_no: int
) -> dict[str, dict[str, Any]]:
    expected = [str(cue["cue_id"]) for cue in batch]
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        cue_id = str(row.get("cue_id") or "").strip()
        text = str(row.get("text") or "").strip()
        if not cue_id or not text or cue_id in result:
            raise RuntimeError(f"film script cleaner contract mismatch at batch={batch_no}")
        try:
            confidence = float(row.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.6
        result[cue_id] = {
            "text": text,
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
        }
    if list(result) != expected:
        raise RuntimeError(f"film script cleaner cue coverage mismatch at batch={batch_no}")
    return result


def _clean_with_backend(
    raw_cues: list[dict[str, Any]], timeline: list[dict[str, Any]], on_progress: ProgressFn,
    *, backend: str, model: str,
) -> dict[str, dict[str, Any]]:
    corrected: dict[str, dict[str, Any]] = {}
    total = (len(raw_cues) + CORRECTION_BATCH_SIZE - 1) // CORRECTION_BATCH_SIZE
    for offset in range(0, len(raw_cues), CORRECTION_BATCH_SIZE):
        cancel.checkpoint()
        batch = raw_cues[offset:offset + CORRECTION_BATCH_SIZE]
        batch_no = offset // CORRECTION_BATCH_SIZE + 1
        on_progress(f"Film script cleaner: backend={backend} batch={batch_no}/{total}")
        rows = _parse_json_array(_call_cleaner_backend(backend, _cleaner_prompt(batch, timeline), model))
        corrected.update(_validate_cleaner_rows(rows, batch, batch_no=batch_no))
    if set(corrected) != {str(cue["cue_id"]) for cue in raw_cues}:
        raise RuntimeError("film script cleaner did not cover all OCR cues")
    return corrected


def _clean_script_with_fallback(
    raw_cues: list[dict[str, Any]], timeline: list[dict[str, Any]], on_progress: ProgressFn,
) -> tuple[dict[str, dict[str, Any]], str | None, list[str]]:
    """Use one backend for every batch; failures restart at batch one."""
    failures: list[str] = []
    for candidate in CORRECTION_BACKENDS:
        backend, model = candidate["id"], candidate["model"]
        try:
            on_progress(f"Film script cleaner: starting {backend}")
            return _clean_with_backend(raw_cues, timeline, on_progress, backend=backend, model=model), backend, failures
        except cancel.TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - bounded backend fallback.
            failures.append(f"{backend}:{type(exc).__name__}")
            on_progress(f"Film script cleaner: {backend} unavailable; trying next backend")
    return {}, None, failures


# Public seam: callers/tests may replace it with a local correction implementation.
CLEAN_SCRIPT_AGENT: CleanScriptAgentFn = _clean_script_with_fallback


def _consensus_cues(
    observations: list[dict[str, Any]],
    *,
    duration_ms: int,
) -> list[dict[str, Any]]:
    frame_ms = int(round(1000 / FRAME_SAMPLING_FPS))
    groups: list[list[dict[str, Any]]] = []
    active: list[dict[str, Any]] = []
    for observation in observations:
        if not observation["text"]:
            if active:
                groups.append(active)
                active = []
            continue
        if (
            active
            and observation["time_ms"] - active[-1]["time_ms"] <= frame_ms * 2
            and _same_subtitle(active[-1]["text"], observation["text"])
        ):
            active.append(observation)
        else:
            if active:
                groups.append(active)
            active = [observation]
    if active:
        groups.append(active)

    draft: list[dict[str, Any]] = []
    for samples in groups:
        representative = max(
            samples,
            key=lambda item: (
                len(_text_key(str(item["text"]))),
                float(item["confidence"]),
            ),
        )
        confidence = sum(float(item["confidence"]) for item in samples) / len(samples)
        start_ms = int(samples[0]["time_ms"])
        end_ms = min(duration_ms, int(samples[-1]["time_ms"]) + frame_ms)
        if end_ms <= start_ms:
            end_ms = start_ms + frame_ms
        draft.append({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": str(representative["text"]),
            "confidence": round(confidence, 4),
            "sample_count": len(samples),
        })

    merged: list[dict[str, Any]] = []
    for cue in draft:
        if (
            merged
            and _same_subtitle(
                str(merged[-1]["text"]),
                str(cue["text"]),
            )
            and int(cue["start_ms"]) - int(merged[-1]["end_ms"]) <= frame_ms * 2
        ):
            previous = merged[-1]
            total = int(previous["sample_count"]) + int(cue["sample_count"])
            previous["confidence"] = round(
                (
                    float(previous["confidence"]) * int(previous["sample_count"])
                    + float(cue["confidence"]) * int(cue["sample_count"])
                )
                / total,
                4,
            )
            previous["sample_count"] = total
            previous["end_ms"] = cue["end_ms"]
            if len(_text_key(str(cue["text"]))) > len(_text_key(str(previous["text"]))):
                previous["text"] = cue["text"]
            continue
        merged.append(cue)

    return [
        {"cue_id": f"cue_{index:04d}", **cue}
        for index, cue in enumerate(merged, start=1)
    ]


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


def _temporal_merge_clean_cues(
    cues: list[dict[str, Any]],
    *,
    frame_sampling_fps: int = FRAME_SAMPLING_FPS,
) -> list[dict[str, Any]]:
    """Merge post-correction adjacent repeats without mutating raw OCR cues."""
    max_gap_ms = int(round(2_000 / max(1, frame_sampling_fps)))
    merged: list[dict[str, Any]] = []
    for cue in cues:
        if (
            merged
            and _text_key(str(merged[-1]["text"])) == _text_key(str(cue["text"]))
            and int(cue["start_ms"]) - int(merged[-1]["end_ms"]) <= max_gap_ms
        ):
            previous = merged[-1]
            previous["end_ms"] = max(int(previous["end_ms"]), int(cue["end_ms"]))
            previous["source_cue_ids"].extend(cue["source_cue_ids"])
            previous["confidence"] = round(
                min(float(previous["confidence"]), float(cue["confidence"])), 4
            )
            previous["needs_review"] = bool(
                previous["needs_review"] or cue["needs_review"]
            )
            continue
        merged.append(dict(cue))
    for index, cue in enumerate(merged, start=1):
        cue["cue_id"] = f"clean_{index:04d}"
    return merged


def _build_clean_cues(
    raw_cues: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    on_progress: ProgressFn,
) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    corrected, backend, failures = CLEAN_SCRIPT_AGENT(
        [dict(cue) for cue in raw_cues], timeline, on_progress
    )
    needs_review = backend is None
    provisional: list[dict[str, Any]] = []
    for raw_cue in raw_cues:
        cue_id = str(raw_cue["cue_id"])
        corrected_row = corrected.get(cue_id, {})
        candidate_text = str(corrected_row.get("text") or "").strip()
        invalid_correction = bool(corrected_row) and not _CJK_RE.search(candidate_text)
        text = candidate_text or str(raw_cue["text"]).strip()
        if invalid_correction:
            text = str(raw_cue["text"]).strip()
        confidence = min(
            float(raw_cue.get("confidence") or 0.0),
            float(corrected_row.get("confidence") or 0.55),
        )
        provisional.append({
            "cue_id": cue_id,
            "start_ms": int(raw_cue["start_ms"]),
            "end_ms": int(raw_cue["end_ms"]),
            "text": text,
            "source_cue_ids": [cue_id],
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "needs_review": needs_review or invalid_correction or confidence < 0.75,
        })
    return _temporal_merge_clean_cues(provisional), backend, failures


def _resolve_optional_timeline(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    return path if path.is_file() else None


def extract_video_subtitles(
    video_path: str | Path,
    *,
    platform: str,
    work_id: str,
    asr_timeline: str | Path | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Run film OCR and deliver the v2 clean Chinese script from Shenkuo."""
    video = Path(video_path)
    if not video.is_file():
        raise ValueError(f"film video missing: {video}")
    work_dir = works_repo.work_dir(platform, work_id)
    output_dir = work_dir / "film_subtitles"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "raw.json"
    raw_srt_path = output_dir / "raw.srt"
    raw_txt_path = output_dir / "raw.txt"
    raw_report_path = output_dir / "raw.report.json"
    clean_path = output_dir / "clean.json"
    clean_srt_path = output_dir / "clean.srt"
    clean_txt_path = output_dir / "clean.txt"
    clean_report_path = output_dir / "clean.report.json"
    timeline_path = _resolve_optional_timeline(asr_timeline)

    on_progress("Film OCR: probing video")
    duration_ms = _video_duration_ms(video)
    with tempfile.TemporaryDirectory(prefix="nof-film-ocr-") as tmp:
        frame_dir = Path(tmp)
        on_progress(f"Film OCR: extracting frames at {FRAME_SAMPLING_FPS} fps")
        frames = _extract_subtitle_frames(video, frame_dir)
        if not frames:
            raise RuntimeError("film subtitle frame extraction produced no frames")
        engine = OCR_ENGINE_FACTORY()
        observations: list[dict[str, Any]] = []
        for index, frame_path in enumerate(frames):
            cancel.checkpoint()
            text, confidence = _ocr_frame(engine, frame_path)
            observations.append({
                "time_ms": int(round(index * 1000 / FRAME_SAMPLING_FPS)),
                "text": text,
                "confidence": round(confidence, 4),
            })
            if index == 0 or (index + 1) % 50 == 0 or index + 1 == len(frames):
                on_progress(
                    f"Film OCR: frames {index + 1}/{len(frames)}"
                )

    cues = _consensus_cues(observations, duration_ms=duration_ms)
    if not cues:
        raise RuntimeError("film subtitle OCR found no Chinese subtitle cues")
    raw_doc = {
        "version": 1,
        "source_work_id": work_id,
        "platform": platform,
        "video": _artifact_ref(video),
        "backend": OCR_BACKEND,
        "frame_sampling_fps": FRAME_SAMPLING_FPS,
        "cues": cues,
    }
    raw_report = {
        "version": 1,
        "backend": OCR_BACKEND,
        "frame_sampling_fps": FRAME_SAMPLING_FPS,
        "crop": {
            "x": 0.0,
            "y": 0.48,
            "width": 1.0,
            "height": 0.45,
        },
        "duration_ms": duration_ms,
        "sampled_frames": len(observations),
        "frames_with_chinese_text": sum(
            1 for observation in observations if observation["text"]
        ),
        "cue_count": len(cues),
        "low_confidence_cues": sum(
            1 for cue in cues if float(cue["confidence"]) < 0.75
        ),
    }
    raw_path.write_text(
        json.dumps(raw_doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    raw_srt_path.write_text(_render_srt(cues), encoding="utf-8")
    raw_txt_path.write_text(
        "\n".join(str(cue["text"]) for cue in cues) + "\n",
        encoding="utf-8",
    )
    raw_report_path.write_text(
        json.dumps(raw_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    timeline = []
    if timeline_path is not None:
        try:
            candidate = json.loads(timeline_path.read_text(encoding="utf-8"))
            timeline = [row for row in candidate.get("segments", []) if isinstance(row, dict)]
        except (OSError, json.JSONDecodeError):
            on_progress("Film script cleaner: ASR context unavailable; using OCR only")
    on_progress("Film script cleaner: correcting Chinese OCR")
    clean_cues, correction_backend, correction_failures = _build_clean_cues(
        cues, timeline, on_progress
    )
    clean_needs_review = any(bool(cue["needs_review"]) for cue in clean_cues)
    clean_doc = {
        "version": 2,
        "language": "zh-CN",
        "cues": [
            {key: value for key, value in cue.items() if key != "needs_review"}
            for cue in clean_cues
        ],
    }
    clean_report = {
        "version": 2,
        "correction_backend": correction_backend,
        "correction_failures": correction_failures,
        "raw_cue_count": len(cues),
        "clean_cue_count": len(clean_cues),
        "needs_review": clean_needs_review,
        "review_cue_ids": [cue["cue_id"] for cue in clean_cues if cue["needs_review"]],
    }
    clean_path.write_text(json.dumps(clean_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    clean_srt_path.write_text(_render_srt(clean_cues), encoding="utf-8")
    clean_txt_path.write_text(
        "\n".join(str(cue["text"]) for cue in clean_cues) + "\n", encoding="utf-8"
    )
    clean_report_path.write_text(
        json.dumps(clean_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    film_source = {
        "mode": "film_script_source",
        "version": 2,
        "language": "zh-CN",
        "video": _artifact_ref(video),
        "raw_ocr": {
            "backend": OCR_BACKEND,
            "json": _artifact_ref(raw_path),
            "srt": _artifact_ref(raw_srt_path),
            "txt": _artifact_ref(raw_txt_path),
            "report": _artifact_ref(raw_report_path),
            "cue_count": len(cues),
            "frame_sampling_fps": FRAME_SAMPLING_FPS,
        },
        "clean_script": {
            "json": _artifact_ref(clean_path),
            "srt": _artifact_ref(clean_srt_path),
            "txt": _artifact_ref(clean_txt_path),
            "report": _artifact_ref(clean_report_path),
            "cue_count": len(clean_cues),
            "needs_review": clean_needs_review,
        },
        "asr_timeline": (
            _artifact_ref(timeline_path) if timeline_path is not None else None
        ),
    }
    manifest = works_repo.load_manifest(platform, work_id) or {}
    products = dict(manifest.get("products") or {})
    products["film_subtitles"] = dict(film_source["clean_script"])
    works_repo.merge(platform, work_id, products=products)
    on_progress(f"Film script: done raw={len(cues)} clean={len(clean_cues)}")
    return film_source


def _clean_script_text(film_source: dict[str, Any]) -> str:
    clean = film_source.get("clean_script")
    if not isinstance(clean, dict):
        return ""
    value = clean.get("txt")
    if not isinstance(value, str) or not value.strip():
        return ""
    path = Path(value)
    if not path.is_absolute():
        path = _ROOT / path
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _local_timeline(source: Path) -> Path | None:
    for candidate in (
        source.with_suffix(".asr.timeline.json"),
        source.parent / "asr.timeline.json",
    ):
        if candidate.is_file():
            return candidate
    return None


def _collect_local_video(
    source: Path,
    *,
    on_progress: ProgressFn,
) -> dict[str, Any]:
    work_id = _stable_local_id(source)
    platform = "local"
    work_dir = works_repo.work_dir(platform, work_id)
    cached_video = work_dir / "video.mp4"
    if not cached_video.is_file() or cached_video.stat().st_size != source.stat().st_size:
        shutil.copy2(source, cached_video)
    film_source = extract_video_subtitles(
        cached_video,
        platform=platform,
        work_id=work_id,
        asr_timeline=_local_timeline(source),
        on_progress=on_progress,
    )
    return {
        "platform": platform,
        "aweme_id": work_id,
        "video": _artifact_ref(cached_video),
        "status": {"download": "local", "transcribe": "skipped"},
        "film_source": film_source,
        "text": _clean_script_text(film_source),
    }


def collect_film_subtitles(
    job_dir: str | Path,
    urls: list[str],
    shares: list[dict[str, Any]] | None = None,
    *,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Collect film subtitle sources for legacy and engine production paths."""
    root = Path(job_dir)
    collect_dir = root / "01_collect"
    collect_dir.mkdir(parents=True, exist_ok=True)
    if not urls:
        raise ValueError("urls is empty; need at least one film video")
    shares_by_url = {
        str(share["url"]): share
        for share in (shares or [])
        if isinstance(share, dict) and isinstance(share.get("url"), str)
    }

    collected: list[dict[str, Any]] = []
    for index, value in enumerate(urls, start=1):
        on_progress(f"Film source {index}/{len(urls)}: resolving")
        try:
            local_path = Path(value).expanduser()
            if local_path.is_file():
                entry = _collect_local_video(
                    local_path.resolve(),
                    on_progress=on_progress,
                )
            else:
                ref = tikhub_client.resolve_video_ref(value)
                if ref is None:
                    raise ValueError(f"unsupported film source: {value}")
                meta: dict[str, Any] = {}
                if ref.platform == "douyin":
                    try:
                        meta = tikhub_client.extract_meta(
                            tikhub_client.fetch_one_video_detail(ref.video_id)
                        )
                    except Exception as exc:  # noqa: BLE001
                        on_progress(
                            "Film source metadata unavailable; "
                            f"continuing: {type(exc).__name__}"
                        )
                else:
                    try:
                        meta = tikhub_client.fetch_video_ref_meta(ref)
                    except Exception as exc:  # noqa: BLE001
                        on_progress(
                            "Film source metadata unavailable; "
                            f"continuing: {type(exc).__name__}"
                        )
                        meta = tikhub_client.video_ref_meta(ref)
                share = shares_by_url.get(value) or {}
                if share.get("title") and not meta.get("desc"):
                    meta["desc"] = str(share["title"])
                if share.get("author") and not meta.get("author"):
                    meta["author"] = str(share["author"])
                entry = shenkuo.collect_one(
                    ref.video_id,
                    collect_dir,
                    meta=meta,
                    on_progress=on_progress,
                    top_comments=0,
                    do_audio=False,
                    do_frames=False,
                    platform=ref.platform,
                    source_url=ref.url,
                )
                work_dir = works_repo.work_dir(ref.platform, ref.video_id)
                timeline = work_dir / "asr.timeline.json"
                entry["film_source"] = extract_video_subtitles(
                    work_dir / "video.mp4",
                    platform=ref.platform,
                    work_id=ref.video_id,
                    asr_timeline=timeline if timeline.is_file() else None,
                    on_progress=on_progress,
                )
                entry["text"] = _clean_script_text(entry["film_source"])
            entry["index"] = index
            entry["url"] = value
            collected.append(entry)
        except cancel.TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            on_progress(
                f"Film source {index}/{len(urls)} failed: "
                f"{type(exc).__name__}: {exc}"
            )
            collected.append({
                "index": index,
                "url": value,
                "status": {},
                "error": f"{type(exc).__name__}: {exc}",
            })

    succeeded = [
        entry
        for entry in collected
        if isinstance(entry.get("film_source"), dict) and not entry.get("error")
    ]
    if not succeeded:
        raise RuntimeError(
            f"all {len(urls)} film sources failed subtitle collection"
        )
    return {
        "collected": collected,
        "items": collected,
        "collect_dir": str(collect_dir),
    }
