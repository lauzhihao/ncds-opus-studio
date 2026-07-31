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

FRAME_SAMPLING_FPS = 2
OCR_BACKEND = "rapidocr-onnxruntime-ppocrv6-small"
ProgressFn = Callable[[str], None]

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
            and _text_key(str(merged[-1]["text"])) == _text_key(str(cue["text"]))
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
    """Run real OCR for one cached video and persist the film_source artifacts."""
    video = Path(video_path)
    if not video.is_file():
        raise ValueError(f"film video missing: {video}")
    work_dir = works_repo.work_dir(platform, work_id)
    output_dir = work_dir / "film_subtitles"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "raw.json"
    srt_path = output_dir / "raw.srt"
    txt_path = output_dir / "raw.txt"
    report_path = output_dir / "report.json"
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
    report = {
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
    srt_path.write_text(_render_srt(cues), encoding="utf-8")
    txt_path.write_text(
        "\n".join(str(cue["text"]) for cue in cues) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    film_source = {
        "mode": "film_subtitle_source",
        "version": 1,
        "video": _artifact_ref(video),
        "ocr": {
            "backend": OCR_BACKEND,
            "raw_cues": _artifact_ref(raw_path),
            "srt": _artifact_ref(srt_path),
            "txt": _artifact_ref(txt_path),
            "report": _artifact_ref(report_path),
            "cue_count": len(cues),
            "frame_sampling_fps": FRAME_SAMPLING_FPS,
        },
        "asr_timeline": (
            _artifact_ref(timeline_path) if timeline_path is not None else None
        ),
    }
    manifest = works_repo.load_manifest(platform, work_id) or {}
    products = dict(manifest.get("products") or {})
    products["film_subtitles"] = dict(film_source["ocr"])
    works_repo.merge(platform, work_id, products=products)
    on_progress(f"Film OCR: done cues={len(cues)}")
    return film_source


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
