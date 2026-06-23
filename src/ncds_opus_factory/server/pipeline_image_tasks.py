"""Image node execution helpers for :mod:`pipeline_runner`.

The runner owns JobState/SSE concerns; this module owns WuDaozi image
orchestration. The current visual contract is ``episode.visual.shots[]``:
one stable stage background plus foreground assets for each subtitle-driven
shot. ``beats[].scene`` is intentionally not used for visual switching here.
"""

from __future__ import annotations

import asyncio
import filecmp
import json
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


def _safe_slug(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw).strip("-")
    return safe or fallback


def _shot_id(shot: dict[str, Any], index: int) -> str:
    return _safe_slug(shot.get("shotId") or shot.get("id"), f"b{index:03d}")


def _asset_id(asset: dict[str, Any], index: int) -> str:
    return _safe_slug(asset.get("id"), f"a{index}")


@dataclass
class PipelineImageRun:
    """`PipelineRunner._execute_image` 的一次图片批量生成运行上下文。"""

    runner: Any
    job_id: str
    job_dir: Path
    episode: dict[str, Any]
    generate_scene_image: Callable[..., Any]
    visual: dict[str, Any] = field(init=False)
    shots: list[dict[str, Any]] = field(init=False)
    image_cfg: dict[str, Any] = field(init=False)
    size: str = field(init=False)
    quality: str = field(init=False)
    background_n: int = field(init=False)
    asset_n: int = field(init=False)
    workers: int = field(init=False)
    retries: int = field(init=False)
    retry_backoff_seconds: float = field(init=False)
    no_text_hint: str = field(init=False)
    asset_size: str = field(init=False)
    asset_prefix: str = field(init=False)
    background_cfg: dict[str, Any] = field(init=False)
    background: dict[str, Any] = field(init=False)
    out_dir: Path = field(init=False)
    image_sem: asyncio.Semaphore = field(init=False)
    items: list[dict[str, Any]] = field(default_factory=list)
    item_by_shot: dict[str, dict[str, Any]] = field(default_factory=dict)
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    asset_ok: int = 0
    asset_skipped: int = 0
    asset_failed: int = 0

    def __post_init__(self) -> None:
        self.visual = self.episode.get("visual") if isinstance(self.episode.get("visual"), dict) else {}
        raw_shots = self.visual.get("shots") if isinstance(self.visual.get("shots"), list) else []
        self.shots = [s for s in raw_shots if isinstance(s, dict)]
        self.shots.sort(key=lambda s: int(s.get("beatIndex") or 0))
        if not self.shots:
            raise ValueError("episode.visual.shots is empty; run storyboard first")

        self.image_cfg = self.episode.get("image") if isinstance(self.episode.get("image"), dict) else {}
        self.size = self.image_cfg.get("size") or "1536x1024"
        self.quality = self.image_cfg.get("quality") or "auto"
        self.background_n = _bounded_int(
            self.image_cfg.get("n", self.image_cfg.get("containerN", self.image_cfg.get("candidates"))),
            4,
            lo=1,
            hi=4,
        )
        self.asset_n = _bounded_int(
            self.image_cfg.get("assetN", self.image_cfg.get("sketchN")),
            4,
            lo=1,
            hi=4,
        )
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
        self.asset_size = self.image_cfg.get("assetSize", self.image_cfg.get("sketchSize")) or "1024x1024"
        self.asset_prefix = str(self.image_cfg.get("sketchStylePrefix") or "").strip()

        stage = self.visual.get("stage") if isinstance(self.visual.get("stage"), dict) else {}
        raw_bg = stage.get("background") if isinstance(stage.get("background"), dict) else {}
        if not raw_bg:
            raw_bg = self.image_cfg.get("background") if isinstance(self.image_cfg.get("background"), dict) else {}
        self.background_cfg = dict(raw_bg or {})
        self.background_cfg.setdefault("imageFile", "pictures/background.webp")
        if not str(self.background_cfg.get("prompt") or "").strip():
            self.background_cfg["prompt"] = self._fallback_background_prompt()
        self.background = {}

        self.out_dir = self.job_dir / "03_image"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.image_sem = asyncio.Semaphore(self.workers)

    @property
    def n_shots(self) -> int:
        return len(self.shots)

    def on_progress(self, text: str) -> None:
        self.runner._push_progress(self.job_id, "image", text)

    def patch_output(self, key: str, value: Any) -> None:
        patch = getattr(self.runner, "_push_outputs_patch", None)
        if callable(patch):
            patch(self.job_id, "image", key, value)

    def patch_snapshot(self) -> None:
        self.patch_output("background", self.background)
        self.patch_output("items", self.items)
        self.patch_output("asset_summary", self.asset_summary())

    def asset_summary(self) -> dict[str, Any]:
        total_assets = sum(len(it.get("assets") or []) for it in self.items)
        ready_assets = sum(
            1 for it in self.items for asset in (it.get("assets") or []) if asset.get("image_relpath")
        )
        failed_assets = sum(
            1 for it in self.items for asset in (it.get("assets") or []) if asset.get("error")
        )
        return {
            "background_total": 1,
            "background_ready": 1 if self.background.get("image_relpath") else 0,
            "shot_total": len(self.items),
            "foreground_total": total_assets,
            "foreground_ready": ready_assets,
            "foreground_failed": failed_assets,
        }

    def _fallback_background_prompt(self) -> str:
        for shot in self.shots:
            intent = str(shot.get("intent") or "").strip()
            if intent:
                return (
                    f"{intent}，16:9 全幅暖纸质感页面背景，淡雅远景层次贯穿整张画面，"
                    "主体元素留给前景素材，无文字，无数字。"
                )
        return "16:9 全幅暖纸质感页面背景，淡雅远景层次贯穿整张画面，细腻纸张纹理，主体元素留给前景素材，无文字，无数字。"

    def _write_episode_background(self) -> None:
        visual = self.episode.get("visual") if isinstance(self.episode.get("visual"), dict) else {}
        visual = dict(visual or {})
        stage = visual.get("stage") if isinstance(visual.get("stage"), dict) else {}
        stage = dict(stage or {})
        bg = stage.get("background") if isinstance(stage.get("background"), dict) else {}
        bg = {**dict(bg or {}), **self.background_cfg}
        bg["imageFile"] = "pictures/background.webp"
        stage["background"] = bg
        visual["stage"] = stage
        self.episode["visual"] = visual

        image_cfg = self.episode.get("image") if isinstance(self.episode.get("image"), dict) else {}
        image_cfg = dict(image_cfg or {})
        image_cfg["background"] = bg
        self.episode["image"] = image_cfg

        ep_path = self.job_dir / "02_rw" / "episode.json"
        if ep_path.parent.is_dir():
            ep_path.write_text(json.dumps(self.episode, ensure_ascii=False, indent=2), encoding="utf-8")

    async def run(self) -> dict[str, Any]:
        self._write_episode_background()
        self.items = self.build_placeholders()
        self.item_by_shot = {str(it.get("shot_id")): it for it in self.items}
        self.background = self.build_background_placeholder()
        self.patch_snapshot()

        total_assets = sum(len(it.get("assets") or []) for it in self.items)
        self.on_progress(
            f"image 开始：1 张背景 · {self.n_shots} 句画面 · {total_assets} 个前景素材 · "
            f"背景 n={self.background_n} 前景 n={self.asset_n} · 并发 {self.workers}"
        )
        tasks = [asyncio.create_task(self.generate_background())] + [
            asyncio.create_task(self.process_shot(i, shot))
            for i, shot in enumerate(self.shots, start=1)
        ]
        await asyncio.gather(*tasks)

        if not self.background.get("image_relpath"):
            raise RuntimeError("画面背景生成失败，请稍后重试；详细错误已写入服务日志。")

        self.on_progress(
            f"image 完成：背景 ok={self.ok} skipped={self.skipped} failed={self.failed} · "
            f"前景素材 ok={self.asset_ok} skipped={self.asset_skipped} failed={self.asset_failed}"
        )
        self.patch_snapshot()
        summary = self.asset_summary()
        return {
            "background": self.background,
            "items": self.items,
            "pictures_dir": str(self.out_dir),
            "pictures_count": summary["background_ready"] + summary["foreground_ready"],
            "ok": self.ok,
            "skipped": self.skipped,
            "failed": self.failed,
            "asset_ok": self.asset_ok,
            "asset_skipped": self.asset_skipped,
            "asset_failed": self.asset_failed,
            "asset_summary": summary,
        }

    def build_background_placeholder(self) -> dict[str, Any]:
        target = self.out_dir / "background.webp"
        variants = self._existing_variants("background", target, self.background_n)
        selected = self._selected_variant_relpath(target, variants)
        return {
            "id": "background",
            "prompt": str(self.background_cfg.get("prompt") or ""),
            "image_relpath": self._rel(target) if target.is_file() else None,
            "variants": [
                {**variant, "selected": variant.get("image_relpath") == selected}
                for variant in variants
            ],
            "selected_variant_relpath": selected,
            "status": "done" if target.is_file() else "queued",
        }

    def build_placeholders(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for index, shot in enumerate(self.shots, start=1):
            sid = _shot_id(shot, index)
            asset_items: list[dict[str, Any]] = []
            assets_def = shot.get("assets") if isinstance(shot.get("assets"), list) else []
            for n, asset_def in enumerate([a for a in assets_def if isinstance(a, dict)], start=1):
                aid = _asset_id(asset_def, n)
                target = self.out_dir / f"{sid}-{aid}.webp"
                variants = self._existing_variants(f"{sid}-{aid}", target, self.asset_n)
                selected = self._selected_variant_relpath(target, variants)
                asset_items.append({
                    "index": n,
                    "asset_id": aid,
                    "role": str(asset_def.get("role") or ""),
                    "prompt": str(asset_def.get("prompt") or "").strip(),
                    "pos": asset_def.get("pos") if isinstance(asset_def.get("pos"), dict) else {"x": 50, "y": 50},
                    "size": asset_def.get("size") if isinstance(asset_def.get("size"), (int, float)) else 32,
                    "motion": asset_def.get("motion") if isinstance(asset_def.get("motion"), dict) else {},
                    "image_relpath": self._rel(target) if target.is_file() else None,
                    "variants": [
                        {**variant, "selected": variant.get("image_relpath") == selected}
                        for variant in variants
                    ],
                    "selected_variant_relpath": selected,
                    "status": "done" if target.is_file() else "queued",
                })
            items.append({
                "shot_id": sid,
                "beat_index": int(shot.get("beatIndex") or index),
                "group": str(shot.get("group") or ""),
                "intent": str(shot.get("intent") or "").strip(),
                "layout": str(shot.get("layout") or ""),
                "transition": str(shot.get("transition") or ""),
                "background_relpath": "03_image/background.webp",
                "assets": asset_items,
                "status": "queued",
            })
        return items

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

    async def generate_background(self) -> None:
        target = self.out_dir / "background.webp"
        if target.is_file():
            variants = self._existing_variants("background", target, self.background_n)
            if self.background_n <= 1 or len(variants) >= self.background_n:
                self.skipped += 1
                selected = self._selected_variant_relpath(target, variants)
                self.background.update({
                    "image_relpath": self._rel(target),
                    "variants": [
                        {**variant, "selected": variant.get("image_relpath") == selected}
                        for variant in variants
                    ],
                    "selected_variant_relpath": selected,
                    "status": "done",
                })
                self.patch_snapshot()
                self.on_progress("背景图候选已存在，跳过")
                return
            self.on_progress(f"背景图候选不足，补生成 n={self.background_n}...")

        prompt = str(self.background_cfg.get("prompt") or "").strip()
        full_prompt = f"{prompt} {self.no_text_hint}".strip() if self.no_text_hint else prompt
        self.background["status"] = "running"
        self.patch_output("background", self.background)
        self.on_progress(f"背景图生成中 n={self.background_n}...")
        try:
            paths = await self.generate_with_retries(
                i=0,
                sid="background",
                label="背景图",
                scene_id="background",
                prompt=full_prompt,
                size=self.size,
                quality=self.quality,
                target=target,
                n=self.background_n,
            )
            self.ok += 1
            variants = [
                {"index": index, "image_relpath": self._rel(path)}
                for index, path in enumerate(paths, start=1)
            ]
            selected = self._selected_variant_relpath(target, variants)
            self.background.update({
                "image_relpath": self._rel(target),
                "variants": [
                    {**variant, "selected": variant.get("image_relpath") == selected}
                    for variant in variants
                ],
                "selected_variant_relpath": selected,
                "status": "done",
            })
            self.patch_snapshot()
        except _cancel.TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            friendly = _friendly_image_error(exc)
            logger.warning("[pipeline] image background failed: %s", friendly)
            self.on_progress(f"背景图失败：{friendly}")
            self.background.update({"status": "failed", "error": friendly})
            self.patch_snapshot()
            self.failed += 1

    async def process_shot(self, i: int, shot: dict[str, Any]) -> dict[str, Any]:
        sid = _shot_id(shot, i)
        item = self.item_by_shot.get(sid)
        if item is None:
            item = {"shot_id": sid, "beat_index": i, "intent": "", "assets": []}
            self.items.append(item)
            self.item_by_shot[sid] = item

        await self.generate_assets(i, sid, shot.get("assets") if isinstance(shot.get("assets"), list) else [])
        item["status"] = "done"
        self.patch_snapshot()
        return item

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
                    prefix = "" if sid == "background" else f"[{i}/{self.n_shots}] {sid} "
                    self.on_progress(
                        f"{prefix}{label}暂时失败，{suffix}重试 "
                        f"{attempt}/{self.retries}：{friendly}"
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
                raise RuntimeError(friendly) from exc
        raise RuntimeError("图片生成失败，详细错误已写入服务日志。")

    async def generate_assets(self, i: int, sid: str, assets_def: list[Any]) -> list[dict[str, Any]]:
        item = self.item_by_shot[sid]
        asset_items: list[dict[str, Any]] = list(item.get("assets") or [])
        clean_assets = [a for a in assets_def if isinstance(a, dict)]
        for n, asset_def in enumerate(clean_assets, start=1):
            aid = _asset_id(asset_def, n)
            prompt = str(asset_def.get("prompt") or "").strip()
            if not prompt:
                continue
            target = self.out_dir / f"{sid}-{aid}.webp"
            rel = self._rel(target)
            existing = next((asset for asset in asset_items if asset.get("index") == n), None)
            if target.is_file():
                variants = self._existing_variants(f"{sid}-{aid}", target, self.asset_n)
                selected = self._selected_variant_relpath(target, variants)
                patch = {
                    "index": n,
                    "asset_id": aid,
                    "prompt": prompt,
                    "image_relpath": rel,
                    "variants": [
                        {**variant, "selected": variant.get("image_relpath") == selected}
                        for variant in variants
                    ],
                    "selected_variant_relpath": selected,
                    "status": "done",
                }
                if existing is None:
                    asset_items.append(patch)
                else:
                    existing.update(patch)
                self.asset_skipped += 1
                item["assets"] = asset_items
                self.patch_snapshot()
                continue

            full_prompt = " ".join(p for p in (self.asset_prefix, prompt, self.no_text_hint) if p)
            if existing is not None:
                existing.update({"status": "running", "error": None})
                self.patch_output("items", self.items)
            self.on_progress(f"[{i}/{self.n_shots}] {sid} 前景素材 {n}/{len(clean_assets)} 生成中...")
            try:
                paths = await self.generate_with_retries(
                    i=i,
                    sid=sid,
                    label=f"前景素材 {n}",
                    scene_id=f"{sid}-{aid}",
                    prompt=full_prompt,
                    size=self.asset_size,
                    quality=self.quality,
                    target=target,
                    n=self.asset_n,
                )
                variants = [
                    {"index": index, "image_relpath": self._rel(path)}
                    for index, path in enumerate(paths, start=1)
                ]
                selected = self._selected_variant_relpath(target, variants)
                patch = {
                    "index": n,
                    "asset_id": aid,
                    "role": str(asset_def.get("role") or ""),
                    "prompt": prompt,
                    "pos": asset_def.get("pos") if isinstance(asset_def.get("pos"), dict) else {"x": 50, "y": 50},
                    "size": asset_def.get("size") if isinstance(asset_def.get("size"), (int, float)) else 32,
                    "motion": asset_def.get("motion") if isinstance(asset_def.get("motion"), dict) else {},
                    "image_relpath": rel,
                    "variants": [
                        {**variant, "selected": variant.get("image_relpath") == selected}
                        for variant in variants
                    ],
                    "selected_variant_relpath": selected,
                    "status": "done",
                }
                if existing is not None:
                    existing.update(patch)
                else:
                    asset_items.append(patch)
                self.asset_ok += 1
                self.item_by_shot[sid]["assets"] = asset_items
                self.patch_snapshot()
            except _cancel.TaskCancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                friendly = _friendly_image_error(exc)
                logger.warning("[pipeline] asset %s-%s failed: %s", sid, aid, friendly)
                self.on_progress(f"[{i}/{self.n_shots}] {sid} 前景素材 {n} 失败：{friendly}")
                failed_item = {
                    "index": n,
                    "asset_id": aid,
                    "prompt": prompt,
                    "image_relpath": None,
                    "error": friendly,
                    "status": "failed",
                }
                if existing is not None:
                    existing.update(failed_item)
                else:
                    asset_items.append(failed_item)
                self.asset_failed += 1
                self.item_by_shot[sid]["assets"] = asset_items
                self.patch_snapshot()
        return asset_items
