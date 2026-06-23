"""Render node execution helpers for :mod:`pipeline_runner`."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PipelineRenderRun:
    """`PipelineRunner._execute_render` 的一次 final_preview 渲染运行上下文。"""

    runner: Any
    job_id: str
    job_dir: Path
    render_run: Callable[..., dict[str, Any]]
    episode_path: Path = field(init=False)
    audio_dir: Path = field(init=False)
    picture_dir: Path = field(init=False)
    out_dir: Path = field(init=False)
    out_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.episode_path = self.job_dir / "02_rw" / "episode.json"
        if not self.episode_path.is_file():
            raise ValueError("02_rw/episode.json missing; select an rw model first")
        self.audio_dir = self.job_dir / "04_tts"
        if not self.audio_dir.is_dir() or not any(self.audio_dir.glob("*.mp3")):
            raise ValueError("04_tts/*.mp3 missing; run tts first")
        self.picture_dir = self.job_dir / "03_image"
        self.out_dir = self.job_dir / "06_render"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.out_path = self.out_dir / "output.mp4"

    def on_progress(self, text: str) -> None:
        self.runner._push_progress(self.job_id, "render", text)

    async def run(self) -> dict[str, Any]:
        self.on_progress("启动 render_final_preview（scene 整段合音）")
        result = await asyncio.to_thread(
            self.render_run,
            episode_path=str(self.episode_path),
            audio_dir=str(self.audio_dir),
            output_path=str(self.out_path),
            picture_dir=str(self.picture_dir) if self.picture_dir.is_dir() else None,
            workdir=str(self.out_dir / "_render_workdir"),
            cleanup_workdir=True,
            on_progress=self.on_progress,
        )
        return {
            "video_relpath": f"06_render/{self.out_path.name}",
            "output_path": result.get("output_path", str(self.out_path)),
            "video_size_bytes": result.get("video_size_bytes"),
            "workdir": result.get("workdir"),
        }
