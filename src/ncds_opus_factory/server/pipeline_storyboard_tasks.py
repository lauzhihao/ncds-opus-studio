"""Storyboard node execution helpers for :mod:`pipeline_runner`."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.server import storyboard_director
from ncds_opus_factory.server.pipeline_llm_fallback import structure_json_with_model_fallback


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
        visual = await asyncio.to_thread(
            structure_json_with_model_fallback,
            user_prompt,
            system_prompt,
            self.on_progress,
            parse=lambda raw: storyboard_director.parse_director_output(raw, self.beats_raw),
            target_profile="paper_card_talk_storyboard",
            start_progress="正在生成视觉方案...",
            success_progress="视觉方案生成完成",
            failover_progress="当前通道未成功，正在切换备用通道...",
            final_error="视觉方案生成暂时失败：备用通道都没有成功，请稍后重试。",
            log_context="storyboard",
            max_parse_attempts=3,
            retry_hint="JSON 必须含 visual.stage 与 visual.shots[]，每条 shot 必须有 beatIndex。",
        )
        shots = visual.get("shots") if isinstance(visual.get("shots"), list) else []
        group_by_beat = {
            int(shot.get("beatIndex")): str(shot.get("group") or "").strip()
            for shot in shots
            if isinstance(shot, dict) and shot.get("beatIndex") is not None
        }
        for i, b in enumerate(self.beats_raw, start=1):
            # scene 保留为 TTS 粗分段，不再驱动画面切换。
            b["scene"] = group_by_beat.get(i) or b.get("scene") or "main"
        self.episode["beats"] = self.beats_raw
        prev_visual = self.episode.get("visual") if isinstance(self.episode.get("visual"), dict) else {}
        self.episode["visual"] = {**dict(prev_visual or {}), **visual}
        self.episode["scenes"] = {}
        image_cfg = self.episode.get("image") if isinstance(self.episode.get("image"), dict) else {}
        image_cfg = dict(image_cfg or {})
        stage = visual.get("stage") if isinstance(visual.get("stage"), dict) else {}
        background = stage.get("background") if isinstance(stage.get("background"), dict) else {}
        image_cfg["background"] = dict(background or {})
        self.episode["image"] = image_cfg
        self.episode_path.write_text(json.dumps(self.episode, ensure_ascii=False, indent=2), encoding="utf-8")

        asset_total = sum(len(s.get("assets") or []) for s in shots if isinstance(s, dict))
        groups = sorted({str(s.get("group") or "").strip() for s in shots if isinstance(s, dict) and s.get("group")})
        self.on_progress(
            f"视觉方案生成完成：{len(shots)} 句画面 · {len(groups)} 段 · {asset_total} 个前景素材"
        )
        return {
            "episode_relpath": "02_rw/episode.json",
            "shots_count": len(shots),
            "assets_count": asset_total,
            "groups_count": len(groups),
            "beats_count": len(self.beats_raw),
            "background_count": 1,
        }
