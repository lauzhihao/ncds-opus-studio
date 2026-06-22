"""Preview/image/TTS regeneration operations for PipelineRunner."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import asdict

from ncds_opus_core.templates import template_dir as _template_dir
from ncds_opus_factory.server import pipeline_media_helpers as media_helpers


class PipelineRegenOperationsMixin:
    """Image and TTS regeneration operations triggered from preview drawers."""

    async def _mock_regen_delay(self) -> None:
        """mock 下 regen 类操作的统一模拟耗时。"""
        from ncds_opus_factory.server import mock as mock_mod
        await asyncio.sleep(mock_mod.MOCK_NODE_DELAY_SEC)

    async def regen_scene_image_from_preview(self, job_id: str, scene_id: str) -> str:
        """preview 抽屉里点「生成图片」时调用，不要求 image 节点 done。"""
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
        no_text_hint = image_cfg.get("noTextHint") or ""
        full_prompt = f"{prompt} {no_text_hint}".strip() if no_text_hint else prompt

        rel = f"03_image/{scene_id}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            target.unlink()
        await asyncio.to_thread(
            media_helpers._generate_scene_image,
            scene_id=scene_id,
            prompt=full_prompt,
            size=size,
            quality=quality,
            target=target,
            job_id=job_id,
        )

        state = self._load(job_id)
        image_node = state.nodes.get("image")
        if image_node and image_node.outputs:
            items = list(image_node.outputs.get("items") or [])
            for it in items:
                if it.get("scene_id") == scene_id:
                    it["image_relpath"] = rel
                    break
            image_node.outputs["items"] = items
            image_node.finished_at = time.time()
            state.updated_at = time.time()
            self._save(state)
            self._emit(
                job_id,
                {"type": "node_status", "job_id": job_id, "node": "image", "state": asdict(image_node)},
            )
        return rel

    async def regen_image_scene(self, job_id: str, scene_id: str) -> None:
        """重生 image 节点里指定 scene 的图片。不动其他场景，不动下游节点状态。"""
        state = self._load(job_id)
        n = state.nodes.get("image")
        if n is None:
            raise KeyError("image node not found")
        if n.status != "done":
            raise ValueError("image node not done; run image first")
        items = list((n.outputs or {}).get("items") or [])
        if not any(it.get("scene_id") == scene_id for it in items):
            raise ValueError(f"unknown scene: {scene_id}")
        await self.regen_scene_image_from_preview(job_id, scene_id)

    async def regen_image_sketch(self, job_id: str, scene_id: str, n: int) -> str:
        """重生 image 节点里指定 scene 的第 n 幅简笔画（1-based）。"""
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
        sc = (ep.get("scenes") or {}).get(scene_id)
        if not isinstance(sc, dict):
            raise ValueError(f"unknown scene: {scene_id}")
        sketches = sc.get("sketches") or []
        if n < 1 or n > len(sketches):
            raise ValueError(f"sketch index out of range: {n}")
        sp = str((sketches[n - 1] or {}).get("prompt") or "").strip()
        if not sp:
            raise ValueError(f"sketch {scene_id}-sk{n} has empty prompt")

        image_cfg = ep.get("image") or {}
        quality = image_cfg.get("quality") or "auto"
        no_text_hint = image_cfg.get("noTextHint") or ""
        sketch_size = image_cfg.get("sketchSize") or "1024x1024"
        sketch_prefix = str(image_cfg.get("sketchStylePrefix") or "").strip()
        full = " ".join(p for p in (sketch_prefix, sp, no_text_hint) if p)

        rel = f"03_image/{scene_id}-sk{n}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            target.unlink()

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "image", f"[regen {scene_id}-sk{n}] {text}")

        on_progress("简笔画重生中…")
        await asyncio.to_thread(
            media_helpers._generate_scene_image,
            scene_id=f"{scene_id}-sk{n}", prompt=full, size=sketch_size,
            quality=quality, target=target, job_id=job_id,
        )

        state = self._load(job_id)
        img = state.nodes.get("image")
        if img and img.outputs:
            items = list(img.outputs.get("items") or [])
            for it in items:
                if it.get("scene_id") != scene_id:
                    continue
                sk_items = list(it.get("sketches") or [])
                hit = next((s for s in sk_items if s.get("index") == n), None)
                if hit is not None:
                    hit["image_relpath"] = rel
                    hit.pop("error", None)
                else:
                    sk_items.append({"index": n, "prompt": sp, "image_relpath": rel})
                it["sketches"] = sk_items
                break
            img.outputs["items"] = items
            img.finished_at = time.time()
            state.updated_at = time.time()
            self._save(state)
            self._emit(
                job_id,
                {"type": "node_status", "job_id": job_id, "node": "image", "state": asdict(img)},
            )
        return rel

    async def regen_tts_scene(self, job_id: str, scene_id: str) -> None:
        """015：重生指定 scene 的整段音频。"""
        state = self._load(job_id)
        if state.mock:
            await self._mock_regen_tts(job_id, scene_id)
            return
        if state.pipeline_id != "paper_card_talk_015":
            raise ValueError("scene 级重生仅 015 pipeline 支持")
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

        tts_gen = _template_dir("paper_card_talk_015") / ".015-draft-assets" / "tts_gen.py"
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
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "tts", "state": asdict(n)})

    async def _mock_regen_image(self, job_id: str, scene_id: str) -> str:
        """mock：从 015 素材拷该 scene 容器图到 03_image/{scene_id}.webp。"""
        from ncds_opus_factory.server import mock as mock_mod
        await asyncio.sleep(mock_mod.MOCK_NODE_DELAY_SEC)
        rel = f"03_image/{scene_id}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        src_pic = mock_mod._source_dir() / "pictures" / f"{scene_id}.webp"
        if src_pic.is_file():
            await asyncio.to_thread(shutil.copyfile, src_pic, target)
        state = self._load(job_id)
        img = state.nodes.get("image")
        if img and img.outputs and target.is_file():
            items = list(img.outputs.get("items") or [])
            for it in items:
                if it.get("scene_id") == scene_id:
                    it["image_relpath"] = rel
                    break
            img.outputs["items"] = items
            img.finished_at = time.time()
            self._save(state)
            self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "image", "state": asdict(img)})
        return rel

    async def _mock_regen_sketch(self, job_id: str, scene_id: str, n: int) -> str:
        """mock：源素材一般无简笔画文件，有则拷、没有用容器图占位。"""
        from ncds_opus_factory.server import mock as mock_mod
        await asyncio.sleep(mock_mod.MOCK_NODE_DELAY_SEC)
        rel = f"03_image/{scene_id}-sk{n}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        pics = mock_mod._source_dir() / "pictures"
        cand = pics / f"{scene_id}-sk{n}.webp"
        if not cand.is_file():
            cand = pics / f"{scene_id}.webp"
        if cand.is_file():
            await asyncio.to_thread(shutil.copyfile, cand, target)
        state = self._load(job_id)
        img = state.nodes.get("image")
        if img and img.outputs and target.is_file():
            items = list(img.outputs.get("items") or [])
            for it in items:
                if it.get("scene_id") != scene_id:
                    continue
                sk_items = list(it.get("sketches") or [])
                hit = next((s for s in sk_items if s.get("index") == n), None)
                if hit is not None:
                    hit["image_relpath"] = rel
                    hit.pop("error", None)
                else:
                    sk_items.append({"index": n, "prompt": "", "image_relpath": rel})
                it["sketches"] = sk_items
                break
            img.outputs["items"] = items
            img.finished_at = time.time()
            self._save(state)
            self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "image", "state": asdict(img)})
        return rel

    async def _mock_regen_tts(self, job_id: str, scene_id: str) -> None:
        """mock：scene 音频已由 tts mock 落盘，静态复用；sleep 后重建 items 收尾。"""
        await self._mock_regen_delay()
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
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "tts", "state": asdict(n)})
