"""Image node execution helpers for :mod:`pipeline_runner`.

The runner owns JobState/SSE concerns; this module owns the per-run image
orchestration details for the legacy facade path.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class PipelineImageRun:
    """`PipelineRunner._execute_image` 的一次图片批量生成运行上下文。"""

    runner: Any
    job_id: str
    job_dir: Path
    episode: dict[str, Any]
    generate_scene_image: Callable[..., Any]
    scene_order: list[str] = field(init=False)
    eligible: list[str] = field(init=False)
    scenes_def: dict[str, Any] = field(init=False)
    image_cfg: dict[str, Any] = field(init=False)
    size: str = field(init=False)
    quality: str = field(init=False)
    no_text_hint: str = field(init=False)
    sketch_size: str = field(init=False)
    sketch_prefix: str = field(init=False)
    out_dir: Path = field(init=False)
    items: list[dict[str, Any]] = field(default_factory=list)
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    sketch_ok: int = 0
    sketch_failed: int = 0

    def __post_init__(self) -> None:
        beats = self.episode.get("beats") or []
        self.scenes_def = self.episode.get("scenes") or {}
        self.image_cfg = self.episode.get("image") or {}

        # 出场顺序去重
        seen: set[str] = set()
        self.scene_order = []
        for b in beats:
            sid = b.get("scene")
            if sid and sid not in seen:
                seen.add(sid)
                self.scene_order.append(sid)

        self.eligible = [sid for sid in self.scene_order if not sid.startswith("ch")]
        if not self.eligible:
            raise ValueError("no image-eligible scenes (all are chapter cards or no scenes)")

        self.size = self.image_cfg.get("size") or "1536x1024"
        self.quality = self.image_cfg.get("quality") or "auto"
        self.no_text_hint = self.image_cfg.get("noTextHint") or ""
        # 简笔画：白底黑剪影方图，圣经前置 + 通用零文字负面后置；渲染层用
        # mix-blend-mode:multiply 抠掉白底，所以出图链路与容器图完全一样（不需透明）。
        self.sketch_size = self.image_cfg.get("sketchSize") or "1024x1024"
        self.sketch_prefix = str(self.image_cfg.get("sketchStylePrefix") or "").strip()

        self.out_dir = self.job_dir / "03_image"
        self.out_dir.mkdir(parents=True, exist_ok=True)

    @property
    def n_scenes(self) -> int:
        return len(self.scene_order)

    def on_progress(self, text: str) -> None:
        self.runner._push_progress(self.job_id, "image", text)

    async def run(self) -> dict[str, Any]:
        self.on_progress(f"image 开始：{len(self.eligible)} 个场景 · {self.size} {self.quality}")
        for i, sid in enumerate(self.scene_order, start=1):
            await self.process_scene(i, sid)

        if self.ok == 0 and self.failed > 0:
            raise RuntimeError(f"all {self.failed} scene image generations failed")

        self.on_progress(
            f"image 完成：容器 ok={self.ok} skipped={self.skipped} failed={self.failed} · "
            f"简笔画 ok={self.sketch_ok} failed={self.sketch_failed}"
        )
        return {
            "items": self.items,
            "pictures_dir": str(self.out_dir),
            "pictures_count": self.ok + self.skipped,
            "ok": self.ok,
            "skipped": self.skipped,
            "failed": self.failed,
            "sketch_ok": self.sketch_ok,
            "sketch_failed": self.sketch_failed,
        }

    async def process_scene(self, i: int, sid: str) -> None:
        sc = self.scenes_def.get(sid) or {}
        prompt = str(sc.get("prompt") or "").strip()
        if sid.startswith("ch"):
            self.items.append({"scene_id": sid, "prompt": prompt, "image_relpath": None,
                               "skipped_reason": "chapter card", "sketches": []})
            return
        if not prompt:
            self.items.append({"scene_id": sid, "prompt": "", "image_relpath": None,
                               "skipped_reason": "empty prompt", "sketches": []})
            self.failed += 1
            return

        # 容器图（背景底图）
        container_rel, container_err = await self.generate_container(i, sid, prompt)
        # 简笔画层（白底黑剪影，逐幅出；容器失败也照出，渲染层各管各的）
        sketch_items = await self.generate_sketches(i, sid, sc.get("sketches") or [])

        item: dict[str, Any] = {"scene_id": sid, "prompt": prompt,
                                "image_relpath": container_rel, "sketches": sketch_items}
        if container_err:
            item["error"] = container_err
        self.items.append(item)

    async def generate_container(self, i: int, sid: str, prompt: str) -> tuple[str | None, str | None]:
        target = self.out_dir / f"{sid}.webp"
        if target.is_file():
            self.skipped += 1
            self.on_progress(f"[{i}/{self.n_scenes}] {sid} 容器图已存在，跳过")
            return f"03_image/{sid}.webp", None

        full_prompt = f"{prompt} {self.no_text_hint}".strip() if self.no_text_hint else prompt
        self.on_progress(f"[{i}/{self.n_scenes}] {sid} 容器图生成中…")
        try:
            await asyncio.to_thread(
                self.generate_scene_image,
                scene_id=sid, prompt=full_prompt, size=self.size,
                quality=self.quality, target=target, job_id=self.job_id,
            )
            self.ok += 1
            return f"03_image/{sid}.webp", None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pipeline] image scene %s failed: %s", sid, exc)
            self.on_progress(f"[{i}/{self.n_scenes}] {sid} 容器图失败: {exc}")
            self.failed += 1
            return None, str(exc)

    async def generate_sketches(
        self, i: int, sid: str, sketches_def: list[Any],
    ) -> list[dict[str, Any]]:
        sketch_items: list[dict[str, Any]] = []
        for n, skd in enumerate(sketches_def, start=1):
            sp = str((skd or {}).get("prompt") or "").strip()
            if not sp:
                continue
            srel = f"03_image/{sid}-sk{n}.webp"
            stgt = self.out_dir / f"{sid}-sk{n}.webp"
            if stgt.is_file():
                sketch_items.append({"index": n, "prompt": sp, "image_relpath": srel})
                continue
            sfull = " ".join(p for p in (self.sketch_prefix, sp, self.no_text_hint) if p)
            self.on_progress(f"[{i}/{self.n_scenes}] {sid} 简笔画 {n}/{len(sketches_def)} 生成中…")
            try:
                await asyncio.to_thread(
                    self.generate_scene_image,
                    scene_id=f"{sid}-sk{n}", prompt=sfull, size=self.sketch_size,
                    quality=self.quality, target=stgt, job_id=self.job_id,
                )
                sketch_items.append({"index": n, "prompt": sp, "image_relpath": srel})
                self.sketch_ok += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("[pipeline] sketch %s-sk%d failed: %s", sid, n, exc)
                self.on_progress(f"[{i}/{self.n_scenes}] {sid} 简笔画 {n} 失败: {exc}")
                sketch_items.append({"index": n, "prompt": sp, "image_relpath": None,
                                     "error": str(exc)})
                self.sketch_failed += 1
        return sketch_items
