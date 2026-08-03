"""Portable executor boundary for the film v3 OCR observation stage.

The film script workflow owns ASR candidates and artifact composition.  This
module owns the complete ``video -> ROI + observations`` operation so it can
run locally today or be handed to an explicitly configured middleware later.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Protocol

from ncds_opus_core.common import cancel

ProgressFn = Callable[[str], None]

FILM_OCR_REQUEST_SCHEMA_VERSION = "film_ocr_request.v1"
FILM_OCR_RESULT_SCHEMA_VERSION = "film_ocr_result.v1"
FILM_OCR_OPERATION = "film_ocr"

VERSION = 3
PROFILE = "commentary_only"
FRAME_SAMPLING_FPS = 0.5
OCR_BACKEND = "rapidocr-onnxruntime-ppocrv6-tiny"
LAYOUT_DISCOVERY_FRAMES = 12
DEFAULT_ROI = {"x": 0.0, "y": 0.70, "width": 1.0, "height": 0.29}
OCR_SCALE_WIDTH = 1280

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _noop(_text: str) -> None:
    return None


@dataclass(frozen=True)
class FilmOcrJob:
    """Local source identity needed by an OCR executor.

    ``video_path`` is deliberately an executor-only value.  It is never part
    of the versioned remote request payload.
    """

    video_path: Path
    video_sha256: str
    duration_ms: int
    source_size_bytes: int = 0
    source_media_type: str = "application/octet-stream"


@dataclass
class FilmOcrResult:
    roi: dict[str, float]
    observations: list[dict[str, Any]]
    backend: str
    algorithm_signature: dict[str, Any]
    execution: dict[str, Any] = field(default_factory=dict)
    request: dict[str, Any] | None = None
    observations_sha256: str | None = None


class FilmOcrExecutor(Protocol):
    def execute(
        self,
        job: FilmOcrJob,
        *,
        on_progress: ProgressFn = _noop,
    ) -> FilmOcrResult: ...


class FilmOcrMiddlewareTransport(Protocol):
    """Transport seam; source upload is out-of-band from the public request."""

    def submit(
        self,
        *,
        request: dict[str, Any],
        source_path: Path,
        on_progress: ProgressFn | None = None,
    ) -> dict[str, Any]: ...


def canonical_observations_sha256(observations: list[dict[str, Any]]) -> str:
    """Return the stable digest used to detect remote observation corruption."""
    serialized = json.dumps(
        observations,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def algorithm_signature(
    roi: Mapping[str, float],
    *,
    backend: str = OCR_BACKEND,
    frame_sampling_fps: float = FRAME_SAMPLING_FPS,
    layout_discovery_frames: int = LAYOUT_DISCOVERY_FRAMES,
    scale_width: int = OCR_SCALE_WIDTH,
) -> dict[str, Any]:
    """Describe OCR semantics only, never a host or transport implementation."""
    return {
        "version": VERSION,
        "profile": PROFILE,
        "backend": backend,
        "frame_sampling_fps": frame_sampling_fps,
        "layout_discovery_frames": layout_discovery_frames,
        "roi": {key: round(float(value), 4) for key, value in roi.items()},
        "scale_width": scale_width,
    }


def algorithm_settings(
    *,
    backend: str = OCR_BACKEND,
    frame_sampling_fps: float = FRAME_SAMPLING_FPS,
    layout_discovery_frames: int = LAYOUT_DISCOVERY_FRAMES,
    scale_width: int = OCR_SCALE_WIDTH,
) -> dict[str, Any]:
    """The ROI-independent settings included in a portable remote request."""
    signature = algorithm_signature(
        DEFAULT_ROI,
        backend=backend,
        frame_sampling_fps=frame_sampling_fps,
        layout_discovery_frames=layout_discovery_frames,
        scale_width=scale_width,
    )
    signature.pop("roi")
    return signature


def _request_id(job: FilmOcrJob, settings: Mapping[str, Any]) -> str:
    identity = {
        "operation": FILM_OCR_OPERATION,
        "source": {
            "sha256": job.video_sha256,
            "byte_size": job.source_size_bytes,
            "media_type": job.source_media_type,
        },
        "algorithm_signature": dict(settings),
    }
    stable = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"film-ocr-{hashlib.sha256(stable.encode('utf-8')).hexdigest()}"


def build_remote_request(job: FilmOcrJob, *, settings: Mapping[str, Any]) -> dict[str, Any]:
    """Build the versioned public request without exposing a local source path."""
    request_id = _request_id(job, settings)
    return {
        "schema_version": FILM_OCR_REQUEST_SCHEMA_VERSION,
        "operation": FILM_OCR_OPERATION,
        "request_id": request_id,
        "idempotency_key": request_id,
        "source": {
            "sha256": job.video_sha256,
            "byte_size": job.source_size_bytes,
            "media_type": job.source_media_type,
            "duration_ms": job.duration_ms,
        },
        "algorithm_signature": dict(settings),
    }


def media_type_for_video(path: Path) -> str:
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
    }.get(path.suffix.lower(), "application/octet-stream")


def required_binary(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise RuntimeError(f"required executable is unavailable: {name}")
    return binary


def _required_binary(name: str) -> str:
    """Backward-compatible private alias for the local executor internals."""
    return required_binary(name)


def _validated_observations(observations: list[Any]) -> list[dict[str, Any]]:
    """Reject malformed remote rows before they become a durable artifact."""
    validated: list[dict[str, Any]] = []
    observation_ids: set[str] = set()
    for row in observations:
        if not isinstance(row, Mapping):
            raise RuntimeError("film OCR middleware observations contain an invalid row")
        observation_id = row.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id or observation_id in observation_ids:
            raise RuntimeError("film OCR middleware observation identity is invalid")
        observation_ids.add(observation_id)
        integer_values: dict[str, int] = {}
        for key in ("frame_index", "time_ms", "line_order"):
            value = row.get(key)
            if isinstance(value, bool):
                raise RuntimeError(f"film OCR middleware observation {key} is invalid")
            try:
                integer_values[key] = int(value)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"film OCR middleware observation {key} is invalid") from exc
            if integer_values[key] < 0:
                raise RuntimeError(f"film OCR middleware observation {key} is invalid")
        if not isinstance(row.get("text"), str):
            raise RuntimeError("film OCR middleware observation text is invalid")
        try:
            confidence = float(row.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("film OCR middleware observation confidence is invalid") from exc
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise RuntimeError("film OCR middleware observation confidence is invalid")
        bbox = row.get("bbox_norm")
        if not isinstance(bbox, Mapping):
            raise RuntimeError("film OCR middleware observation bbox is invalid")
        try:
            normalized_bbox = {key: float(bbox[key]) for key in ("x", "y", "width", "height")}
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("film OCR middleware observation bbox is invalid") from exc
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in normalized_bbox.values()):
            raise RuntimeError("film OCR middleware observation bbox is invalid")
        if normalized_bbox["x"] + normalized_bbox["width"] > 1.0 or normalized_bbox["y"] + normalized_bbox["height"] > 1.0:
            raise RuntimeError("film OCR middleware observation bbox exceeds normalized bounds")
        if not isinstance(row.get("color_signature"), Mapping):
            raise RuntimeError("film OCR middleware observation colour signature is invalid")
        validated.append(dict(row))
    return validated


def _new_ocr_engine() -> Any:
    try:
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import EngineType, ModelType, OCRVersion
    except ImportError as exc:
        raise RuntimeError("film commentary OCR requires rapidocr>=3.9.0 and onnxruntime") from exc
    return RapidOCR(
        params={
            "EngineConfig.onnxruntime.intra_op_num_threads": 1,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.lang_type": "ch",
            "Det.model_type": ModelType.TINY,
            "Det.ocr_version": OCRVersion.PPOCRV6,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": "ch",
            "Rec.model_type": ModelType.TINY,
            "Rec.ocr_version": OCRVersion.PPOCRV6,
        }
    )


def _result_rows(result: Any) -> list[dict[str, Any]]:
    def serializable_polygon(value: Any) -> list[list[float]]:
        if hasattr(value, "tolist"):
            value = value.tolist()
        try:
            return [[float(point[0]), float(point[1])] for point in value]
        except (TypeError, ValueError, IndexError):
            return []

    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    boxes = getattr(result, "boxes", None)
    if txts is None and isinstance(result, tuple) and result:
        source_rows = result[0] if isinstance(result[0], list) else []
        return [
            {
                "text": str(row[1] or "").strip(),
                "confidence": float(row[2]) if len(row) >= 3 else 0.0,
                "polygon": serializable_polygon(row[0] if row else []),
            }
            for row in source_rows
            if isinstance(row, (list, tuple)) and len(row) >= 2
        ]
    texts = [str(value or "").strip() for value in (txts if txts is not None else [])]
    score_values = [float(value) for value in (scores if scores is not None else [])]
    box_values = list(boxes if boxes is not None else [])
    return [
        {
            "text": text,
            "confidence": score_values[index] if index < len(score_values) else 0.0,
            "polygon": serializable_polygon(box_values[index] if index < len(box_values) else []),
        }
        for index, text in enumerate(texts)
        if text
    ]


def _bbox_from_polygon(polygon: Any) -> tuple[float, float, float, float] | None:
    try:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except (ImportError, OSError) as exc:
        raise RuntimeError(f"cannot inspect OCR frame: {path.name}") from exc


def color_signature(path: Path, bbox_px: tuple[float, float, float, float]) -> dict[str, Any]:
    try:
        from PIL import Image

        with Image.open(path) as source:
            image = source.convert("RGB")
            left, top, right, bottom = bbox_px
            crop = image.crop(
                (
                    max(0, int(math.floor(left))),
                    max(0, int(math.floor(top))),
                    min(image.width, int(math.ceil(right))),
                    min(image.height, int(math.ceil(bottom))),
                )
            )
            pixels = list(crop.getdata())
    except (ImportError, OSError):
        return {"label": "unknown", "yellow_ratio": 0.0, "white_ratio": 0.0}
    bright = [pixel for pixel in pixels if max(pixel) >= 140]
    if not bright:
        return {"label": "unknown", "yellow_ratio": 0.0, "white_ratio": 0.0}
    yellow = sum(1 for red, green, blue in bright if red >= 150 and green >= 95 and blue <= 125)
    white = sum(
        1
        for red, green, blue in bright
        if min(red, green, blue) >= 165 and max(red, green, blue) - min(red, green, blue) <= 55
    )
    yellow_ratio = yellow / len(bright)
    white_ratio = white / len(bright)
    label = "unknown"
    if yellow_ratio >= 0.12 and yellow_ratio > white_ratio * 1.15:
        label = "yellow"
    elif white_ratio >= 0.12:
        label = "white"
    return {"label": label, "yellow_ratio": round(yellow_ratio, 4), "white_ratio": round(white_ratio, 4)}


class LocalFilmOcrExecutor:
    """The current ffmpeg + RapidOCR implementation behind the executor seam."""

    def __init__(
        self,
        *,
        ocr_engine_factory: Callable[[], Any] = _new_ocr_engine,
        color_signature_fn: Callable[[Path, tuple[float, float, float, float]], dict[str, Any]] = color_signature,
        frame_sampling_fps: float = FRAME_SAMPLING_FPS,
        workers: int = 4,
        backend: str = OCR_BACKEND,
        layout_discovery_frames: int = LAYOUT_DISCOVERY_FRAMES,
        scale_width: int = OCR_SCALE_WIDTH,
    ) -> None:
        self._ocr_engine_factory = ocr_engine_factory
        self._color_signature = color_signature_fn
        self._frame_sampling_fps = frame_sampling_fps
        self._workers = max(1, workers)
        self._backend = backend
        self._layout_discovery_frames = layout_discovery_frames
        self._scale_width = scale_width

    def _extract_discovery_frames(self, job: FilmOcrJob, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        duration_seconds = max(1.0, job.duration_ms / 1000)
        for index in range(self._layout_discovery_frames):
            cancel.checkpoint()
            timestamp = duration_seconds * (index + 0.5) / self._layout_discovery_frames
            subprocess.run(  # noqa: S603 - argv uses a resolved executable.
                [
                    _required_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}",
                    "-i", str(job.video_path), "-frames:v", "1", "-vf", "scale=960:-2",
                    str(output_dir / f"layout_{index + 1:04d}.jpg"),
                ],
                check=True,
            )
        return sorted(output_dir.glob("layout_*.jpg"))

    def _discover_roi(self, job: FilmOcrJob, on_progress: ProgressFn) -> dict[str, float]:
        on_progress("Film v3 layout: discovering subtitle band")
        centers: list[float] = []
        heights: list[float] = []
        with tempfile.TemporaryDirectory(prefix="nof-film-layout-") as temp:
            frames = self._extract_discovery_frames(job, Path(temp))
            engine = self._ocr_engine_factory()
            for frame in frames:
                cancel.checkpoint()
                _width, height = _image_size(frame)
                for row in _result_rows(engine(str(frame), use_cls=False)):
                    text = re.sub(r"\s+", "", str(row["text"]))
                    if len(_CJK_RE.findall(text)) < 3:
                        continue
                    bbox = _bbox_from_polygon(row["polygon"])
                    if bbox is None:
                        continue
                    _left, top, _right, bottom = bbox
                    center = ((top + bottom) / 2) / height
                    line_height = max(0.0, bottom - top) / height
                    if center >= 0.58 and line_height <= 0.16:
                        centers.append(center)
                        heights.append(line_height)
        if not centers:
            return dict(DEFAULT_ROI)
        subtitle_center = float(median(centers))
        typical_height = float(median(heights)) if heights else 0.04
        y_min = max(0.62, subtitle_center - max(0.12, typical_height * 3.0))
        y_max = min(0.99, subtitle_center + max(0.14, typical_height * 3.5))
        if y_max - y_min < 0.20:
            y_min = max(0.62, y_max - 0.20)
        return {"x": 0.0, "y": round(y_min, 4), "width": 1.0, "height": round(y_max - y_min, 4)}

    def _extract_ocr_frames(self, job: FilmOcrJob, output_dir: Path, roi: Mapping[str, float]) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        x, y = float(roi["x"]), float(roi["y"])
        width, height = float(roi["width"]), float(roi["height"])
        video_filter = (
            f"fps={self._frame_sampling_fps},"
            f"crop=floor(iw*{width}):floor(ih*{height}):floor(iw*{x}):floor(ih*{y}),"
            f"scale={self._scale_width}:-2"
        )
        subprocess.run(  # noqa: S603 - argv uses a resolved executable.
            [
                _required_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(job.video_path),
                "-vf", video_filter, "-q:v", "3", str(output_dir / "frame_%08d.jpg"),
            ],
            check=True,
        )
        return sorted(output_dir.glob("frame_*.jpg"))

    def _frame_observations(
        self, engine: Any, frame: Path, *, frame_index: int, roi: Mapping[str, float]
    ) -> list[dict[str, Any]]:
        width, height = _image_size(frame)
        observations: list[dict[str, Any]] = []
        for line_order, row in enumerate(_result_rows(engine(str(frame), use_cls=False))):
            text = re.sub(r"\s+", "", str(row["text"]))
            if not text or not (_CJK_RE.search(text) or _LATIN_RE.search(text)):
                continue
            bbox = _bbox_from_polygon(row["polygon"])
            if bbox is None:
                continue
            left, top, right, bottom = bbox
            x_norm = float(roi["x"]) + (left / width) * float(roi["width"])
            y_norm = float(roi["y"]) + (top / height) * float(roi["height"])
            right_norm = float(roi["x"]) + (right / width) * float(roi["width"])
            bottom_norm = float(roi["y"]) + (bottom / height) * float(roi["height"])
            observations.append(
                {
                    "observation_id": "",
                    "frame_index": frame_index,
                    "time_ms": int(round((frame_index - 1) * 1000 / self._frame_sampling_fps)),
                    "line_order": line_order,
                    "text": text,
                    "confidence": round(max(0.0, min(1.0, float(row["confidence"]))), 4),
                    "bbox_norm": {
                        "x": round(x_norm, 5), "y": round(y_norm, 5),
                        "width": round(max(0.0, right_norm - x_norm), 5),
                        "height": round(max(0.0, bottom_norm - y_norm), 5),
                    },
                    "polygon_crop_px": row["polygon"],
                    "color_signature": self._color_signature(frame, bbox),
                }
            )
        for observation in observations:
            if not _CJK_RE.search(str(observation["text"])):
                continue
            box = observation["bbox_norm"]
            bottom = float(box["y"]) + float(box["height"])
            observation["has_latin_companion"] = any(
                _LATIN_RE.search(str(other["text"]))
                and not _CJK_RE.search(str(other["text"]))
                and float(other["bbox_norm"]["y"]) >= bottom - 0.01
                and float(other["bbox_norm"]["y"]) - bottom <= 0.10
                for other in observations
            )
        return observations

    def _ocr_video(self, job: FilmOcrJob, *, roi: Mapping[str, float], on_progress: ProgressFn) -> tuple[list[dict[str, Any]], int]:
        with tempfile.TemporaryDirectory(prefix="nof-film-v3-ocr-") as temp:
            frames = self._extract_ocr_frames(job, Path(temp), roi)
            if not frames:
                raise RuntimeError("film v3 OCR frame extraction produced no frames")

            def process(worker_number: int) -> list[dict[str, Any]]:
                engine = self._ocr_engine_factory()
                output: list[dict[str, Any]] = []
                for index in range(worker_number, len(frames), min(self._workers, len(frames))):
                    cancel.checkpoint()
                    output.extend(self._frame_observations(engine, frames[index], frame_index=index + 1, roi=roi))
                return output

            worker_count = min(self._workers, len(frames))
            observations: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                for rows in executor.map(process, range(worker_count)):
                    observations.extend(rows)
            observations.sort(key=lambda row: (int(row["frame_index"]), int(row["line_order"])))
            for index, observation in enumerate(observations, start=1):
                observation["observation_id"] = f"obs_{index:06d}"
            on_progress(f"Film v3 OCR: frames={len(frames)} observations={len(observations)}")
            return observations, len(frames)

    def execute(self, job: FilmOcrJob, *, on_progress: ProgressFn = _noop) -> FilmOcrResult:
        roi = self._discover_roi(job, on_progress)
        observations, frame_count = self._ocr_video(job, roi=roi, on_progress=on_progress)
        settings = algorithm_settings(
            backend=self._backend,
            frame_sampling_fps=self._frame_sampling_fps,
            layout_discovery_frames=self._layout_discovery_frames,
            scale_width=self._scale_width,
        )
        return FilmOcrResult(
            roi=roi,
            observations=observations,
            backend=self._backend,
            algorithm_signature=algorithm_signature(
                roi,
                backend=self._backend,
                frame_sampling_fps=self._frame_sampling_fps,
                layout_discovery_frames=self._layout_discovery_frames,
                scale_width=self._scale_width,
            ),
            execution={"executor": "local", "frame_count": frame_count},
            request=build_remote_request(job, settings=settings),
            observations_sha256=canonical_observations_sha256(observations),
        )


class RemoteFilmOcrExecutor:
    """Validate middleware output before callers can persist film artifacts."""

    def __init__(self, transport: FilmOcrMiddlewareTransport, *, settings: Mapping[str, Any] | None = None) -> None:
        self._transport = transport
        self._settings = dict(settings or algorithm_settings())

    def execute(self, job: FilmOcrJob, *, on_progress: ProgressFn = _noop) -> FilmOcrResult:
        request = build_remote_request(job, settings=self._settings)
        response = self._transport.submit(request=request, source_path=job.video_path, on_progress=on_progress)
        return self._validated_result(response, request)

    def _validated_result(self, response: Mapping[str, Any], request: Mapping[str, Any]) -> FilmOcrResult:
        if not isinstance(response, Mapping):
            raise RuntimeError("film OCR middleware returned a non-object result")
        if response.get("schema_version") != FILM_OCR_RESULT_SCHEMA_VERSION:
            raise RuntimeError("film OCR middleware result schema mismatch")
        if response.get("operation") != FILM_OCR_OPERATION:
            raise RuntimeError("film OCR middleware result operation mismatch")
        if response.get("request_id") != request["request_id"]:
            raise RuntimeError("film OCR middleware request identity mismatch")
        if response.get("idempotency_key") != request["idempotency_key"]:
            raise RuntimeError("film OCR middleware idempotency identity mismatch")
        source = response.get("source")
        expected_source = request["source"]
        if not isinstance(source, Mapping) or dict(source) != dict(expected_source):
            raise RuntimeError("film OCR middleware source identity mismatch")
        roi = response.get("roi")
        observations = response.get("observations")
        signature = response.get("algorithm_signature")
        backend = response.get("backend")
        if not isinstance(roi, Mapping) or not isinstance(observations, list) or not isinstance(signature, Mapping) or not isinstance(backend, str):
            raise RuntimeError("film OCR middleware result is incomplete")
        try:
            normalized_roi = {key: float(roi[key]) for key in ("x", "y", "width", "height")}
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("film OCR middleware ROI is invalid") from exc
        if any(not math.isfinite(value) for value in normalized_roi.values()) or any(
            value < 0.0 or value > 1.0 for value in normalized_roi.values()
        ):
            raise RuntimeError("film OCR middleware ROI is outside normalized bounds")
        if normalized_roi["width"] <= 0.0 or normalized_roi["height"] <= 0.0:
            raise RuntimeError("film OCR middleware ROI has no area")
        if normalized_roi["x"] + normalized_roi["width"] > 1.0 or normalized_roi["y"] + normalized_roi["height"] > 1.0:
            raise RuntimeError("film OCR middleware ROI exceeds normalized bounds")
        validated_observations = _validated_observations(observations)
        expected_signature = algorithm_signature(
            normalized_roi,
            backend=str(self._settings["backend"]),
            frame_sampling_fps=float(self._settings["frame_sampling_fps"]),
            layout_discovery_frames=int(self._settings["layout_discovery_frames"]),
            scale_width=int(self._settings["scale_width"]),
        )
        if dict(signature) != expected_signature:
            raise RuntimeError("film OCR middleware algorithm signature mismatch")
        if backend != str(signature.get("backend")):
            raise RuntimeError("film OCR middleware backend identity mismatch")
        expected_digest = canonical_observations_sha256(validated_observations)
        if response.get("observations_sha256") != expected_digest:
            raise RuntimeError("film OCR middleware observations checksum mismatch")
        execution = response.get("execution")
        if execution is not None and not isinstance(execution, Mapping):
            raise RuntimeError("film OCR middleware execution metadata is invalid")
        return FilmOcrResult(
            roi=normalized_roi,
            observations=validated_observations,
            backend=backend,
            algorithm_signature=dict(signature),
            execution={**dict(execution or {}), "executor": "remote", "request_id": request["request_id"]},
            request=dict(request),
            observations_sha256=expected_digest,
        )


REMOTE_FILM_OCR_TRANSPORT_FACTORY: Callable[[], FilmOcrMiddlewareTransport] | None = None


def build_configured_film_ocr_executor(
    *,
    local_ocr_engine_factory: Callable[[], Any] = _new_ocr_engine,
    local_color_signature_fn: Callable[[Path, tuple[float, float, float, float]], dict[str, Any]] = color_signature,
    frame_sampling_fps: float = FRAME_SAMPLING_FPS,
    workers: int = 4,
    backend: str = OCR_BACKEND,
    layout_discovery_frames: int = LAYOUT_DISCOVERY_FRAMES,
    scale_width: int = OCR_SCALE_WIDTH,
) -> FilmOcrExecutor:
    """Select an explicitly configured executor without any remote fallback."""
    mode = os.environ.get("NOF_FILM_OCR_EXECUTOR", "local").strip().lower()
    if mode == "local":
        return LocalFilmOcrExecutor(
            ocr_engine_factory=local_ocr_engine_factory,
            color_signature_fn=local_color_signature_fn,
            frame_sampling_fps=frame_sampling_fps,
            workers=workers,
            backend=backend,
            layout_discovery_frames=layout_discovery_frames,
            scale_width=scale_width,
        )
    if mode == "remote":
        if REMOTE_FILM_OCR_TRANSPORT_FACTORY is None:
            raise RuntimeError("NOF_FILM_OCR_EXECUTOR=remote requires an injected FilmOcrMiddlewareTransport")
        return RemoteFilmOcrExecutor(
            REMOTE_FILM_OCR_TRANSPORT_FACTORY(),
            settings=algorithm_settings(
                backend=backend,
                frame_sampling_fps=frame_sampling_fps,
                layout_discovery_frames=layout_discovery_frames,
                scale_width=scale_width,
            ),
        )
    raise RuntimeError("NOF_FILM_OCR_EXECUTOR must be local or remote")
