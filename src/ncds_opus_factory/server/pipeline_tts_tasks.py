"""TTS node execution helpers for :mod:`pipeline_runner`."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PipelineTtsRun:
    """`PipelineRunner._execute_tts` 的一次 015 配音运行上下文。"""

    runner: Any
    job_id: str
    job_dir: Path
    episode: dict[str, Any]
    tts_gen_script: Path
    run_tts_gen: Callable[..., None]
    rebuild_tts_items: Callable[[dict[str, Any]], list[dict[str, Any]]]
    beats_raw: list[Any] = field(init=False)
    out_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.beats_raw = self.episode.get("beats") or []
        if not self.beats_raw:
            raise ValueError("episode.beats is empty; nothing to synthesize")
        self.out_dir = self.job_dir / "04_tts"
        self.out_dir.mkdir(parents=True, exist_ok=True)

    @property
    def episode_path(self) -> Path:
        return self.job_dir / "02_rw" / "episode.json"

    def on_progress(self, text: str) -> None:
        self.runner._push_progress(self.job_id, "tts", text)

    async def run(self) -> dict[str, Any]:
        if not self.tts_gen_script.is_file():
            raise RuntimeError(f"tts_gen.py not found: {self.tts_gen_script}")

        self.on_progress(f"按 scene 整段合成（{len(self.beats_raw)} beats）…")
        await asyncio.to_thread(
            self.run_tts_gen,
            script=self.tts_gen_script,
            episode_path=self.episode_path,
            audio_dir=self.out_dir,
            on_line=self.on_progress,
        )

        # 读回 tts_gen 写好时间戳的 episode，组装 beat 级 items（audio 指向 scene mp3）
        ep2 = json.loads(self.episode_path.read_text(encoding="utf-8"))
        items = self.rebuild_tts_items(ep2)
        scene_files = {it["audio_relpath"] for it in items if it.get("audio_relpath")}
        self.on_progress(f"完成：{len(scene_files)} 段 scene 音频 · {len(items)} beats")
        return {
            "items": items,
            "audio_dir": str(self.out_dir),
            "mode": "segmented",
            "scene_count": len(scene_files),
            "audio_count": len(scene_files),
        }
