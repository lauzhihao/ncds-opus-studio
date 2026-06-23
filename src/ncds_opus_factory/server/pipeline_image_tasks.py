"""Image node execution helpers for :mod:`pipeline_runner`.

The runner owns JobState/SSE concerns; this module owns the per-run image
orchestration details for the legacy facade path.
"""

from __future__ import annotations

import asyncio
import filecmp
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ncds_opus_core.common import cancel as _cancel

logger = logging.getLogger(__name__)


def _bounded_int(value: Any, default: int, *, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _bounded_float(value: Any, default: float, *, lo: float, hi: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _is_retryable_image_error(exc: Exception) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(
        token in msg
        for token in (
            "timeout",
            "timed out",
            "read operation timed out",
            "http 429",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
            "rate limit",
            "upstream",
            "connection reset",
            "temporarily",
        )
    )


def _friendly_image_error(exc: Exception) -> str:
    raw = str(exc).strip()
    if raw.startswith("图片"):
        return raw
    msg = f"{type(exc).__name__}: {exc}".lower()
    if "timeout" in msg or "timed out" in msg or "read operation timed out" in msg:
        return "图片服务响应超时，请稍后重试。"
    if "http 429" in msg or "rate limit" in msg:
        return "图片服务请求过于频繁，请稍后重试。"
    if "http 502" in msg or "http 503" in msg or "http 504" in msg or "upstream" in msg:
        return "图片服务暂时不可用，请稍后重试。"
    return "图片生成失败，详细错误已写入服务日志。"


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
    container_n: int = field(init=False)
    sketch_n: int = field(init=False)
    workers: int = field(init=False)
    retries: int = field(init=False)
    retry_backoff_seconds: float = field(init=False)
    no_text_hint: str = field(init=False)
    sketch_size: str = field(init=False)
    sketch_prefix: str = field(init=False)
    out_dir: Path = field(init=False)
    image_sem: asyncio.Semaphore = field(init=False)
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
        self.container_n = _bounded_int(
            self.image_cfg.get("n", self.image_cfg.get("containerN", self.image_cfg.get("candidates"))),
            4,
            lo=1,
            hi=4,
        )
        self.sketch_n = _bounded_int(self.image_cfg.get("sketchN"), 1, lo=1, hi=4)
        self.workers = _bounded_int(
            self.image_cfg.get("workers", self.image_cfg.get("concurrency")),
            4,
            lo=1,
            hi=8,
        )
        self.retries = _bounded_int(
            self.image_cfg.get("retries", self.image_cfg.get("imageRetries", os.getenv("NOF_PIPELINE_IMAGE_RETRIES"))),
            2,
            lo=0,
            hi=5,
        )
        self.retry_backoff_seconds = _bounded_float(
            self.image_cfg.get("retryBackoffSeconds", os.getenv("NOF_PIPELINE_IMAGE_RETRY_BACKOFF")),
            2.0,
            lo=0.0,
            hi=30.0,
        )
        self.no_text_hint = self.image_cfg.get("noTextHint") or ""
        # 简笔画：白底黑剪影方图，圣经前置 + 通用零文字负面后置；渲染层用
        # mix-blend-mode:multiply 抠掉白底，所以出图链路与容器图完全一样（不需透明）。
        self.sketch_size = self.image_cfg.get("sketchSize") or "1024x1024"
        self.sketch_prefix = str(self.image_cfg.get("sketchStylePrefix") or "").strip()

        self.out_dir = self.job_dir / "03_image"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.image_sem = asyncio.Semaphore(self.workers)

    @property
    def n_scenes(self) -> int:
        return len(self.scene_order)

    def on_progress(self, text: str) -> None:
        self.runner._push_progress(self.job_id, "image", text)

    async def run(self) -> dict[str, Any]:
        self.on_progress(
            f"image 开始：{len(self.eligible)} 个场景 · {self.size} n={self.container_n} · "
            f"并发 {self.workers}"
        )
        tasks = [
            asyncio.create_task(self.process_scene(i, sid))
            for i, sid in enumerate(self.scene_order, start=1)
        ]
        self.items = await asyncio.gather(*tasks)

        if self.ok == 0 and self.failed > 0:
            raise RuntimeError("所有画面资产都生成失败，请稍后重试；详细错误已写入服务日志。")

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

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.job_dir).as_posix()

    def _variant_target(self, sid: str, index: int) -> Path:
        return self.out_dir / f"{sid}-v{index}.webp"

    def _existing_variants(self, sid: str, target: Path, count: int) -> list[dict[str, Any]]:
        if count <= 1:
            return [{"index": 1, "image_relpath": self._rel(target)}] if target.is_file() else []
        out: list[dict[str, Any]] = []
        for index in range(1, count + 1):
            p = self._variant_target(sid, index)
            if p.is_file():
                out.append({"index": index, "image_relpath": self._rel(p)})
        return out

    def _selected_variant_relpath(self, target: Path, variants: list[dict[str, Any]]) -> str | None:
        if not variants:
            return None
        if target.is_file():
            for variant in variants:
                rel = str(variant.get("image_relpath") or "")
                path = self.job_dir / rel
                try:
                    if path.is_file() and filecmp.cmp(target, path, shallow=False):
                        return rel
                except OSError:
                    continue
        return str(variants[0].get("image_relpath") or "") or None

    async def process_scene(self, i: int, sid: str) -> dict[str, Any]:
        sc = self.scenes_def.get(sid) or {}
        prompt = str(sc.get("prompt") or "").strip()
        if sid.startswith("ch"):
            return {"scene_id": sid, "prompt": prompt, "image_relpath": None,
                    "skipped_reason": "chapter card", "sketches": []}
        if not prompt:
            self.failed += 1
            return {"scene_id": sid, "prompt": "", "image_relpath": None,
                    "skipped_reason": "empty prompt", "sketches": []}

        # 容器图（背景底图）
        container_rel, variants, container_err = await self.generate_container(i, sid, prompt)
        # 简笔画层（白底黑剪影，逐幅出；容器失败也照出，渲染层各管各的）
        sketch_items = await self.generate_sketches(i, sid, sc.get("sketches") or [])

        item: dict[str, Any] = {"scene_id": sid, "prompt": prompt,
                                "image_relpath": container_rel, "sketches": sketch_items}
        if variants:
            selected_variant = self._selected_variant_relpath(self.out_dir / f"{sid}.webp", variants)
            item["variants"] = [
                {**variant, "selected": variant.get("image_relpath") == selected_variant}
                for variant in variants
            ]
            item["selected_variant_relpath"] = selected_variant
        if container_err:
            item["error"] = container_err
        return item

    async def generate_container(
        self,
        i: int,
        sid: str,
        prompt: str,
    ) -> tuple[str | None, list[dict[str, Any]], str | None]:
        target = self.out_dir / f"{sid}.webp"
        if target.is_file():
            variants = self._existing_variants(sid, target, self.container_n)
            if self.container_n <= 1 or len(variants) >= self.container_n:
                self.skipped += 1
                self.on_progress(f"[{i}/{self.n_scenes}] {sid} 容器图候选已存在，跳过")
                return self._rel(target), variants, None
            self.on_progress(f"[{i}/{self.n_scenes}] {sid} 容器图候选不足，补生成 n={self.container_n}…")

        full_prompt = f"{prompt} {self.no_text_hint}".strip() if self.no_text_hint else prompt
        self.on_progress(f"[{i}/{self.n_scenes}] {sid} 容器图生成中 n={self.container_n}…")
        try:
            paths = await self.generate_with_retries(
                i=i,
                sid=sid,
                label="容器图",
                scene_id=sid,
                prompt=full_prompt,
                size=self.size,
                quality=self.quality,
                target=target,
                n=self.container_n,
            )
            self.ok += 1
            variants = [
                {"index": index, "image_relpath": self._rel(path)}
                for index, path in enumerate(paths, start=1)
            ]
            return self._rel(target), variants, None
        except _cancel.TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            friendly = _friendly_image_error(exc)
            logger.warning("[pipeline] image scene %s failed: %s", sid, friendly)
            self.on_progress(f"[{i}/{self.n_scenes}] {sid} 容器图失败：{friendly}")
            self.failed += 1
            return None, [], friendly

    async def generate_with_retries(
        self,
        *,
        i: int,
        sid: str,
        label: str,
        scene_id: str,
        prompt: str,
        size: str,
        quality: str,
        target: Path,
        n: int,
    ) -> list[Path]:
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                async with self.image_sem:
                    return await asyncio.to_thread(
                        self.generate_scene_image,
                        scene_id=scene_id,
                        prompt=prompt,
                        size=size,
                        quality=quality,
                        target=target,
                        job_id=self.job_id,
                        n=n,
                    )
            except _cancel.TaskCancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                friendly = _friendly_image_error(exc)
                logger.warning(
                    "[pipeline] image asset failed: scene=%s label=%s attempt=%d/%d summary=%s",
                    scene_id,
                    label,
                    attempt,
                    attempts,
                    friendly,
                    exc_info=True,
                )
                if attempt < attempts and _is_retryable_image_error(exc):
                    delay = self.retry_backoff_seconds * attempt
                    suffix = f"{delay:g}s 后" if delay > 0 else ""
                    self.on_progress(
                        f"[{i}/{self.n_scenes}] {sid} {label}暂时失败，{suffix}重试 "
                        f"{attempt}/{self.retries}：{friendly}"
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
                raise RuntimeError(friendly) from exc
        raise RuntimeError("图片生成失败，详细错误已写入服务日志。")

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
                paths = await self.generate_with_retries(
                    i=i,
                    sid=sid,
                    label=f"简笔画 {n}",
                    scene_id=f"{sid}-sk{n}",
                    prompt=sfull,
                    size=self.sketch_size,
                    quality=self.quality,
                    target=stgt,
                    n=self.sketch_n,
                )
                item: dict[str, Any] = {"index": n, "prompt": sp, "image_relpath": srel}
                if self.sketch_n > 1:
                    item["variants"] = [
                        {"index": index, "image_relpath": self._rel(path)}
                        for index, path in enumerate(paths, start=1)
                    ]
                sketch_items.append(item)
                self.sketch_ok += 1
            except _cancel.TaskCancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                friendly = _friendly_image_error(exc)
                logger.warning("[pipeline] sketch %s-sk%d failed: %s", sid, n, friendly)
                self.on_progress(f"[{i}/{self.n_scenes}] {sid} 简笔画 {n} 失败：{friendly}")
                sketch_items.append({"index": n, "prompt": sp, "image_relpath": None,
                                     "error": friendly})
                self.sketch_failed += 1
        return sketch_items
