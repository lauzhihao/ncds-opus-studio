"""Pipeline runner facade.

`PipelineRunner` 保持 web `/jobs` 的 public 契约和每个节点的路由入口；
状态持久化、事件、调度取消、agent 后台任务、RW 操作和 regen 操作都拆到 mixin/task 模块。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from ncds_opus_core.templates import template_dir as _template_dir

from ncds_opus_factory.server import pipeline_media_helpers as media_helpers
from ncds_opus_factory.server import pipeline_rw_helpers as rw_helpers
from ncds_opus_factory.server.pipeline_agent_tasks import PipelineAgentTasksMixin
from ncds_opus_factory.server.pipeline_asr_tasks import PipelineAsrCollectRun
from ncds_opus_factory.server.pipeline_engine_bridge import PipelineEngineBridgeMixin
from ncds_opus_factory.server.pipeline_events import PipelineEventsMixin
from ncds_opus_factory.server.pipeline_image_tasks import PipelineImageRun
from ncds_opus_factory.server.pipeline_lines_tasks import PipelineLinesRun
from ncds_opus_factory.server.pipeline_models import EventBus, JobState, NodeState
from ncds_opus_factory.server.pipeline_regen_operations import PipelineRegenOperationsMixin
from ncds_opus_factory.server.pipeline_render_tasks import PipelineRenderRun
from ncds_opus_factory.server.pipeline_rw_operations import PipelineRwOperationsMixin
from ncds_opus_factory.server.pipeline_rw_tasks import PipelineRwRun
from ncds_opus_factory.server.pipeline_scheduler import PipelineSchedulerMixin
from ncds_opus_factory.server.pipeline_state_store import PipelineStateStoreMixin
from ncds_opus_factory.server.pipeline_storyboard_tasks import PipelineStoryboardRun
from ncds_opus_factory.server.pipeline_tts_tasks import PipelineTtsRun

DEFAULT_OPUS_MODEL_ID = rw_helpers.DEFAULT_OPUS_MODEL_ID

# Backward-compatible exports used by mock.py/tests.
__all__ = [
    "DEFAULT_OPUS_MODEL_ID",
    "EventBus",
    "JobState",
    "NodeState",
    "PipelineRunner",
]


class PipelineRunner(
    PipelineEventsMixin,
    PipelineStateStoreMixin,
    PipelineSchedulerMixin,
    PipelineEngineBridgeMixin,
    PipelineAgentTasksMixin,
    PipelineRwOperationsMixin,
    PipelineRegenOperationsMixin,
):
    """每个进程一个 PipelineRunner 单例（state.py 里建）。"""

    def __init__(self, video_jobs_dir: Path) -> None:
        self.video_jobs_dir = video_jobs_dir
        self.video_jobs_dir.mkdir(parents=True, exist_ok=True)
        self.bus = EventBus()
        self._running_nodes: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._enrich_tasks: dict[str, asyncio.Task[Any]] = {}
        self._refresh_tasks: dict[str, asyncio.Task[Any]] = {}
        self._guiguzi_tasks: dict[str, asyncio.Task[Any]] = {}
        self._engine: Any = None
        self._task_runner: Any = None
        self._event_seq: dict[str, int] = {}

        _all = {"lines", "storyboard", "tts", "image", "render"}
        _env = os.getenv("NOF_ENGINE_NODES")
        if _env is None:
            self._engine_nodes: set[str] = set(_all)
        elif _env.strip().lower() in {"", "none", "off", "legacy"}:
            self._engine_nodes = set()
        else:
            self._engine_nodes = {n.strip() for n in _env.split(",") if n.strip()}

    async def _execute_tts(self, job_id: str) -> dict[str, Any]:
        """Legacy fallback: synthesize scene-level final_preview audio."""
        job_dir = self.video_jobs_dir / job_id
        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found; run rw first (or manually seed it)")

        tts_gen = _template_dir("final_preview") / ".final-preview-assets" / "tts_gen.py"
        return await PipelineTtsRun(
            runner=self,
            job_id=job_id,
            job_dir=job_dir,
            episode=ep,
            tts_gen_script=tts_gen,
            run_tts_gen=media_helpers._run_tts_gen_015,
            rebuild_tts_items=media_helpers._rebuild_tts_items_015,
        ).run()

    async def _execute_image(self, job_id: str) -> dict[str, Any]:
        """Legacy fallback: generate scene images from episode scenes."""
        job_dir = self.video_jobs_dir / job_id
        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found; run rw first (or manually seed it)")
        return await PipelineImageRun(
            runner=self,
            job_id=job_id,
            job_dir=job_dir,
            episode=ep,
            generate_scene_image=media_helpers._generate_scene_image,
        ).run()

    async def _execute_asr_collect(self, job_id: str) -> dict[str, Any]:
        """ASR/Shenkuo fast collect remains legacy because it streams outputs mid-run."""
        state = self._load(job_id)
        job_dir = self.video_jobs_dir / job_id
        return await PipelineAsrCollectRun(
            runner=self,
            job_id=job_id,
            job_dir=job_dir,
            inputs=state.inputs,
            flag_path=self._cancel_flag(job_id, "asr"),
            run_in_thread_cancellable=self._run_in_thread_cancellable,
        ).run()

    async def _execute_rw(self, job_id: str) -> dict[str, Any]:
        """Legacy RW path: multi-model execution with per-model progress patches."""
        state = self._load(job_id)
        asr_node = state.nodes.get("asr")
        if asr_node is None or asr_node.status != "done":
            raise ValueError("asr node not done; run asr first")
        asr_out = asr_node.outputs or {}
        asr_items = list(asr_out.get("collected") or asr_out.get("items") or [])
        if not asr_items:
            raise ValueError("asr outputs empty; nothing to rewrite")

        job_dir = self.video_jobs_dir / job_id
        rw_root = job_dir / "02_rw"
        rw_root.mkdir(parents=True, exist_ok=True)

        source_text = rw_helpers._rw_source_text(asr_items, job_dir)
        if not source_text:
            raise RuntimeError("asr 采集文案全部为空，无法 rw")

        domain_guidance = rw_helpers._rw_domain_guidance(state.inputs.get("domain"))
        system_prompt, user_prompt = rw_helpers._build_rw_prompt(source_text, domain_guidance=domain_guidance)

        return await PipelineRwRun(
            runner=self,
            job_id=job_id,
            rw_root=rw_root,
            source_text=source_text,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model_candidates=rw_helpers.MODEL_CANDIDATES,
            model_unavailable_cls=rw_helpers._ModelUnavailable,
            invoke_rw_candidate=rw_helpers._invoke_rw_candidate,
            apply_rw_qc=rw_helpers._apply_rw_qc,
        ).run()

    async def _execute_lines(self, job_id: str) -> dict[str, Any]:
        """Legacy fallback: structure selected RW draft into beats."""
        pipeline_id = self._load(job_id).pipeline_id
        return await PipelineLinesRun(
            runner=self,
            job_id=job_id,
            job_dir=self.video_jobs_dir / job_id,
            pipeline_id=pipeline_id,
            call_opus_for_rw=rw_helpers._call_opus_for_rw,
            model_id=DEFAULT_OPUS_MODEL_ID,
        ).run()

    async def _execute_storyboard(self, job_id: str) -> dict[str, Any]:
        """Legacy fallback: run director storyboard on episode beats."""
        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found; run lines first")
        beats_raw = ep.get("beats") or []
        if not beats_raw:
            raise ValueError("episode.beats is empty; run lines first")
        return await PipelineStoryboardRun(
            runner=self,
            job_id=job_id,
            episode=ep,
            beats_raw=beats_raw,
            call_opus_for_rw=rw_helpers._call_opus_for_rw,
            model_id=DEFAULT_OPUS_MODEL_ID,
        ).run()

    async def _execute_render(self, job_id: str) -> dict[str, Any]:
        """Legacy fallback: render final_preview MP4."""
        job_dir = self.video_jobs_dir / job_id
        from ncds_opus_factory.commands import render_final_preview as render_cmd

        return await PipelineRenderRun(
            runner=self,
            job_id=job_id,
            job_dir=job_dir,
            render_run=render_cmd.run,
        ).run()
