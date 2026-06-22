"""Storyboard node execution helpers for :mod:`pipeline_runner`."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.server import storyboard_director


@dataclass
class PipelineStoryboardRun:
    """`PipelineRunner._execute_storyboard` 的一次 director 分镜运行上下文。"""

    runner: Any
    job_id: str
    episode: dict[str, Any]
    beats_raw: list[dict[str, Any]]
    call_opus_for_rw: Callable[[str, str, str], str]
    model_id: str
    beats_in: list[dict[str, Any]] = field(init=False)
    style_bible: str = field(init=False)
    container_guide: str = field(init=False)
    palette: str = field(init=False)

    def __post_init__(self) -> None:
        # 组装 director 输入 beats（带 1-based index）
        self.beats_in = [
            {"index": i, "zh": str(b.get("zh") or ""), "en": str(b.get("en") or "")}
            for i, b in enumerate(self.beats_raw, start=1)
        ]
        image_cfg = self.episode.get("image") or {}
        self.style_bible = (
            str(image_cfg.get("sketchStylePrefix") or "").strip()
            or storyboard_director.DEFAULT_SKETCH_STYLE_PREFIX
        )
        self.container_guide = str(image_cfg.get("sketchContainerGuide") or "").strip()
        self.palette = str((self.episode.get("visual") or {}).get("palette") or "").strip()

    @property
    def episode_path(self) -> Path:
        return self.runner.video_jobs_dir / self.job_id / "02_rw" / "episode.json"

    def on_progress(self, text: str) -> None:
        self.runner._push_progress(self.job_id, "storyboard", text)

    async def run(self) -> dict[str, Any]:
        system_prompt, user_prompt = storyboard_director.build_director_prompt(
            self.episode.get("meta") or {},
            self.beats_in,
            style_bible=self.style_bible,
            container_guide=self.container_guide,
            palette=self.palette,
        )
        self.on_progress(f"调 director agent 分镜（{len(self.beats_in)} beats）…")
        raw = await asyncio.to_thread(
            self.call_opus_for_rw, user_prompt, system_prompt, self.model_id
        )

        scene_by_beat, scenes = storyboard_director.parse_director_output(raw, self.beats_raw)
        for i, b in enumerate(self.beats_raw, start=1):
            b["scene"] = scene_by_beat.get(i, b.get("scene") or "")
        self.episode["beats"] = self.beats_raw
        self.episode["scenes"] = scenes
        self.episode_path.write_text(json.dumps(self.episode, ensure_ascii=False, indent=2), encoding="utf-8")

        sketch_total = sum(len(s.get("sketches") or []) for s in scenes.values())
        groups = sorted({s.get("group") or sid for sid, s in scenes.items()})
        self.on_progress(
            f"完成：{len(groups)} 段 · {len(scenes)} 个子场景 · {sketch_total} 幅简笔画"
        )
        return {
            "episode_relpath": "02_rw/episode.json",
            "scenes_count": len(scenes),
            "sketches_count": sketch_total,
            "groups_count": len(groups),
            "beats_count": len(self.beats_raw),
        }
