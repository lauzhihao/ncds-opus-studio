"""Preview/image/TTS regeneration operations for PipelineRunner."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ncds_opus_core.templates import template_dir as _template_dir

from ncds_opus_factory.server import pipeline_media_helpers as media_helpers


class PipelineRegenOperationsMixin:
    """Image and TTS regeneration operations triggered from preview drawers."""

    def _emit_node_status(self, job_id: str, node_name: str, node: Any) -> None:
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(node)})

    def _update_image_items(
        self,
        job_id: str,
        mutate_items: Callable[[list[dict[str, Any]]], None],
        *,
        touch_job: bool = True,
    ) -> None:
        state = self._load(job_id)
        img = state.nodes.get("image")
        if img is None or not img.outputs:
            return
        items = list(img.outputs.get("items") or [])
        mutate_items(items)
        img.outputs["items"] = items
        img.finished_at = time.time()
        if touch_job:
            state.updated_at = time.time()
        self._save(state)
        self._emit_node_status(job_id, "image", img)

    def _update_image_background(
        self,
        job_id: str,
        mutate_background: Callable[[dict[str, Any]], None],
        *,
        touch_job: bool = True,
    ) -> None:
        state = self._load(job_id)
        img = state.nodes.get("image")
        if img is None or not img.outputs:
            return
        background = dict(img.outputs.get("background") or {})
        mutate_background(background)
        img.outputs["background"] = background
        img.finished_at = time.time()
        if touch_job:
            state.updated_at = time.time()
        self._save(state)
        self._emit_node_status(job_id, "image", img)

    @staticmethod
    def _put_scene_image(
        items: list[dict[str, Any]],
        scene_id: str,
        rel: str,
        variants: list[dict[str, Any]] | None = None,
        selected_variant_relpath: str | None = None,
    ) -> None:
        for it in items:
            if it.get("scene_id") == scene_id:
                it["image_relpath"] = rel
                if variants is not None:
                    it["variants"] = variants
                if selected_variant_relpath is not None:
                    it["selected_variant_relpath"] = selected_variant_relpath
                break

    @staticmethod
    def _put_background_image(
        background: dict[str, Any],
        rel: str,
        variants: list[dict[str, Any]] | None = None,
        selected_variant_relpath: str | None = None,
    ) -> None:
        background["image_relpath"] = rel
        background["status"] = "done"
        background.pop("error", None)
        if variants is not None:
            background["variants"] = variants
        if selected_variant_relpath is not None:
            background["selected_variant_relpath"] = selected_variant_relpath

    @staticmethod
    def _is_background_scene(scene_id: str) -> bool:
        return scene_id in {"background", "__background"}

    def _background_prompt(self, ep: dict[str, Any]) -> str:
        visual = ep.get("visual") if isinstance(ep.get("visual"), dict) else {}
        stage = visual.get("stage") if isinstance(visual.get("stage"), dict) else {}
        bg2 = stage.get("background") if isinstance(stage.get("background"), dict) else {}
        prompt2 = str((bg2 or {}).get("prompt") or "").strip()
        if prompt2:
            return prompt2
        image_cfg = ep.get("image") if isinstance(ep.get("image"), dict) else {}
        bg = image_cfg.get("background") if isinstance(image_cfg.get("background"), dict) else {}
        prompt = str((bg or {}).get("prompt") or "").strip()
        if prompt:
            return prompt
        for shot in visual.get("shots") or []:
            if isinstance(shot, dict) and str(shot.get("intent") or "").strip():
                return (
                    f"{shot['intent']}，16:9 全幅暖纸质感页面背景，淡雅远景层次贯穿整张画面，"
                    "主体元素留给前景素材，无文字，无数字。"
                )
        return "16:9 全幅暖纸质感页面背景，淡雅远景层次贯穿整张画面，细腻纸张纹理，主体元素留给前景素材，无文字，无数字。"

    def _write_background_image_file(self, job_id: str, ep: dict[str, Any]) -> None:
        visual = ep.get("visual") if isinstance(ep.get("visual"), dict) else {}
        visual = dict(visual or {})
        stage = visual.get("stage") if isinstance(visual.get("stage"), dict) else {}
        stage = dict(stage or {})
        vbg = stage.get("background") if isinstance(stage.get("background"), dict) else {}
        vbg = dict(vbg or {})
        vbg["imageFile"] = "pictures/background.webp"
        if not str(vbg.get("prompt") or "").strip():
            vbg["prompt"] = self._background_prompt(ep)
        stage["background"] = vbg
        visual["stage"] = stage
        ep["visual"] = visual

        image_cfg = ep.get("image") if isinstance(ep.get("image"), dict) else {}
        image_cfg = dict(image_cfg or {})
        bg = image_cfg.get("background") if isinstance(image_cfg.get("background"), dict) else {}
        bg = {**vbg, **dict(bg or {})}
        bg["imageFile"] = "pictures/background.webp"
        if not str(bg.get("prompt") or "").strip():
            bg["prompt"] = self._background_prompt(ep)
        image_cfg["background"] = bg
        ep["image"] = image_cfg
        ep_path = self.video_jobs_dir / job_id / "02_rw" / "episode.json"
        ep_path.write_text(json.dumps(ep, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _put_sketch_image(
        items: list[dict[str, Any]],
        *,
        scene_id: str,
        n: int,
        rel: str,
        prompt: str,
        variants: list[dict[str, Any]] | None = None,
        selected_variant_relpath: str | None = None,
    ) -> None:
        for it in items:
            if it.get("shot_id") != scene_id:
                continue
            sk_items = list(it.get("assets") or [])
            hit = next((s for s in sk_items if s.get("index") == n), None)
            if hit is not None:
                hit["image_relpath"] = rel
                hit["status"] = "done"
                hit.pop("error", None)
                if variants is not None:
                    hit["variants"] = variants
                if selected_variant_relpath is not None:
                    hit["selected_variant_relpath"] = selected_variant_relpath
            else:
                item = {"index": n, "prompt": prompt, "image_relpath": rel, "status": "done"}
                if variants is not None:
                    item["variants"] = variants
                if selected_variant_relpath is not None:
                    item["selected_variant_relpath"] = selected_variant_relpath
                sk_items.append(item)
            it["assets"] = sk_items
            break

    async def _mock_regen_delay(self) -> None:
        """mock 下 regen 类操作的统一模拟耗时。"""
        from ncds_opus_factory.server import mock as mock_mod
        await asyncio.sleep(mock_mod.mock_delay_seconds("regen_model"))

    async def regen_scene_image_from_preview(self, job_id: str, scene_id: str) -> str:
        """preview 抽屉里点「生成图片」时调用，不要求 image 节点 done。"""
        if self._is_background_scene(scene_id):
            return await self.regen_background_image(job_id)
        if self._load(job_id).mock:
            return await self._mock_regen_image(job_id, scene_id)
        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found; run rw first")
        scenes = (ep.get("scenes") or {})
        if scene_id not in scenes:
            raise ValueError(f"unknown scene: {scene_id}")
        sc = scenes[scene_id] or {}
        prompt = str(sc.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"scene {scene_id} has empty prompt; can't generate")

        image_cfg = ep.get("image") or {}
        size = image_cfg.get("size") or "1536x1024"
        quality = image_cfg.get("quality") or "auto"
        count = self._bounded_image_count(image_cfg.get("n", image_cfg.get("containerN", image_cfg.get("candidates"))))
        no_text_hint = image_cfg.get("noTextHint") or ""
        full_prompt = f"{prompt} {no_text_hint}".strip() if no_text_hint else prompt

        rel = f"03_image/{scene_id}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        self._unlink_scene_candidates(target, count)
        paths = await asyncio.to_thread(
            media_helpers._generate_scene_image,
            scene_id=scene_id,
            prompt=full_prompt,
            size=size,
            quality=quality,
            target=target,
            job_id=job_id,
            n=count,
        )
        variants = [
            {"index": index, "image_relpath": self._rel_to_job(job_id, path), "selected": index == 1}
            for index, path in enumerate(paths, start=1)
        ]

        self._update_image_items(
            job_id,
            lambda items: self._put_scene_image(
                items,
                scene_id,
                rel,
                variants,
                variants[0]["image_relpath"] if variants else rel,
            ),
        )
        return rel

    async def regen_background_image(self, job_id: str) -> str:
        """重生全片统一背景图。"""
        if self._load(job_id).mock:
            return await self._mock_regen_image(job_id, "background")
        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found; run rw first")
        image_cfg = ep.get("image") if isinstance(ep.get("image"), dict) else {}
        size = image_cfg.get("size") or "1536x1024"
        quality = image_cfg.get("quality") or "auto"
        count = self._bounded_image_count(image_cfg.get("n", image_cfg.get("containerN", image_cfg.get("candidates"))))
        no_text_hint = image_cfg.get("noTextHint") or ""
        prompt = self._background_prompt(ep)
        full_prompt = f"{prompt} {no_text_hint}".strip() if no_text_hint else prompt

        rel = "03_image/background.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        self._unlink_scene_candidates(target, count)

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "image", f"[regen background] {text}")

        on_progress("背景图重生中…")
        paths = await asyncio.to_thread(
            media_helpers._generate_scene_image,
            scene_id="background",
            prompt=full_prompt,
            size=size,
            quality=quality,
            target=target,
            job_id=job_id,
            n=count,
        )
        variants = [
            {"index": index, "image_relpath": self._rel_to_job(job_id, path), "selected": index == 1}
            for index, path in enumerate(paths, start=1)
        ]
        self._write_background_image_file(job_id, ep)
        self._update_image_background(
            job_id,
            lambda bg: self._put_background_image(
                bg,
                rel,
                variants,
                variants[0]["image_relpath"] if variants else rel,
            ),
        )
        return rel

    @staticmethod
    def _bounded_image_count(value: Any) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            n = 4
        return max(1, min(4, n))

    def _rel_to_job(self, job_id: str, path: Path) -> str:
        return path.relative_to(self.video_jobs_dir / job_id).as_posix()

    @staticmethod
    def _unlink_scene_candidates(target: Path, count: int) -> None:
        paths = [target]
        if count > 1:
            paths.extend(target.with_name(f"{target.stem}-v{i}{target.suffix}") for i in range(1, count + 1))
        for path in paths:
            if path.is_file():
                path.unlink()

    def select_image_variant(self, job_id: str, scene_id: str, image_relpath: str) -> str:
        """把候选图复制为标准主图，保持下游按 03_image/{scene}.webp 取图。"""
        state = self._load(job_id)
        img = state.nodes.get("image")
        if img is None or not img.outputs:
            raise ValueError("image node has no outputs")
        if self._is_background_scene(scene_id):
            return self._select_background_variant(job_id, image_relpath)
        items = list(img.outputs.get("items") or [])
        item = next((it for it in items if it.get("scene_id") == scene_id), None)
        if item is None:
            raise ValueError(f"unknown scene: {scene_id}")

        rel = image_relpath.strip().lstrip("/")
        candidates = [str(v.get("image_relpath") or "") for v in item.get("variants") or []]
        if rel not in candidates and rel != item.get("image_relpath"):
            raise ValueError(f"image is not a candidate for scene {scene_id}: {rel}")

        job_dir = self.video_jobs_dir / job_id
        src = (job_dir / rel).resolve()
        root = job_dir.resolve()
        if root not in src.parents or not src.is_file():
            raise ValueError(f"candidate image not found: {rel}")

        main_rel = f"03_image/{scene_id}.webp"
        main = job_dir / main_rel
        main.parent.mkdir(parents=True, exist_ok=True)
        if src != main.resolve():
            shutil.copyfile(src, main)

        item["image_relpath"] = main_rel
        item["selected_variant_relpath"] = rel
        variants = list(item.get("variants") or [])
        for v in variants:
            v["selected"] = v.get("image_relpath") == rel
        item["variants"] = variants

        img.outputs["items"] = items
        img.finished_at = time.time()
        state.updated_at = time.time()
        self._save(state)
        self._emit_node_status(job_id, "image", img)
        return main_rel

    def _select_background_variant(self, job_id: str, image_relpath: str) -> str:
        state = self._load(job_id)
        img = state.nodes.get("image")
        if img is None or not img.outputs:
            raise ValueError("image node has no outputs")
        background = dict(img.outputs.get("background") or {})
        rel = image_relpath.strip().lstrip("/")
        candidates = [str(v.get("image_relpath") or "") for v in background.get("variants") or []]
        if rel not in candidates and rel != background.get("image_relpath"):
            raise ValueError(f"image is not a candidate for background: {rel}")

        job_dir = self.video_jobs_dir / job_id
        src = (job_dir / rel).resolve()
        root = job_dir.resolve()
        if root not in src.parents or not src.is_file():
            raise ValueError(f"candidate image not found: {rel}")

        main_rel = "03_image/background.webp"
        main = job_dir / main_rel
        main.parent.mkdir(parents=True, exist_ok=True)
        if src != main.resolve():
            shutil.copyfile(src, main)

        background["image_relpath"] = main_rel
        background["selected_variant_relpath"] = rel
        variants = list(background.get("variants") or [])
        for v in variants:
            v["selected"] = v.get("image_relpath") == rel
        background["variants"] = variants
        background["status"] = "done"

        img.outputs["background"] = background
        img.finished_at = time.time()
        state.updated_at = time.time()
        self._save(state)
        self._emit_node_status(job_id, "image", img)
        return main_rel

    async def regen_image_scene(self, job_id: str, scene_id: str) -> None:
        """重生 image 节点里指定 scene 的图片。不动其他场景，不动下游节点状态。"""
        state = self._load(job_id)
        n = state.nodes.get("image")
        if n is None:
            raise KeyError("image node not found")
        if n.status != "done":
            raise ValueError("image node not done; run image first")
        if self._is_background_scene(scene_id):
            await self.regen_background_image(job_id)
            return
        items = list((n.outputs or {}).get("items") or [])
        if not any(it.get("scene_id") == scene_id for it in items):
            raise ValueError(f"unknown scene: {scene_id}")
        await self.regen_scene_image_from_preview(job_id, scene_id)

    async def regen_image_sketch(self, job_id: str, scene_id: str, n: int) -> str:
        """重生 image 节点里指定 shot 的第 n 个前景素材（1-based）。"""
        state = self._load(job_id)
        if state.mock:
            return await self._mock_regen_sketch(job_id, scene_id, n)
        img = state.nodes.get("image")
        if img is None:
            raise KeyError("image node not found")
        if img.status != "done":
            raise ValueError("image node not done; run image first")

        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found")
        visual = ep.get("visual") if isinstance(ep.get("visual"), dict) else {}
        shots = [s for s in (visual.get("shots") or []) if isinstance(s, dict)]
        shot = next((s for s in shots if str(s.get("shotId") or s.get("id") or "") == scene_id), None)
        if not isinstance(shot, dict):
            raise ValueError(f"unknown shot: {scene_id}")
        assets = [a for a in (shot.get("assets") or []) if isinstance(a, dict)]
        if n < 1 or n > len(assets):
            raise ValueError(f"asset index out of range: {n}")
        asset = assets[n - 1] or {}
        aid = str(asset.get("id") or f"a{n}").strip() or f"a{n}"
        sp = str(asset.get("prompt") or "").strip()
        if not sp:
            raise ValueError(f"asset {scene_id}-{aid} has empty prompt")

        image_cfg = ep.get("image") or {}
        quality = image_cfg.get("quality") or "auto"
        no_text_hint = image_cfg.get("noTextHint") or ""
        sketch_size = image_cfg.get("assetSize", image_cfg.get("sketchSize")) or "1024x1024"
        sketch_prefix = str(image_cfg.get("sketchStylePrefix") or "").strip()
        count = self._bounded_image_count(image_cfg.get("assetN", image_cfg.get("sketchN")))
        full = " ".join(p for p in (sketch_prefix, sp, no_text_hint) if p)

        rel = f"03_image/{scene_id}-{aid}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        self._unlink_scene_candidates(target, count)

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "image", f"[regen {scene_id}-{aid}] {text}")

        on_progress("前景素材重生中…")
        paths = await asyncio.to_thread(
            media_helpers._generate_scene_image,
            scene_id=f"{scene_id}-{aid}", prompt=full, size=sketch_size,
            quality=quality, target=target, job_id=job_id, n=count,
        )
        variants = [
            {"index": index, "image_relpath": self._rel_to_job(job_id, path), "selected": index == 1}
            for index, path in enumerate(paths, start=1)
        ]

        self._update_image_items(
            job_id,
            lambda items: self._put_sketch_image(
                items,
                scene_id=scene_id,
                n=n,
                rel=rel,
                prompt=sp,
                variants=variants,
                selected_variant_relpath=variants[0]["image_relpath"] if variants else rel,
            ),
        )
        return rel

    async def regen_tts_scene(self, job_id: str, scene_id: str) -> None:
        """final_preview：重生指定 scene 的整段音频。"""
        state = self._load(job_id)
        if state.mock:
            await self._mock_regen_tts(job_id, scene_id)
            return
        if state.pipeline_id != "final_preview":
            raise ValueError("scene 级重生仅 final_preview pipeline 支持")
        n = state.nodes.get("tts")
        if n is None:
            raise KeyError("tts node not found")
        if n.status != "done":
            raise ValueError("tts node not done; run tts first")

        job_dir = self.video_jobs_dir / job_id
        ep_path = job_dir / "02_rw" / "episode.json"
        if not ep_path.is_file():
            raise ValueError("episode.json not found")
        ep = json.loads(ep_path.read_text(encoding="utf-8"))
        if not any((b.get("scene") == scene_id) for b in (ep.get("beats") or [])):
            raise ValueError(f"unknown scene: {scene_id}")

        tts_gen = _template_dir("final_preview") / ".final-preview-assets" / "tts_gen.py"
        audio_dir = job_dir / "04_tts"

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "tts", f"[regen {scene_id}] {text}")

        await asyncio.to_thread(
            media_helpers._run_tts_gen_015,
            script=tts_gen,
            episode_path=ep_path,
            audio_dir=audio_dir,
            on_line=on_progress,
            only=scene_id,
            force=True,
        )

        ep2 = json.loads(ep_path.read_text(encoding="utf-8"))
        state = self._load(job_id)
        n = state.nodes.get("tts")
        if n is None:
            return
        n.outputs["items"] = media_helpers._rebuild_tts_items_015(ep2)
        n.finished_at = time.time()
        state.updated_at = time.time()
        self._save(state)
        self._emit_node_status(job_id, "tts", n)

    async def _mock_regen_image(self, job_id: str, scene_id: str) -> str:
        """mock：从 final_preview 素材拷背景图或旧 scene 图到 03_image/{scene_id}.webp。"""
        from ncds_opus_factory.server import mock as mock_mod
        await asyncio.sleep(mock_mod.mock_delay_seconds("regen_image"))
        rel = f"03_image/{scene_id}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        src_pic = mock_mod._source_dir() / "pictures" / f"{scene_id}.webp"
        if self._is_background_scene(scene_id) and not src_pic.is_file():
            for cand in sorted((mock_mod._source_dir() / "pictures").glob("*.webp")):
                src_pic = cand
                break
        if src_pic.is_file():
            await asyncio.to_thread(shutil.copyfile, src_pic, target)
        if target.is_file():
            if self._is_background_scene(scene_id):
                self._update_image_background(
                    job_id,
                    lambda bg: self._put_background_image(bg, rel, [], rel),
                    touch_job=False,
                )
            else:
                self._update_image_items(
                    job_id,
                    lambda items: self._put_scene_image(items, scene_id, rel),
                    touch_job=False,
                )
        return rel

    async def _mock_regen_sketch(self, job_id: str, scene_id: str, n: int) -> str:
        """mock：源素材一般无前景素材文件，有则拷、没有用背景图占位。"""
        from ncds_opus_factory.server import mock as mock_mod
        await asyncio.sleep(mock_mod.mock_delay_seconds("regen_sketch"))
        ep = self.get_episode(job_id) or {}
        shot = next(
            (
                s for s in ((ep.get("visual") or {}).get("shots") or [])
                if isinstance(s, dict) and str(s.get("shotId") or s.get("id") or "") == scene_id
            ),
            {},
        )
        assets = [a for a in (shot.get("assets") or []) if isinstance(a, dict)] if isinstance(shot, dict) else []
        asset = assets[n - 1] if 1 <= n <= len(assets) else {}
        aid = str((asset or {}).get("id") or f"a{n}").strip() or f"a{n}"
        rel = f"03_image/{scene_id}-{aid}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        pics = mock_mod._source_dir() / "pictures"
        cand = pics / f"{scene_id}-{aid}.webp"
        if not cand.is_file():
            cand = pics / "background.webp"
        if not cand.is_file():
            cand = next(iter(sorted(pics.glob("*.webp"))), cand)
        if cand.is_file():
            await asyncio.to_thread(shutil.copyfile, cand, target)
        if target.is_file():
            self._update_image_items(
                job_id,
                lambda items: self._put_sketch_image(items, scene_id=scene_id, n=n, rel=rel, prompt=""),
                touch_job=False,
            )
        return rel

    async def _mock_regen_tts(self, job_id: str, scene_id: str) -> None:
        """mock：scene 音频已由 tts mock 落盘，静态复用；sleep 后重建 items 收尾。"""
        from ncds_opus_factory.server import mock as mock_mod
        await asyncio.sleep(mock_mod.mock_delay_seconds("regen_tts"))
        state = self._load(job_id)
        n = state.nodes.get("tts")
        if n is None:
            return
        ep_path = self.video_jobs_dir / job_id / "02_rw" / "episode.json"
        if ep_path.is_file():
            ep = json.loads(ep_path.read_text(encoding="utf-8"))
            n.outputs["items"] = media_helpers._rebuild_tts_items_015(ep)
        n.finished_at = time.time()
        self._save(state)
        self._emit_node_status(job_id, "tts", n)
