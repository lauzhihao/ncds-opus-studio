"""Engine performers for the deterministic film rebuild recipes."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ncds_opus_factory.commands.film_commentary import build_film_commentary
from ncds_opus_factory.commands.film_rebuild import (
    build_film_frame_edl,
    prepare_film_sources,
    prepare_film_voice,
    quality_check_film_render,
    render_film_from_master,
)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def run_film_source_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    reference_path: str,
    master_path: str,
    master_audio_stream: int = 0,
    **_: Any,
) -> dict[str, Any]:
    return prepare_film_sources(
        job_dir,
        reference_path,
        master_path,
        master_audio_stream=master_audio_stream,
        on_progress=on_progress,
    )


def run_film_guiguzi_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    sources: list[dict[str, Any]] | None = None,
    collected: list[dict[str, Any]] | None = None,
    items: list[dict[str, Any]] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Promote the existing film commentary cleaner into a synchronous step."""
    source_rows = list(sources or collected or items or [])
    if not source_rows:
        raise ValueError("film guiguzi requires Shenkuo collected sources")
    result = build_film_commentary(
        job_dir,
        source_rows,
        on_progress=on_progress,
    )
    path = Path(job_dir) / "guiguzi.json"
    _write_json(path, result)
    return {**result, "guiguzi_path": str(path)}


def run_film_storyboard_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    source_manifest_path: str,
    edl_path: str,
    profile: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    return build_film_frame_edl(
        job_dir,
        source_manifest_path,
        edl_path,
        profile=profile,
        on_progress=on_progress,
    )


def run_film_tts_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    voice_path: str,
    edl_manifest_path: str,
    subtitle_path: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    return prepare_film_voice(
        job_dir,
        voice_path,
        edl_manifest_path,
        subtitle_path=subtitle_path,
        on_progress=on_progress,
    )


def run_film_render_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    source_manifest_path: str,
    edl_manifest_path: str,
    voice_manifest_path: str,
    render_profile: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    return render_film_from_master(
        job_dir,
        source_manifest_path,
        edl_manifest_path,
        voice_manifest_path,
        render_profile=render_profile,
        on_progress=on_progress,
    )


def run_film_quality_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    render_manifest_path: str,
    **_: Any,
) -> dict[str, Any]:
    return quality_check_film_render(
        job_dir,
        render_manifest_path,
        on_progress=on_progress,
    )


PERFORMERS_FILM: dict[str, Callable[..., dict[str, Any]]] = {
    "film_source": run_film_source_step,
    "film_guiguzi": run_film_guiguzi_step,
    "film_storyboard": run_film_storyboard_step,
    "film_tts": run_film_tts_step,
    "film_render": run_film_render_step,
    "film_quality": run_film_quality_step,
}


__all__ = [
    "PERFORMERS_FILM",
    "run_film_source_step",
    "run_film_guiguzi_step",
    "run_film_storyboard_step",
    "run_film_tts_step",
    "run_film_render_step",
    "run_film_quality_step",
]
