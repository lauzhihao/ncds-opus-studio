"""Scheduling, cancellation, and node dispatch for PipelineRunner."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from ncds_opus_core.common import cancel as _cancel
from ncds_opus_core.pipelines import get_pipeline
from ncds_opus_factory.server.pipeline_models import NodeState

logger = logging.getLogger(__name__)


class PipelineSchedulerMixin:
    """Run-node scheduler, file-flag cancellation, and facade state transitions."""

    _CANCEL_WATCHER_INTERVAL_SEC: float = 0.5
    _CANCEL_GRACE_SEC: float = 15.0

    def _cancel_flag(self, job_id: str, node_name: str) -> Path:
        return self.video_jobs_dir / job_id / "cancel" / f"{node_name}.flag"

    async def _run_in_thread_cancellable(self, fn: Callable, flag_path: Path, /, *args: Any, **kwargs: Any) -> Any:
        """Run sync work in a thread with cancel.current() backed by the file flag."""
        def _wrapped() -> Any:
            _cancel.install(lambda: _cancel.is_flagged(flag_path))
            try:
                return fn(*args, **kwargs)
            finally:
                _cancel.uninstall()
        return await asyncio.to_thread(_wrapped)

    def _reset_node(self, n: NodeState) -> None:
        n.status = "idle"
        n.started_at = None
        n.finished_at = None
        n.progress = ""
        n.outputs = {}
        n.error = None
        n.task_id = None

    async def run_node(
        self,
        job_id: str,
        node_name: str,
        params: dict[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        """触发某节点执行。会把节点及其下游全部 reset 后再排队。"""
        state = self._load(job_id)
        pipeline = get_pipeline(state.pipeline_id)
        node = pipeline.node(node_name)
        if node.kind in ("input", "output"):
            raise ValueError(f"node {node_name} is UI-only, not runnable")

        if params:
            cfg = dict(state.node_configs.get(node_name) or {})
            cfg.update(params)
            state.node_configs[node_name] = cfg
            self._save(state)

        if (
            not force
            and node_name == "asr"
            and state.nodes[node_name].status == "done"
            and state.nodes[node_name].outputs
        ):
            self._emit(job_id, {
                "type": "node_status", "job_id": job_id, "node": node_name,
                "state": asdict(state.nodes[node_name]),
            })
            return

        for dep in node.deps:
            if state.nodes[dep].status != "done":
                raise RuntimeError(
                    f"cannot run {node_name}: dep {dep} status={state.nodes[dep].status}"
                )

        self._reset_node(state.nodes[node_name])
        for dn in pipeline.downstream_of(node_name):
            if state.nodes[dn].status != "idle":
                self._reset_node(state.nodes[dn])

        state.nodes[node_name].status = "queued"
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(state.nodes[node_name])})

        key = (job_id, node_name)
        if key in self._running_nodes and not self._running_nodes[key].done():
            return
        self._running_nodes[key] = asyncio.create_task(self._execute(job_id, node_name))

    async def _execute(self, job_id: str, node_name: str) -> None:
        try:
            if self._load(job_id).mock:
                await self._execute_mock(job_id, node_name)
            else:
                await self._execute_real_with_flag_watcher(job_id, node_name)
        except asyncio.CancelledError:
            try:
                state = self._load(job_id)
                n = state.nodes[node_name]
                self._reset_node(n)
                n.error = "cancelled"
                n.finished_at = time.time()
                self._save(state)
                self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})
            finally:
                _cancel.clear_flag(self._cancel_flag(job_id, node_name))
                raise
        except _cancel.TaskCancelled:
            try:
                state = self._load(job_id)
                n = state.nodes[node_name]
                self._reset_node(n)
                n.error = "cancelled"
                n.finished_at = time.time()
                self._save(state)
                self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})
            finally:
                _cancel.clear_flag(self._cancel_flag(job_id, node_name))
        except Exception as exc:
            logger.exception("[pipeline] node %s/%s failed", job_id, node_name)
            state = self._load(job_id)
            n = state.nodes[node_name]
            n.status = "failed"
            n.error = f"{type(exc).__name__}: {exc}"
            n.finished_at = time.time()
            self._save(state)
            self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})
        finally:
            self._running_nodes.pop((job_id, node_name), None)

    async def _execute_real_with_flag_watcher(self, job_id: str, node_name: str) -> None:
        """Wrap _execute_real with a file-flag watcher for cooperative cancellation."""
        flag_path = self._cancel_flag(job_id, node_name)

        async def _watcher() -> None:
            while True:
                await asyncio.sleep(self._CANCEL_WATCHER_INTERVAL_SEC)
                if _cancel.is_flagged(flag_path):
                    return

        inner: asyncio.Task[None] = asyncio.create_task(self._execute_real(job_id, node_name))
        watcher: asyncio.Task[None] = asyncio.create_task(_watcher())
        try:
            done, _pending = await asyncio.wait(
                {inner, watcher},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            inner.cancel()
            watcher.cancel()
            await asyncio.gather(inner, watcher, return_exceptions=True)
            raise

        if watcher in done and not watcher.cancelled():
            if not inner.done():
                done2, _ = await asyncio.wait({inner}, timeout=self._CANCEL_GRACE_SEC)
                if inner not in done2:
                    inner.cancel()
                    await asyncio.gather(inner, return_exceptions=True)
                    raise asyncio.CancelledError("cancel flag detected (forced after grace)")
            if inner.cancelled():
                raise asyncio.CancelledError("cancel flag detected")
            exc = inner.exception()
            if exc is not None:
                raise exc
            return

        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        exc = inner.exception()
        if exc is not None:
            raise exc

    async def _execute_mock(self, job_id: str, node_name: str) -> None:
        """Mock execution path: sleep, then copy the static mock artifact for the node."""
        from ncds_opus_factory.server import mock as mock_mod

        state = self._load(job_id)
        n = state.nodes[node_name]
        n.status = "running"
        n.started_at = time.time()
        n.progress = "mock 执行中..."
        n.error = None
        n.outputs = {}
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

        await asyncio.sleep(mock_mod.MOCK_NODE_DELAY_SEC)
        job_dir = self.video_jobs_dir / job_id
        outputs = await asyncio.to_thread(mock_mod.run_mock_node, job_dir, node_name)

        state = self._load(job_id)
        n = state.nodes[node_name]
        n.status = "done"
        n.finished_at = time.time()
        n.progress = "完成"
        n.outputs = outputs
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

    async def cancel_node(self, job_id: str, node_name: str) -> bool:
        """取消节点。幂等：内存里没有活着的 task 也视为取消成功。"""
        _cancel.set_flag(self._cancel_flag(job_id, node_name))

        enrich = self._enrich_tasks.get(job_id)
        enrich_cancelled = False
        if enrich is not None and not enrich.done():
            enrich.cancel()
            enrich_cancelled = True

        key = (job_id, node_name)
        task = self._running_nodes.get(key)
        if task is not None and not task.done():
            try:
                is_mock = self._load(job_id).mock
            except FileNotFoundError:
                is_mock = False
            if is_mock:
                task.cancel()
            return True

        try:
            state = self._load(job_id)
        except FileNotFoundError:
            _cancel.clear_flag(self._cancel_flag(job_id, node_name))
            return True
        n = state.nodes.get(node_name)
        if n is not None and n.status in ("running", "queued"):
            self._reset_node(n)
            n.error = "cancelled"
            n.finished_at = time.time()
            self._save(state)
            self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})
        if not enrich_cancelled:
            _cancel.clear_flag(self._cancel_flag(job_id, node_name))
        return True

    async def _execute_real(self, job_id: str, node_name: str) -> None:
        """真实执行：按 node_name 分发到对应实现。"""
        _cancel.clear_flag(self._cancel_flag(job_id, node_name))
        state = self._load(job_id)
        n = state.nodes[node_name]
        n.status = "running"
        n.started_at = time.time()
        n.progress = "启动..."
        n.error = None
        n.outputs = {}
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

        if self._engine is not None and node_name in self._engine_nodes and node_name != "asr":
            outputs = await self._execute_via_engine(job_id, node_name)
        elif node_name == "tts":
            outputs = await self._execute_tts(job_id)
        elif node_name == "image":
            outputs = await self._execute_image(job_id)
        elif node_name == "asr":
            outputs = await self._execute_asr_collect(job_id)
        elif node_name == "rw":
            outputs = await self._execute_rw(job_id)
        elif node_name == "lines":
            outputs = await self._execute_lines(job_id)
        elif node_name == "storyboard":
            outputs = await self._execute_storyboard(job_id)
        elif node_name == "render":
            outputs = await self._execute_render(job_id)
        else:
            raise ValueError(f"unknown runnable node: {node_name}")

        state = self._load(job_id)
        n = state.nodes[node_name]
        n.status = "done"
        n.finished_at = time.time()
        n.progress = "完成"
        n.outputs = outputs
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

        if node_name == "asr":
            self._spawn_asr_enrich(job_id)
