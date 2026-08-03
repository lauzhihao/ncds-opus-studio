"""Shenkuo film source collector for ``film_script_source.v3``.

The v2 OCR-first implementation was intentionally removed.  This facade keeps
download/cache orchestration here and delegates ASR-first multimodal script
extraction to :mod:`film_script_v3`.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ncds_opus_core.common import cancel

from ncds_opus_factory.commands import shenkuo
from ncds_opus_factory.commands.film_script_v3 import extract_video_subtitles_v3
from ncds_opus_factory.common import tikhub_client, works_repo

ProgressFn = Callable[[str], None]

_ROOT = Path(__file__).resolve().parents[3]
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


def extract_video_subtitles(
    video_path: str | Path,
    *,
    platform: str,
    work_id: str,
    asr_timeline: str | Path | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Deliver the breaking ASR-first ``film_script_source.v3`` contract."""
    return extract_video_subtitles_v3(
        video_path,
        platform=platform,
        work_id=work_id,
        asr_timeline=asr_timeline,
        on_progress=on_progress,
    )


def _script_text(film_source: dict[str, Any]) -> str:
    script = film_source.get("commentary_script")
    if not isinstance(script, dict):
        return ""
    value = script.get("txt")
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


def _collect_local_video(source: Path, *, on_progress: ProgressFn) -> dict[str, Any]:
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
        "status": {"download": "local", "transcribe": "ready"},
        "film_source": film_source,
        "text": _script_text(film_source),
        "quality_status": film_source.get("quality_status"),
        "publishable": bool(film_source.get("publishable")),
    }


def _resolve_remote_entry(
    value: str,
    *,
    collect_dir: Path,
    share: dict[str, Any],
    on_progress: ProgressFn,
) -> dict[str, Any]:
    ref = tikhub_client.resolve_video_ref(value)
    if ref is None:
        raise ValueError(f"unsupported film source: {value}")
    meta: dict[str, Any] = {}
    try:
        if ref.platform == "douyin":
            meta = tikhub_client.extract_meta(
                tikhub_client.fetch_one_video_detail(ref.video_id)
            )
        else:
            meta = tikhub_client.fetch_video_ref_meta(ref)
    except Exception as exc:  # noqa: BLE001 - metadata is optional.
        on_progress(
            "Film source metadata unavailable; "
            f"continuing: {type(exc).__name__}"
        )
        if ref.platform != "douyin":
            meta = tikhub_client.video_ref_meta(ref)
    if share.get("title") and not meta.get("desc"):
        meta["desc"] = str(share["title"])
    if share.get("author") and not meta.get("author"):
        meta["author"] = str(share["author"])

    # Shenkuo always transcribes after download.  do_audio controls only the
    # expensive Demucs stem separation, which v3 does not need for validation.
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
    entry["text"] = _script_text(entry["film_source"])
    entry["quality_status"] = entry["film_source"].get("quality_status")
    entry["publishable"] = bool(entry["film_source"].get("publishable"))
    return entry


def collect_film_subtitles(
    job_dir: str | Path,
    urls: list[str],
    shares: list[dict[str, Any]] | None = None,
    *,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """Collect one or more film sources and return v3 review drafts."""
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
                entry = _resolve_remote_entry(
                    value,
                    collect_dir=collect_dir,
                    share=shares_by_url.get(value) or {},
                    on_progress=on_progress,
                )
            entry["index"] = index
            entry["url"] = value
            collected.append(entry)
        except cancel.TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - retain per-source audit.
            collected.append({
                "index": index,
                "url": value,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
    if not any(isinstance(entry.get("film_source"), dict) for entry in collected):
        raise RuntimeError("film v3 collection produced no usable source")
    return {
        "collected": collected,
        "items": collected,
        "collect_dir": str(collect_dir),
    }
