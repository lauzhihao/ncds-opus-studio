"""Deterministic film rebuild commands for frame-first production recipes.

The commands in this module intentionally start after creative/media matching:
the caller supplies a clean film master, an approved EDL, an already generated
voice file, and (optionally) subtitles.  The implementation owns reproducible
artifact lineage, direct global-frame rendering, and portable media QA.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any

ProgressFn = Callable[[str], None]
ARTIFACT_SCHEMA_VERSION = "artifact_ref.v1"
PRODUCER_VERSION = "film_rebuild_mvp.v1"
FRAME_EDL_SCHEMA_VERSION = "film_frame_edl.v1"
SOURCE_MANIFEST_SCHEMA_VERSION = "film_source_manifest.v1"
VOICE_MANIFEST_SCHEMA_VERSION = "film_voice_manifest.v1"
RENDER_MANIFEST_SCHEMA_VERSION = "film_render_manifest.v1"
QA_SCHEMA_VERSION = "media_qa_report.v1"


def _noop(_text: str) -> None:
    return None


def _required_binary(name: str) -> str:
    value = shutil.which(name)
    if value is None:
        raise RuntimeError(f"required binary is unavailable: {name}")
    return value


@lru_cache(maxsize=16)
def _ffmpeg_has_filter(ffmpeg: str, name: str) -> bool:
    result = subprocess.run(  # noqa: S603 - resolved ffmpeg plus fixed argv.
        [ffmpeg, "-hide_banner", "-filters"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] == name:
            return True
    return False


def _run(
    args: list[str],
    *,
    label: str,
    capture_stdout: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - args only; shell is never enabled.
        args,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-2000:]
        raise RuntimeError(f"{label} failed ({result.returncode}): {detail}")
    return result


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_uri(job_dir: Path, path: Path) -> str:
    """Return a job-relative URI so manifests do not embed a fixed job root."""
    return Path(os.path.relpath(path.resolve(), job_dir.resolve())).as_posix()


def _resolve_uri(job_dir: Path, uri: str) -> Path:
    value = str(uri or "").strip()
    if not value:
        raise ValueError("artifact uri is empty")
    # MVP manifests only emit relative filesystem URIs.  Accept absolute input
    # for forward/backward compatibility without serializing it ourselves.
    path = Path(value)
    return path if path.is_absolute() else (job_dir / path).resolve()


def _artifact_ref(
    job_dir: Path,
    path: Path,
    *,
    kind: str,
    producer_step: str,
    input_artifact_ids: Iterable[str] = (),
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"artifact file is missing: {path}")
    digest = _sha256(path)
    identity = hashlib.sha256(
        f"{kind}\0{digest}\0{path.name}".encode()
    ).hexdigest()[:24]
    return {
        "artifact_id": f"a_{identity}",
        "kind": kind,
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "uri": _artifact_uri(job_dir, path),
        "sha256": digest,
        "size_bytes": path.stat().st_size,
        "producer_step": producer_step,
        "producer_version": PRODUCER_VERSION,
        "input_artifact_ids": [str(value) for value in input_artifact_ids],
        "metadata": dict(metadata or {}),
    }


def _manifest_artifact(doc: dict[str, Any], kind: str) -> dict[str, Any]:
    artifacts = doc.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("manifest artifacts must be a list")
    for artifact in artifacts:
        if isinstance(artifact, dict) and artifact.get("kind") == kind:
            return artifact
    raise ValueError(f"manifest does not contain artifact kind={kind}")


def _parse_fraction(value: Any, *, label: str) -> Fraction:
    text = str(value or "").strip()
    if not text or text == "0/0":
        raise ValueError(f"{label} is missing")
    try:
        parsed = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{label} is invalid: {text}") from exc
    if parsed <= 0:
        raise ValueError(f"{label} must be positive: {text}")
    return parsed


def _fraction_doc(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _fraction_from_doc(value: Any, *, label: str) -> Fraction:
    if isinstance(value, dict):
        numerator = value.get("numerator", value.get("num"))
        denominator = value.get("denominator", value.get("den"))
        try:
            return _parse_fraction(f"{int(numerator)}/{int(denominator)}", label=label)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} is invalid") from exc
    return _parse_fraction(value, label=label)


def _probe(path: Path, *, count_frames: bool = False) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"media file is missing: {path}")
    args = [
        _required_binary("ffprobe"),
        "-v",
        "error",
    ]
    if count_frames:
        args.append("-count_frames")
    args.extend(["-show_format", "-show_streams", "-of", "json", str(path)])
    result = _run(args, label="ffprobe")
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("streams"), list):
        raise RuntimeError(f"ffprobe returned no streams for {path}")
    return doc


def _stream(probe: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for stream in probe.get("streams") or []:
        if isinstance(stream, dict) and stream.get("codec_type") == kind:
            return stream
    return None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _media_summary(probe: dict[str, Any]) -> dict[str, Any]:
    video = _stream(probe, "video")
    audio = _stream(probe, "audio")
    if video is None:
        raise ValueError("media has no video stream")
    fps = _parse_fraction(
        video.get("avg_frame_rate") or video.get("r_frame_rate"),
        label="video fps",
    )
    format_doc = probe.get("format") if isinstance(probe.get("format"), dict) else {}
    duration = _float(video.get("duration")) or _float(format_doc.get("duration"))
    if duration <= 0:
        raise ValueError("media duration is unavailable")
    nb_frames = _int(video.get("nb_frames")) or _int(video.get("nb_read_frames"))
    estimated_frames = nb_frames or int(math.floor(duration * float(fps) + 1e-6))
    streams = []
    for item in probe.get("streams") or []:
        if not isinstance(item, dict):
            continue
        streams.append({
            "index": item.get("index"),
            "codec_type": item.get("codec_type"),
            "codec_name": item.get("codec_name"),
            "time_base": item.get("time_base"),
            "duration": item.get("duration"),
            "sample_rate": item.get("sample_rate"),
            "channels": item.get("channels"),
            "channel_layout": item.get("channel_layout"),
            "width": item.get("width"),
            "height": item.get("height"),
            "avg_frame_rate": item.get("avg_frame_rate"),
            "r_frame_rate": item.get("r_frame_rate"),
            "nb_frames": item.get("nb_frames"),
            "language": (
                str((item.get("tags") or {}).get("language") or "")
                if isinstance(item.get("tags"), dict)
                else ""
            ),
        })
    return {
        "duration_seconds": duration,
        "fps": _fraction_doc(fps),
        "fps_rational": f"{fps.numerator}/{fps.denominator}",
        "time_base": str(video.get("time_base") or ""),
        "frame_count": estimated_frames,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": audio is not None,
        "streams": streams,
    }


def prepare_film_sources(
    job_dir: str | Path,
    reference_path: str | Path,
    master_path: str | Path,
    *,
    master_audio_stream: int = 0,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Probe and register the benchmark reference and clean film master."""
    root = Path(job_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    reference = Path(reference_path).expanduser().resolve()
    master = Path(master_path).expanduser().resolve()
    on_progress("film source: probing reference")
    reference_summary = _media_summary(_probe(reference))
    on_progress("film source: probing clean master")
    master_summary = _media_summary(_probe(master))
    if not master_summary["has_audio"]:
        raise ValueError("clean film master must contain an audio stream")
    audio_streams = [
        item
        for item in master_summary["streams"]
        if item.get("codec_type") == "audio"
    ]
    audio_ordinal = int(master_audio_stream)
    if audio_ordinal < 0 or audio_ordinal >= len(audio_streams):
        raise ValueError(
            "master_audio_stream is out of range: "
            f"selected={audio_ordinal} available={len(audio_streams)}"
        )
    selected_audio = {
        "ordinal": audio_ordinal,
        **audio_streams[audio_ordinal],
    }
    master_summary["selected_audio_stream"] = selected_audio
    master_summary["audio_stream_ordinal"] = audio_ordinal

    reference_asset = _artifact_ref(
        root,
        reference,
        kind="film_reference",
        producer_step="source",
        metadata={"role": "benchmark_reference", **reference_summary},
    )
    master_asset = _artifact_ref(
        root,
        master,
        kind="film_master",
        producer_step="source",
        metadata={"role": "clean_master", **master_summary},
    )
    manifest = {
        "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "producer_step": "source",
        "producer_version": PRODUCER_VERSION,
        "job_root": ".",
        "reference_asset_id": reference_asset["artifact_id"],
        "master_asset_id": master_asset["artifact_id"],
        "artifacts": [reference_asset, master_asset],
    }
    manifest_path = root / "film_rebuild" / "source" / "source_manifest.json"
    _write_json(manifest_path, manifest)
    on_progress("film source: manifest ready")
    return {
        "source_manifest_path": str(manifest_path),
        "reference_asset": reference_asset,
        "master_asset": master_asset,
        "artifacts": [reference_asset, master_asset],
    }


def _edl_profile(profile: Any) -> dict[str, Any]:
    if profile is None:
        return {}
    if isinstance(profile, dict):
        return dict(profile)
    raise ValueError("film EDL profile must be an object")


def _is_intentional(segment: dict[str, Any]) -> bool:
    if segment.get("intentional") is True or segment.get("intentional_non_monotonic") is True:
        return True
    flags = segment.get("flags")
    return isinstance(flags, list) and any(
        "intentional" in str(flag).lower() for flag in flags
    )


def _confidence_is_low(value: Any, threshold: float) -> bool:
    if isinstance(value, (int, float)):
        return float(value) < threshold
    return str(value or "").strip().lower() in {"low", "weak", "review"}


def _normalize_edl_segments(
    doc: dict[str, Any],
    *,
    fps: Fraction,
) -> list[dict[str, Any]]:
    raw = doc.get("segments")
    schema = "frames"
    if not isinstance(raw, list):
        raw = doc.get("edit_decision_list")
        schema = "milliseconds"
    if not isinstance(raw, list) or not raw:
        raise ValueError("film EDL must contain non-empty segments or edit_decision_list")
    normalized: list[dict[str, Any]] = []
    output_frame = 0
    for index, value in enumerate(raw, start=1):
        if not isinstance(value, dict):
            raise ValueError(f"film EDL segment {index} must be an object")
        segment_id = str(
            value.get("segment_id") or value.get("id") or f"segment-{index:04d}"
        )
        if schema == "frames":
            try:
                start = int(value["source_start_frame"])
                end = int(value["source_end_frame"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"film EDL segment {index} has invalid frame bounds") from exc
        else:
            try:
                start_ms = float(value["source_start_ms"])
                end_ms = float(value["source_end_ms"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"film EDL segment {index} has invalid ms bounds") from exc
            start = int(round(start_ms * float(fps) / 1000.0))
            end = int(round(end_ms * float(fps) / 1000.0))
        frame_count = end - start
        if start < 0 or frame_count <= 0:
            raise ValueError(
                f"film EDL segment {segment_id} must be a positive half-open frame range"
            )
        intentional = _is_intentional(value)
        normalized.append({
            "segment_id": segment_id,
            "source_start_frame": start,
            "source_end_frame": end,
            "output_start_frame": output_frame,
            "output_end_frame": output_frame + frame_count,
            "frame_count": frame_count,
            "confidence": value.get("confidence"),
            "intentional_non_monotonic": intentional,
            "source": value,
        })
        output_frame += frame_count
    return normalized


def build_film_frame_edl(
    job_dir: str | Path,
    source_manifest_path: str | Path,
    edl_path: str | Path,
    *,
    profile: dict[str, Any] | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Normalize approved frame/ms EDL input and emit review diagnostics."""
    root = Path(job_dir).resolve()
    source_manifest = _read_json(Path(source_manifest_path), label="film source manifest")
    master_asset = _manifest_artifact(source_manifest, "film_master")
    reference_asset = _manifest_artifact(source_manifest, "film_reference")
    master_metadata = master_asset.get("metadata") or {}
    fps = _fraction_from_doc(master_metadata.get("fps"), label="master fps")
    master_frames = int(master_metadata.get("frame_count") or 0)
    edl_input = Path(edl_path).expanduser().resolve()
    edl_doc = _read_json(edl_input, label="film EDL")
    edl_fps_value = edl_doc.get("fps") or edl_doc.get("frame_rate")
    if edl_fps_value is not None:
        edl_fps = _fraction_from_doc(edl_fps_value, label="EDL fps")
        if edl_fps != fps:
            raise ValueError(
                f"film EDL fps {edl_fps} does not match master fps {fps}"
            )
    settings = _edl_profile(profile)
    max_segment_seconds = float(settings.get("max_segment_seconds", 5.0))
    low_confidence_threshold = float(settings.get("low_confidence_threshold", 0.75))
    if max_segment_seconds <= 0:
        raise ValueError("max_segment_seconds must be positive")
    on_progress("film storyboard: normalizing frame EDL")
    segments = _normalize_edl_segments(edl_doc, fps=fps)

    backward: list[dict[str, Any]] = []
    overlap: list[dict[str, Any]] = []
    discontinuities: list[dict[str, Any]] = []
    overlong: list[dict[str, Any]] = []
    low_confidence: list[dict[str, Any]] = []
    out_of_bounds: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for segment in segments:
        sid = segment["segment_id"]
        if master_frames > 0 and segment["source_end_frame"] > master_frames:
            out_of_bounds.append({
                "segment_id": sid,
                "source_end_frame": segment["source_end_frame"],
                "master_frames": master_frames,
            })
        seconds = segment["frame_count"] / float(fps)
        if seconds > max_segment_seconds + 1e-9:
            overlong.append({
                "segment_id": sid,
                "duration_seconds": seconds,
                "limit_seconds": max_segment_seconds,
            })
        if _confidence_is_low(segment.get("confidence"), low_confidence_threshold):
            low_confidence.append({"segment_id": sid, "confidence": segment.get("confidence")})
        if previous is not None:
            start = segment["source_start_frame"]
            previous_start = previous["source_start_frame"]
            previous_end = previous["source_end_frame"]
            intentional = bool(segment["intentional_non_monotonic"])
            if start < previous_start:
                backward.append({
                    "segment_id": sid,
                    "previous_segment_id": previous["segment_id"],
                    "delta_frames": start - previous_start,
                    "intentional": intentional,
                })
            if start < previous_end:
                overlap.append({
                    "segment_id": sid,
                    "previous_segment_id": previous["segment_id"],
                    "overlap_frames": previous_end - start,
                    "intentional": intentional,
                })
            if start != previous_end:
                discontinuities.append({
                    "segment_id": sid,
                    "previous_segment_id": previous["segment_id"],
                    "delta_frames": start - previous_end,
                })
        previous = segment
    if out_of_bounds:
        first = out_of_bounds[0]
        raise ValueError(
            "film EDL exceeds clean master frame bounds: "
            f"segment={first['segment_id']} end={first['source_end_frame']} "
            f"master={first['master_frames']}"
        )

    review_reasons: list[str] = []
    if any(not item["intentional"] for item in backward):
        review_reasons.append("unmarked_backward_cut")
    if any(not item["intentional"] for item in overlap):
        review_reasons.append("unmarked_source_overlap")
    if overlong:
        review_reasons.append("segment_over_profile_limit")
    if low_confidence:
        review_reasons.append("low_confidence_match")
    frame_count = sum(segment["frame_count"] for segment in segments)
    qa = {
        "status": "review" if review_reasons else "pass",
        "review_reasons": review_reasons,
        "half_open_ranges": True,
        "segment_count": len(segments),
        "frame_count": frame_count,
        "max_segment_seconds": max_segment_seconds,
        "backward_cuts": backward,
        "source_overlaps": overlap,
        "source_discontinuities": discontinuities,
        "overlong_segments": overlong,
        "low_confidence_segments": low_confidence,
    }
    normalized_doc = {
        "schema_version": FRAME_EDL_SCHEMA_VERSION,
        "producer_step": "storyboard",
        "producer_version": PRODUCER_VERSION,
        "frame_interval": "half-open",
        "fps": _fraction_doc(fps),
        "frame_count": frame_count,
        "duration_seconds": frame_count / float(fps),
        "source_master_artifact_id": master_asset["artifact_id"],
        "segments": segments,
        "qa": qa,
    }
    output_dir = root / "film_rebuild" / "storyboard"
    normalized_path = output_dir / "frame_edl.json"
    _write_json(normalized_path, normalized_doc)
    input_edl_artifact = _artifact_ref(
        root,
        edl_input,
        kind="film_edl_input",
        producer_step="storyboard",
        input_artifact_ids=[reference_asset["artifact_id"], master_asset["artifact_id"]],
        metadata={"source_schema_version": edl_doc.get("schema_version") or edl_doc.get("version")},
    )
    edl_artifact = _artifact_ref(
        root,
        normalized_path,
        kind="film_frame_edl",
        producer_step="storyboard",
        input_artifact_ids=[
            reference_asset["artifact_id"],
            master_asset["artifact_id"],
            input_edl_artifact["artifact_id"],
        ],
        metadata={
            "schema_version": FRAME_EDL_SCHEMA_VERSION,
            "fps": _fraction_doc(fps),
            "frame_count": frame_count,
            "duration_seconds": frame_count / float(fps),
        },
    )
    manifest = {
        "schema_version": "film_edl_manifest.v1",
        "producer_step": "storyboard",
        "producer_version": PRODUCER_VERSION,
        "source_manifest_uri": _artifact_uri(root, Path(source_manifest_path)),
        "edl_artifact_id": edl_artifact["artifact_id"],
        "qa": qa,
        "artifacts": [input_edl_artifact, edl_artifact],
    }
    manifest_path = output_dir / "edl_manifest.json"
    _write_json(manifest_path, manifest)
    on_progress(
        f"film storyboard: ready segments={len(segments)} frames={frame_count} status={qa['status']}"
    )
    return {
        "edl_manifest_path": str(manifest_path),
        "edl_artifact": edl_artifact,
        "frame_count": frame_count,
        "duration_seconds": frame_count / float(fps),
        "qa": qa,
        "artifacts": [input_edl_artifact, edl_artifact],
    }


def prepare_film_voice(
    job_dir: str | Path,
    voice_path: str | Path,
    edl_manifest_path: str | Path,
    *,
    subtitle_path: str | Path | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Normalize a caller-supplied voice file to a portable 48 kHz stereo stem."""
    root = Path(job_dir).resolve()
    voice = Path(voice_path).expanduser().resolve()
    edl_manifest = _read_json(Path(edl_manifest_path), label="film EDL manifest")
    edl_artifact = _manifest_artifact(edl_manifest, "film_frame_edl")
    input_voice = _artifact_ref(
        root,
        voice,
        kind="film_voice_input",
        producer_step="tts",
        input_artifact_ids=[edl_artifact["artifact_id"]],
        metadata={"source": "caller_supplied", "external_tts_invoked": False},
    )
    probe = _probe(voice)
    audio = _stream(probe, "audio")
    if audio is None:
        raise ValueError("film voice input has no audio stream")
    output_dir = root / "film_rebuild" / "voice"
    output_dir.mkdir(parents=True, exist_ok=True)
    stem_path = output_dir / "voice_stem_48k_stereo.wav"
    on_progress("film tts: normalizing supplied voice stem")
    _run(
        [
            _required_binary("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(voice),
            "-map",
            "0:a:0",
            "-vn",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(stem_path),
        ],
        label="film voice normalization",
        capture_stdout=False,
    )
    stem_probe = _probe(stem_path)
    stem_audio = _stream(stem_probe, "audio") or {}
    format_doc = stem_probe.get("format") if isinstance(stem_probe.get("format"), dict) else {}
    voice_artifact = _artifact_ref(
        root,
        stem_path,
        kind="film_voice_stem",
        producer_step="tts",
        input_artifact_ids=[edl_artifact["artifact_id"], input_voice["artifact_id"]],
        metadata={
            "sample_rate": int(stem_audio.get("sample_rate") or 48000),
            "channels": int(stem_audio.get("channels") or 2),
            "codec_name": stem_audio.get("codec_name"),
            "duration_seconds": _float(stem_audio.get("duration")) or _float(format_doc.get("duration")),
            "external_tts_invoked": False,
        },
    )
    artifacts = [input_voice, voice_artifact]
    subtitle_artifact: dict[str, Any] | None = None
    if subtitle_path is not None and str(subtitle_path).strip():
        subtitle = Path(subtitle_path).expanduser().resolve()
        subtitle_artifact = _artifact_ref(
            root,
            subtitle,
            kind="film_narration_subtitles",
            producer_step="tts",
            input_artifact_ids=[edl_artifact["artifact_id"]],
            metadata={"format": subtitle.suffix.lower().lstrip(".")},
        )
        artifacts.append(subtitle_artifact)
    manifest = {
        "schema_version": VOICE_MANIFEST_SCHEMA_VERSION,
        "producer_step": "tts",
        "producer_version": PRODUCER_VERSION,
        "edl_manifest_uri": _artifact_uri(root, Path(edl_manifest_path)),
        "voice_artifact_id": voice_artifact["artifact_id"],
        "subtitle_artifact_id": (
            subtitle_artifact["artifact_id"] if subtitle_artifact is not None else None
        ),
        "artifacts": artifacts,
    }
    manifest_path = output_dir / "voice_manifest.json"
    _write_json(manifest_path, manifest)
    on_progress("film tts: supplied voice stem ready")
    return {
        "voice_manifest_path": str(manifest_path),
        "voice_artifact": voice_artifact,
        "subtitle_artifact": subtitle_artifact,
        "artifacts": artifacts,
    }


def _filter_path(path: Path) -> str:
    # Escaping is for FFmpeg's filter expression, not for a shell (we never use one).
    value = str(path.resolve()).replace("\\", "\\\\")
    for token in (":", "'", ",", "[", "]"):
        value = value.replace(token, f"\\{token}")
    return value


def _db_volume(value: Any, default: float) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number) or number < -60 or number > 24:
        raise ValueError("audio volume must be between -60 dB and 24 dB")
    return f"{number:.3f}dB"


def _render_settings(profile: Any) -> dict[str, Any]:
    if profile is None:
        return {}
    if not isinstance(profile, dict):
        raise ValueError("render_profile must be an object")
    return dict(profile)


def _encoder_args(profile: dict[str, Any]) -> list[str]:
    encoder = str(profile.get("encoder") or profile.get("video_codec") or "libx264").strip()
    if not encoder:
        raise ValueError("render encoder is empty")
    if encoder == "libx264":
        return [
            "-c:v",
            encoder,
            "-preset",
            str(profile.get("preset") or "veryfast"),
            "-crf",
            str(int(profile.get("crf", 20))),
        ]
    if encoder in {"h264_nvenc", "hevc_nvenc"}:
        return [
            "-c:v",
            encoder,
            "-preset",
            str(profile.get("preset") or "p5"),
            "-cq",
            str(int(profile.get("cq", 20))),
        ]
    # Other locally installed encoders remain configurable without coupling the
    # production contract to one machine or IP address.
    return ["-c:v", encoder]


def _segment_audio_filter(
    index: int,
    segment: dict[str, Any],
    *,
    fps: Fraction,
    fade_seconds: float,
    fade_in: bool,
    fade_out: bool,
    audio_ordinal: int,
) -> str:
    start = segment["source_start_frame"] / float(fps)
    end = segment["source_end_frame"] / float(fps)
    duration = segment["frame_count"] / float(fps)
    filters = [
        f"[0:a:{audio_ordinal}]atrim=start={start:.12f}:end={end:.12f}",
        "asetpts=PTS-STARTPTS",
        "aresample=48000",
        "aformat=sample_rates=48000:channel_layouts=stereo",
        "apad",
        f"atrim=duration={duration:.12f}",
    ]
    applied_fade = min(fade_seconds, duration / 4.0)
    if applied_fade > 0 and fade_in:
        filters.append(f"afade=t=in:st=0:d={applied_fade:.6f}")
    if applied_fade > 0 and fade_out:
        filters.append(
            f"afade=t=out:st={max(0.0, duration - applied_fade):.12f}:d={applied_fade:.6f}"
        )
    return ",".join(filters) + f"[a{index}]"


def render_film_from_master(
    job_dir: str | Path,
    source_manifest_path: str | Path,
    edl_manifest_path: str | Path,
    voice_manifest_path: str | Path,
    *,
    render_profile: dict[str, Any] | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Render directly from clean-master global frames plus supplied narration."""
    root = Path(job_dir).resolve()
    source_manifest = _read_json(Path(source_manifest_path), label="film source manifest")
    edl_manifest = _read_json(Path(edl_manifest_path), label="film EDL manifest")
    voice_manifest = _read_json(Path(voice_manifest_path), label="film voice manifest")
    master_asset = _manifest_artifact(source_manifest, "film_master")
    edl_artifact = _manifest_artifact(edl_manifest, "film_frame_edl")
    voice_artifact = _manifest_artifact(voice_manifest, "film_voice_stem")
    master = _resolve_uri(root, master_asset["uri"])
    master_metadata = master_asset.get("metadata")
    if not isinstance(master_metadata, dict):
        raise ValueError("film master metadata is missing")
    audio_ordinal = _int(master_metadata.get("audio_stream_ordinal"), -1)
    if audio_ordinal < 0:
        raise ValueError("film master selected audio stream is missing")
    edl_path = _resolve_uri(root, edl_artifact["uri"])
    voice = _resolve_uri(root, voice_artifact["uri"])
    edl = _read_json(edl_path, label="normalized film frame EDL")
    if edl.get("schema_version") != FRAME_EDL_SCHEMA_VERSION:
        raise ValueError("render requires film_frame_edl.v1")
    segments = edl.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("normalized film frame EDL has no segments")
    fps = _fraction_from_doc(edl.get("fps"), label="frame EDL fps")
    expected_frames = int(edl.get("frame_count") or 0)
    if expected_frames <= 0 or expected_frames != sum(
        int(item.get("frame_count") or 0) for item in segments if isinstance(item, dict)
    ):
        raise ValueError("normalized film frame EDL frame_count is inconsistent")
    expected_duration = expected_frames / float(fps)
    settings = _render_settings(render_profile)
    profile_fps = settings.get("fps")
    if profile_fps is not None:
        requested_fps = _fraction_from_doc(profile_fps, label="render profile fps")
        if requested_fps != fps:
            raise ValueError(
                f"render profile fps {requested_fps} does not match EDL fps {fps}"
            )
    fade_ms = float(settings.get("cut_audio_fade_ms", 12.0))
    if fade_ms < 0 or fade_ms > 100:
        raise ValueError("cut_audio_fade_ms must be between 0 and 100")
    contrast = float(settings.get("contrast", 1.0))
    if contrast <= 0 or contrast > 3:
        raise ValueError("contrast must be within (0, 3]")
    subtitle_artifact: dict[str, Any] | None = None
    try:
        subtitle_artifact = _manifest_artifact(voice_manifest, "film_narration_subtitles")
    except ValueError:
        pass
    render_warnings: list[str] = []
    subtitle_mode = "none"

    video_filters: list[str] = []
    audio_filters: list[str] = []
    for index, raw_segment in enumerate(segments):
        if not isinstance(raw_segment, dict):
            raise ValueError(f"normalized film frame EDL segment {index + 1} is invalid")
        start = int(raw_segment["source_start_frame"])
        end = int(raw_segment["source_end_frame"])
        video_filters.append(
            f"[0:v:0]trim=start_frame={start}:end_frame={end},"
            f"setpts=PTS-STARTPTS[v{index}]"
        )
        previous = segments[index - 1] if index > 0 else None
        following = segments[index + 1] if index + 1 < len(segments) else None
        fade_in = bool(
            previous is not None
            and int(previous["source_end_frame"]) != start
        )
        fade_out = bool(
            following is not None
            and end != int(following["source_start_frame"])
        )
        audio_filters.append(
            _segment_audio_filter(
                index,
                raw_segment,
                fps=fps,
                fade_seconds=fade_ms / 1000.0,
                fade_in=fade_in,
                fade_out=fade_out,
                audio_ordinal=audio_ordinal,
            )
        )
    video_inputs = "".join(f"[v{index}]" for index in range(len(segments)))
    audio_inputs = "".join(f"[a{index}]" for index in range(len(segments)))
    filters = [
        *video_filters,
        *audio_filters,
        f"{video_inputs}concat=n={len(segments)}:v=1:a=0[cutv]",
        f"{audio_inputs}concat=n={len(segments)}:v=0:a=1[beda]",
    ]
    visual_filters: list[str] = []
    if abs(contrast - 1.0) > 1e-9:
        visual_filters.append(f"eq=contrast={contrast:.6f}")
    width = settings.get("width")
    height = settings.get("height")
    if width is not None or height is not None:
        if width is None or height is None:
            raise ValueError("render padding requires both width and height")
        width_i = int(width)
        height_i = int(height)
        if width_i <= 0 or height_i <= 0:
            raise ValueError("render width and height must be positive")
        visual_filters.extend([
            f"scale={width_i}:{height_i}:force_original_aspect_ratio=decrease",
            f"pad={width_i}:{height_i}:(ow-iw)/2:(oh-ih)/2:color=black",
        ])
    if subtitle_artifact is not None:
        subtitle = _resolve_uri(root, subtitle_artifact["uri"])
        ffmpeg = _required_binary("ffmpeg")
        if _ffmpeg_has_filter(ffmpeg, "subtitles"):
            visual_filters.append(f"subtitles=filename='{_filter_path(subtitle)}'")
            subtitle_mode = "burned"
        elif settings.get("require_burned_subtitles") is True:
            raise RuntimeError(
                "render requires the FFmpeg subtitles filter, but this build has no libass support"
            )
        else:
            subtitle_mode = "artifact_only"
            render_warnings.append(
                "FFmpeg subtitles filter unavailable; narration subtitle artifact was not burned"
            )
    visual_filters.append("format=yuv420p")
    filters.append("[cutv]" + ",".join(visual_filters) + "[outv]")
    bed_volume = _db_volume(settings.get("bed_volume_db"), -2.0)
    voice_volume = _db_volume(settings.get("voice_volume_db"), 0.0)
    filters.extend([
        f"[1:a:0]aresample=48000,apad,atrim=duration={expected_duration:.12f},"
        f"asetpts=PTS-STARTPTS,volume={voice_volume}[voicepre]",
        "[voicepre]asplit=2[voicesc][voicemix]",
        f"[beda]volume={bed_volume}[bed]",
        "[bed][voicesc]sidechaincompress=threshold=0.030:ratio=8:attack=5:release=250[ducked]",
        "[ducked][voicemix]amix=inputs=2:duration=first:dropout_transition=0,"
        f"alimiter=limit=0.95,apad,atrim=duration={expected_duration:.12f}[outa]",
    ])
    output_dir = root / "film_rebuild" / "render"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = Path(str(settings.get("output_name") or "film_rebuild.mp4")).name
    if not output_name.lower().endswith(".mp4"):
        output_name += ".mp4"
    output_path = output_dir / output_name
    ffmpeg_args = [
        _required_binary("ffmpeg"),
        "-y",
        "-v",
        "error",
        "-i",
        str(master),
        "-i",
        str(voice),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-r",
        f"{fps.numerator}/{fps.denominator}",
        "-frames:v",
        str(expected_frames),
        "-fps_mode",
        "cfr",
        *_encoder_args(settings),
        "-c:a",
        str(settings.get("audio_encoder") or settings.get("audio_codec") or "aac"),
        "-b:a",
        str(settings.get("audio_bitrate") or "192k"),
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    on_progress(
        f"film render: start segments={len(segments)} frames={expected_frames}"
    )
    _run(ffmpeg_args, label="film render", capture_stdout=False)
    input_ids = [
        master_asset["artifact_id"],
        edl_artifact["artifact_id"],
        voice_artifact["artifact_id"],
    ]
    if subtitle_artifact is not None:
        input_ids.append(subtitle_artifact["artifact_id"])
    render_artifact = _artifact_ref(
        root,
        output_path,
        kind="film_render",
        producer_step="render",
        input_artifact_ids=input_ids,
        metadata={
            "expected_frames": expected_frames,
            "expected_duration_seconds": expected_duration,
            "fps": _fraction_doc(fps),
            "encoder": str(
                settings.get("encoder") or settings.get("video_codec") or "libx264"
            ),
            "source_audio_artifact_id": master_asset["artifact_id"],
            "source_audio_role": "clean_master",
            "source_audio_stream_ordinal": audio_ordinal,
            "cut_audio_fade_ms": fade_ms,
            "subtitle_mode": subtitle_mode,
        },
    )
    manifest = {
        "schema_version": RENDER_MANIFEST_SCHEMA_VERSION,
        "producer_step": "render",
        "producer_version": PRODUCER_VERSION,
        "expected_frames": expected_frames,
        "expected_duration_seconds": expected_duration,
        "fps": _fraction_doc(fps),
        "render_artifact_id": render_artifact["artifact_id"],
        "input_artifact_ids": input_ids,
        "subtitle_mode": subtitle_mode,
        "warnings": render_warnings,
        "artifacts": [render_artifact],
    }
    manifest_path = output_dir / "render_manifest.json"
    _write_json(manifest_path, manifest)
    on_progress("film render: output ready")
    return {
        "render_manifest_path": str(manifest_path),
        "render_artifact": render_artifact,
        "output_path": str(output_path),
        "expected_frames": expected_frames,
        "warnings": render_warnings,
        "artifacts": [render_artifact],
    }


def _check(
    checks: list[dict[str, Any]],
    name: str,
    passed: bool,
    *,
    expected: Any = None,
    actual: Any = None,
) -> None:
    checks.append({
        "name": name,
        "status": "passed" if passed else "failed",
        "expected": expected,
        "actual": actual,
    })


def quality_check_film_render(
    job_dir: str | Path,
    render_manifest_path: str | Path,
    *,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Probe and fully decode a film render, enforcing frame/A-V invariants."""
    root = Path(job_dir).resolve()
    render_manifest = _read_json(Path(render_manifest_path), label="film render manifest")
    render_artifact = _manifest_artifact(render_manifest, "film_render")
    output = _resolve_uri(root, render_artifact["uri"])
    expected_frames = int(render_manifest.get("expected_frames") or 0)
    expected_duration = float(render_manifest.get("expected_duration_seconds") or 0)
    expected_fps = _fraction_from_doc(render_manifest.get("fps"), label="render fps")
    if expected_frames <= 0 or expected_duration <= 0:
        raise ValueError("render manifest expected frame contract is invalid")
    on_progress("film quality: probing output")
    probe = _probe(output, count_frames=True)
    video = _stream(probe, "video")
    audio = _stream(probe, "audio")
    checks: list[dict[str, Any]] = []
    warnings = [
        str(value)
        for value in (render_manifest.get("warnings") or [])
        if str(value).strip()
    ]
    _check(checks, "video_stream_present", video is not None, expected=True, actual=video is not None)
    _check(checks, "audio_stream_present", audio is not None, expected=True, actual=audio is not None)
    if video is None:
        actual_frames = 0
        actual_fps = Fraction(1, 1)
        video_duration = 0.0
    else:
        actual_frames = int(video.get("nb_read_frames") or video.get("nb_frames") or 0)
        actual_fps = _parse_fraction(
            video.get("avg_frame_rate") or video.get("r_frame_rate"),
            label="rendered video fps",
        )
        video_duration = _float(video.get("duration"))
        if video_duration <= 0 and actual_frames > 0:
            video_duration = actual_frames / float(actual_fps)
    frame_tolerance = 1.0 / float(expected_fps)
    _check(
        checks,
        "frame_count",
        actual_frames == expected_frames,
        expected=expected_frames,
        actual=actual_frames,
    )
    _check(
        checks,
        "cfr_fps",
        actual_fps == expected_fps,
        expected=str(expected_fps),
        actual=str(actual_fps),
    )
    _check(
        checks,
        "video_duration_within_one_frame",
        abs(video_duration - expected_duration) <= frame_tolerance + 1e-6,
        expected=expected_duration,
        actual=video_duration,
    )
    audio_duration = _float(audio.get("duration")) if audio is not None else 0.0
    if audio is not None and audio_duration <= 0:
        format_doc = probe.get("format") if isinstance(probe.get("format"), dict) else {}
        audio_duration = _float(format_doc.get("duration"))
    _check(
        checks,
        "audio_duration_within_one_frame",
        audio is not None and abs(audio_duration - expected_duration) <= frame_tolerance + 1e-6,
        expected=expected_duration,
        actual=audio_duration,
    )
    on_progress("film quality: full decode")
    decode = subprocess.run(  # noqa: S603 - resolved ffmpeg plus fixed argv.
        [
            _required_binary("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(output),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        check=False,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _check(
        checks,
        "full_decode",
        decode.returncode == 0,
        expected=0,
        actual=decode.returncode,
    )
    if decode.stderr.strip():
        warnings.append(decode.stderr.strip()[-1000:])
    status = "pass" if all(item["status"] == "passed" for item in checks) else "fail"
    report = {
        "schema_version": QA_SCHEMA_VERSION,
        "producer_step": "quality",
        "producer_version": PRODUCER_VERSION,
        "status": status,
        "render_artifact_id": render_artifact["artifact_id"],
        "expected_frames": expected_frames,
        "expected_duration_seconds": expected_duration,
        "checks": checks,
        "warnings": warnings,
    }
    report_path = root / "film_rebuild" / "quality" / "media_qa_report.json"
    _write_json(report_path, report)
    qa_artifact = _artifact_ref(
        root,
        report_path,
        kind="media_qa_report",
        producer_step="quality",
        input_artifact_ids=[render_artifact["artifact_id"]],
        metadata={"status": status},
    )
    on_progress(f"film quality: {status}")
    return {
        "qa_report_path": str(report_path),
        "qa_artifact": qa_artifact,
        "status": status,
        "checks": checks,
        "warnings": warnings,
        "artifacts": [qa_artifact],
    }


__all__ = [
    "prepare_film_sources",
    "build_film_frame_edl",
    "prepare_film_voice",
    "render_film_from_master",
    "quality_check_film_render",
]
