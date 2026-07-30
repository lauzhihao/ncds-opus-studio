"""Offline steps for the authorised ``film_localization`` recipe.

This module imports only the source link recorded on the job, then uses the
job-local video for all later steps. It does not alter watermark/DRM data and
does not implement any platform-detection evasion. Every filesystem path is
resolved below the job directory before it is passed to ffmpeg or an ASR
provider.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ncds_opus_core.common import cancel
from ncds_opus_core.common.tts_provider import SynthSpec, get_provider

from ncds_opus_factory.common import capabilities
from ncds_opus_factory.common.opus_cli import DEFAULT_OPUS_MODEL
from ncds_opus_factory.server.pipeline_rw_helpers import _call_opus_for_rw

ProgressFn = Callable[[str], None]

_SOURCE_DIR = "00_source"
_ANALYSIS_DIR = "01_analysis"
_LOCALIZE_DIR = "02_localize"
_VOICE_DIR = "03_voice"
_RENDER_DIR = "04_render"
_FILM_SOURCE_SUFFIXES = {".mp4", ".mov", ".mkv"}
_SOURCE_PLATFORMS = {"douyin", "tiktok", "youtube"}
_SOURCE_HOSTS = {
    "douyin": ("douyin.com",),
    "tiktok": ("tiktok.com",),
    "youtube": ("youtube.com", "youtu.be"),
}
_DEFAULT_FILM_MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024


def _safe_job_file(job_dir: Path, relpath: str) -> Path:
    """Resolve a job-local artifact and reject a malformed persisted path."""
    root = job_dir.resolve()
    target = (root / relpath).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("film artifact path escapes job directory") from exc
    return target


def _source_video(job_dir: Path, inputs: dict[str, Any]) -> tuple[Path, str]:
    if inputs.get("rights_confirmed") is not True:
        raise ValueError("rights_confirmed=true is required before film processing")
    relpath = str(inputs.get("source_video") or "").strip()
    if not relpath:
        raise ValueError("source video missing; import it before running analyze")
    source = _safe_job_file(job_dir, relpath)
    if not source.is_file() or source.parent.name != _SOURCE_DIR:
        raise ValueError("imported source video is unavailable")
    if source.suffix.lower() not in _FILM_SOURCE_SUFFIXES:
        raise ValueError("imported source video type is not supported")
    return source, relpath


def _film_max_source_bytes() -> int:
    raw = os.getenv("NOF_FILM_SOURCE_MAX_BYTES") or os.getenv("NOF_FILM_MAX_UPLOAD_BYTES")
    if not raw:
        return _DEFAULT_FILM_MAX_SOURCE_BYTES
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_FILM_MAX_SOURCE_BYTES
    return value if value > 0 else _DEFAULT_FILM_MAX_SOURCE_BYTES


def _source_reference(inputs: dict[str, Any]) -> tuple[str, str, str, str]:
    """Validate a resolver-produced public work reference before importing it."""
    if inputs.get("rights_confirmed") is not True:
        raise ValueError("rights confirmation is required before film processing")
    ref = inputs.get("source_ref")
    if not isinstance(ref, dict):
        raise ValueError("source reference is missing")
    platform = str(ref.get("platform") or "").strip().lower()
    work_id = str(ref.get("work_id") or "").strip()
    source_url = str(ref.get("source_url") or "").strip()
    title = str(ref.get("title") or "").strip()
    if platform not in _SOURCE_PLATFORMS:
        raise ValueError("source platform is not supported")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", work_id):
        raise ValueError("source work id is invalid")
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or not any(host == suffix or host.endswith(f".{suffix}") for suffix in _SOURCE_HOSTS[platform]):
        raise ValueError("source URL is not a supported platform link")
    return platform, work_id, source_url, title


def run_import(*, job_dir: str, inputs: dict[str, Any], on_progress: ProgressFn) -> dict[str, Any]:
    """Import one authorized resolver-produced source into ``00_source``."""
    root = Path(job_dir)
    platform, work_id, source_url, title = _source_reference(inputs)
    source_dir = root / _SOURCE_DIR
    source_dir.mkdir(parents=True, exist_ok=True)
    final_path = source_dir / "source.mp4"
    partial_path = source_dir / "source.download.mp4"
    if final_path.is_file() and final_path.stat().st_size > 0:
        size_bytes = final_path.stat().st_size
        on_progress("Reusing imported source video")
    else:
        partial_path.unlink(missing_ok=True)
        on_progress("Importing source video from share link")
        capabilities.fetch_and_download(
            work_id,
            partial_path,
            on_progress=on_progress,
            check=cancel.current(),
            platform=platform,
            source_url=source_url,
            wait_for_completion=True,
        )
        cancel.checkpoint()
        if not partial_path.is_file() or partial_path.stat().st_size == 0:
            raise RuntimeError("source import produced no video")
        size_bytes = partial_path.stat().st_size
        max_bytes = _film_max_source_bytes()
        if size_bytes > max_bytes:
            partial_path.unlink(missing_ok=True)
            raise RuntimeError(f"source exceeds configured limit of {max_bytes} bytes")
        partial_path.replace(final_path)
    source_relpath = f"{_SOURCE_DIR}/{final_path.name}"
    source_meta = {
        "path": source_relpath,
        "size_bytes": size_bytes,
        "platform": platform,
        "work_id": work_id,
        "source_url": source_url,
        "title": title,
        "rights_confirmed": True,
    }
    on_progress("Source import complete")
    return {"source_video": source_relpath, "source": source_meta}


def _run_checked(args: list[str], label: str) -> subprocess.CompletedProcess[str]:
    """Run a fixed ffmpeg/ffprobe argv list and keep error output bounded."""
    try:
        proc = subprocess.run(  # noqa: S603 - argv is fixed by this module
            args, check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} executable not found") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise RuntimeError(f"{label} failed: {detail}")
    return proc


def _probe_duration(source: Path) -> float:
    proc = _run_checked(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)],
        "ffprobe",
    )
    try:
        value = float((json.loads(proc.stdout).get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("ffprobe returned an invalid duration") from exc
    if value <= 0:
        raise RuntimeError("source video duration is unavailable")
    return value


def _extract_audio(source: Path, out_path: Path) -> None:
    _run_checked(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(out_path)],
        "ffmpeg audio extraction",
    )


def _segments_from_asr(raw: dict[str, Any] | None, text: str, duration_s: float) -> list[dict[str, Any]]:
    response = raw.get("rawResponse") if isinstance(raw, dict) else None
    raw_segments = response.get("segments") if isinstance(response, dict) else None
    segments: list[dict[str, Any]] = []
    if isinstance(raw_segments, list):
        for segment in raw_segments:
            if not isinstance(segment, dict):
                continue
            segment_text = str(segment.get("text") or "").strip()
            try:
                start = max(0.0, float(segment.get("start") or 0))
                end = min(duration_s, float(segment.get("end") or duration_s))
            except (TypeError, ValueError):
                continue
            if segment_text and end > start:
                segments.append({"start_s": round(start, 3), "end_s": round(end, 3), "text_zh": segment_text})
    if segments:
        return segments
    return [{"start_s": 0.0, "end_s": round(duration_s, 3), "text_zh": text.strip()}]


def run_analyze(*, job_dir: str, inputs: dict[str, Any], on_progress: ProgressFn) -> dict[str, Any]:
    """Probe, extract audio, and transcribe the uploaded Chinese source video."""
    root = Path(job_dir)
    source, source_relpath = _source_video(root, inputs)
    out_dir = root / _ANALYSIS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    audio = out_dir / "source_audio.wav"

    on_progress("Probing source video")
    duration_s = _probe_duration(source)
    on_progress("Extracting source audio")
    _extract_audio(source, audio)
    on_progress("Transcribing Chinese source audio")
    raw, transcript_zh = capabilities.transcribe(audio, on_progress=on_progress, language="zh")
    transcript_zh = (transcript_zh or "").strip()
    if not transcript_zh:
        raise RuntimeError("ASR returned an empty Chinese transcript")
    cleaned = capabilities.clean_transcript(transcript_zh, on_progress=on_progress) or transcript_zh
    timeline = {
        "source_video": source_relpath,
        "duration_s": round(duration_s, 3),
        "transcript_zh": cleaned,
        "segments": _segments_from_asr(raw, cleaned, duration_s),
    }
    path = out_dir / "timeline.json"
    path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    on_progress("Analysis complete")
    return {"timeline_relpath": f"{_ANALYSIS_DIR}/timeline.json", "duration_s": timeline["duration_s"], "transcript_zh": cleaned}


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is missing or invalid") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _subtitle_chunks(text: str, duration_s: float, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate edit-friendly bilingual subtitle intervals without guessing cuts."""
    sentences = [piece.strip() for piece in re.split(r"(?<=[.!?])\s+|\n+", text) if piece.strip()]
    if not sentences:
        sentences = [text.strip()]
    zh_by_index = [str(item.get("text_zh") or "").strip() for item in segments if isinstance(item, dict)]
    weights = [max(1, len(sentence)) for sentence in sentences]
    total = sum(weights)
    cursor = 0.0
    subtitles: list[dict[str, Any]] = []
    for index, (sentence, weight) in enumerate(zip(sentences, weights, strict=True)):
        end = duration_s if index == len(sentences) - 1 else min(duration_s, cursor + duration_s * weight / total)
        zh = zh_by_index[min(index, len(zh_by_index) - 1)] if zh_by_index else ""
        subtitles.append({"start_s": round(cursor, 3), "end_s": round(end, 3), "text_zh": zh, "text_en": sentence})
        cursor = end
    return subtitles


def _srt_time(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _write_srt(subtitles: list[dict[str, Any]], path: Path) -> None:
    blocks: list[str] = []
    for index, subtitle in enumerate(subtitles, start=1):
        zh = str(subtitle.get("text_zh") or "").strip()
        en = str(subtitle.get("text_en") or "").strip()
        blocks.append(
            f"{index}\n{_srt_time(float(subtitle['start_s']))} --> {_srt_time(float(subtitle['end_s']))}\n{zh}\n{en}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")


def run_localize(*, job_dir: str, on_progress: ProgressFn) -> dict[str, Any]:
    """Use the existing Opus runner for English localization, then persist subtitles."""
    root = Path(job_dir)
    timeline = _load_json(root / _ANALYSIS_DIR / "timeline.json", "timeline.json")
    transcript_zh = str(timeline.get("transcript_zh") or "").strip()
    if not transcript_zh:
        raise ValueError("timeline transcript_zh is empty")
    on_progress("Localizing Chinese transcript into English")
    prompt = (
        "Localize the following Chinese narration into natural spoken English. Preserve factual claims, names, "
        "and uncertainty. Keep a similar narration length and pacing. Output only the English voiceover script; "
        "do not add notes, markdown, or labels.\n\nChinese transcript:\n" + transcript_zh
    )
    script_en = _call_opus_for_rw(prompt, "You are a careful audiovisual localization editor.", DEFAULT_OPUS_MODEL).strip()
    if not script_en:
        raise RuntimeError("Opus returned an empty English script")
    duration_s = float(timeline.get("duration_s") or 0)
    if duration_s <= 0:
        raise ValueError("timeline duration_s is invalid")
    segments = timeline.get("segments") if isinstance(timeline.get("segments"), list) else []
    subtitles = _subtitle_chunks(script_en, duration_s, segments)
    out_dir = root / _LOCALIZE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path = out_dir / "bilingual.srt"
    _write_srt(subtitles, srt_path)
    localization = {"script_en": script_en, "subtitles": subtitles, "subtitle_relpath": f"{_LOCALIZE_DIR}/bilingual.srt"}
    (out_dir / "localization.json").write_text(json.dumps(localization, ensure_ascii=False, indent=2), encoding="utf-8")
    on_progress("Localization complete")
    return {"localization_relpath": f"{_LOCALIZE_DIR}/localization.json", "subtitle_relpath": localization["subtitle_relpath"], "script_en": script_en, "subtitles": subtitles}


def run_voice(*, job_dir: str, on_progress: ProgressFn) -> dict[str, Any]:
    """Synthesize the localized English script through the configured standard TTS provider."""
    root = Path(job_dir)
    localization = _load_json(root / _LOCALIZE_DIR / "localization.json", "localization.json")
    script_en = str(localization.get("script_en") or "").strip()
    if not script_en:
        raise ValueError("localization script_en is empty")
    out_dir = root / _VOICE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "voice.mp3"
    provider_name = os.getenv("NOF_FILM_TTS_PROVIDER", "cosyvoice").strip() or "cosyvoice"
    provider = get_provider(provider_name)
    default = provider.default_spec
    voice = os.getenv("NOF_FILM_TTS_VOICE", default.voice).strip() or default.voice
    spec = SynthSpec(voice=voice, rate=default.rate, sample_rate=default.sample_rate, audio_format="mp3", model=default.model)
    on_progress(f"Synthesizing English voice with {provider_name}")
    provider.synth(script_en, output, spec, on_progress=on_progress)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("TTS provider produced no audio file")
    on_progress("Voice synthesis complete")
    return {"voice_relpath": f"{_VOICE_DIR}/voice.mp3", "tts_provider": provider_name}


def _ffmpeg_filter_path(path: Path) -> str:
    """Escape a local path embedded in an ffmpeg subtitles filter value."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("[", "\\[").replace("]", "\\]").replace(",", "\\,")


def run_render(*, job_dir: str, inputs: dict[str, Any], on_progress: ProgressFn) -> dict[str, Any]:
    """Render portrait source video, replacing original audio with English voiceover."""
    root = Path(job_dir)
    source, _ = _source_video(root, inputs)
    voice = _safe_job_file(root, f"{_VOICE_DIR}/voice.mp3")
    subtitles = _safe_job_file(root, f"{_LOCALIZE_DIR}/bilingual.srt")
    if not voice.is_file() or not subtitles.is_file():
        raise ValueError("voice or bilingual subtitles are missing")
    out_dir = root / _RENDER_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "output.mp4"
    subtitle_path = _ffmpeg_filter_path(subtitles)
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        f"subtitles='{subtitle_path}':force_style='FontSize=20,Alignment=2,Outline=2,Shadow=1,MarginV=70'"
    )
    on_progress("Rendering portrait MP4 with English voice and bilingual subtitles")
    _run_checked(
        [
            "ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(source), "-i", str(voice),
            "-map", "0:v:0", "-map", "1:a:0", "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(output),
        ],
        "ffmpeg render",
    )
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("render produced no MP4")
    on_progress("Render complete")
    return {"video_relpath": f"{_RENDER_DIR}/output.mp4", "video_size_bytes": output.stat().st_size}
