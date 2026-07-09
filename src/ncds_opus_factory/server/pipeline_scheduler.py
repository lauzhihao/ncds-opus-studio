"""Scheduling, cancellation, and node dispatch for PipelineRunner."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ncds_opus_core.common import cancel as _cancel
from ncds_opus_core.pipelines import get_pipeline

from ncds_opus_factory.server.pipeline_models import NodeState

logger = logging.getLogger(__name__)


class PipelineSchedulerMixin:
    """Run-node scheduler, file-flag cancellation, and facade state transitions."""

    _CANCEL_WATCHER_INTERVAL_SEC: float = 0.5
    _CANCEL_GRACE_SEC: float = 15.0
    _ORPHAN_GRACE_SEC: float = 10.0
    _PIPELINE_TASK_CMD: str = "pipeline_node"
    _LEGACY_ONLY_NODES: set[str] = {"asr", "rw"}

    def attach_task_runner(self, task_runner: Any) -> None:
        """Inject the process-wide TaskRunner.

        Production server uses this to enqueue long-running `/jobs` node work
        into nof-worker. Bare test runners leave it unset and keep the legacy
        in-process execution path.
        """
        self._task_runner = task_runner

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

        current = state.nodes[node_name]
        if current.status in {"queued", "running"}:
            self._emit(job_id, {
                "type": "node_status", "job_id": job_id, "node": node_name,
                "state": asdict(current),
            })
            return

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

        task_runner = getattr(self, "_task_runner", None)
        if task_runner is not None:
            try:
                task_id = await task_runner.submit(
                    self._PIPELINE_TASK_CMD,
                    {"job_id": job_id, "node_name": node_name},
                    source="pipeline",
                )
            except Exception:
                failed = self._load(job_id)
                fn = failed.nodes[node_name]
                fn.status = "failed"
                fn.finished_at = time.time()
                fn.error = "节点调度失败，请稍后重试"
                self._save(failed)
                self._emit(
                    job_id,
                    {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(fn)},
                )
                raise
            queued = self._load(job_id)
            qn = queued.nodes[node_name]
            qn.task_id = task_id
            self._save(queued)
            self._emit(
                job_id,
                {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(qn)},
            )
            return

        key = (job_id, node_name)
        if key in self._running_nodes and not self._running_nodes[key].done():
            return
        self._running_nodes[key] = asyncio.create_task(self._execute(job_id, node_name))

    def run_pipeline_node_task(
        self,
        *,
        job_id: str,
        node_name: str,
        on_progress: Callable[[str], None],
        **_: Any,
    ) -> dict[str, Any]:
        """TaskRunner command: execute a queued pipeline node inside nof-worker."""

        async def _run() -> dict[str, Any]:
            on_progress(f"pipeline node {job_id}/{node_name} started")
            active = self._find_pipeline_task(job_id, node_name, statuses={"running", "pending"})
            if active is not None:
                self._stamp_pipeline_task(job_id, node_name, active.task_id)
            await self._execute(job_id, node_name)
            state = self._load(job_id)
            node = state.nodes[node_name]
            if node.status == "done":
                return {"job_id": job_id, "node": node_name, "outputs": node.outputs}
            if node.error == "cancelled":
                raise _cancel.TaskCancelled(f"pipeline node cancelled: {job_id}/{node_name}")
            if node.status == "failed":
                raise RuntimeError(node.error or f"pipeline node failed: {job_id}/{node_name}")
            raise RuntimeError(f"pipeline node ended with status={node.status}: {job_id}/{node_name}")

        return asyncio.run(_run())

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
        n.progress = "执行中..."
        n.error = None
        n.outputs = {}
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

        await asyncio.sleep(mock_mod.mock_delay_seconds(node_name))
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

        try:
            state_for_task = self._load(job_id)
            task_id = state_for_task.nodes.get(node_name).task_id if state_for_task.nodes.get(node_name) else None
        except FileNotFoundError:
            task_id = None
        except KeyError:
            task_id = None
        task_runner = getattr(self, "_task_runner", None)
        if task_runner is not None and task_id:
            try:
                meta = task_runner.store.get_meta(task_id)
                if meta is not None and meta.status in ("pending", "running"):
                    await task_runner.cancel_task(task_id)
                    task_runner.store.update_status(task_id, "cancelled")
                    task_runner.store.append_progress(task_id, "pipeline node 已被用户取消")
            except Exception as exc:  # noqa: BLE001
                logger.warning("[pipeline] cancel task mirror failed: %s", exc)

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

        if (
            self._engine is not None
            and node_name in self._engine_nodes
            and node_name not in self._LEGACY_ONLY_NODES
        ):
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
        elif node_name == "preview":
            outputs = {}
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

    def _pipeline_task_meta(self, task_id: str | None) -> Any | None:
        if not task_id:
            return None
        task_runner = getattr(self, "_task_runner", None)
        if task_runner is None:
            return None
        try:
            return task_runner.store.get_meta(task_id)
        except Exception:  # noqa: BLE001
            return None

    def _find_pipeline_task(
        self,
        job_id: str,
        node_name: str,
        *,
        statuses: set[str] | None = None,
    ) -> Any | None:
        task_runner = getattr(self, "_task_runner", None)
        if task_runner is None:
            return None
        try:
            for meta in task_runner.store.list_tasks():
                if meta.cmd != self._PIPELINE_TASK_CMD:
                    continue
                if statuses is not None and meta.status not in statuses:
                    continue
                params = meta.params or {}
                if params.get("job_id") == job_id and params.get("node_name") == node_name:
                    return meta
        except Exception:  # noqa: BLE001
            return None
        return None

    def _stamp_pipeline_task(self, job_id: str, node_name: str, task_id: str) -> None:
        try:
            state = self._load(job_id)
            node = state.nodes.get(node_name)
            if node is None:
                return
            if node.task_id != task_id:
                node.task_id = task_id
                self._save(state)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pipeline] stamp task id failed: %s", exc)

    def _has_live_pipeline_executor(self, job_id: str, node_name: str, node: NodeState) -> bool:
        key = (job_id, node_name)
        task = self._running_nodes.get(key)
        if task is not None and not task.done():
            return True
        meta = self._pipeline_task_meta(node.task_id)
        if meta and meta.cmd == self._PIPELINE_TASK_CMD and meta.status in ("pending", "running"):
            return True
        return self._find_pipeline_task(job_id, node_name, statuses={"pending", "running"}) is not None

    def _node_age_sec(self, state: Any, node: NodeState) -> float:
        anchor = node.started_at or state.updated_at or state.created_at
        try:
            return max(0.0, time.time() - float(anchor))
        except (TypeError, ValueError):
            return self._ORPHAN_GRACE_SEC + 1.0

    def reconcile_runtime_state(self, state: Any, *, emit: bool = False) -> Any:
        """Converge facade node status with the real scheduler/engine state.

        This keeps stale `/jobs` running states from surviving a server restart.
        Correct live work is identified by either an in-process task (legacy
        tests/dev path) or a TaskRunner `pipeline_node` task (nof-worker path).
        """
        changed = False
        for node_name, node in state.nodes.items():
            active = self._find_pipeline_task(state.job_id, node_name, statuses={"pending", "running"})
            if active is not None:
                desired_status = "queued" if active.status == "pending" else "running"
                if (
                    node.status != desired_status
                    or node.task_id != active.task_id
                    or node.error is not None
                    or node.finished_at is not None
                ):
                    node.status = desired_status
                    node.task_id = active.task_id
                    node.error = None
                    node.finished_at = None
                    if not node.started_at and active.started_at:
                        node.started_at = time.time()
                    changed = True
                continue

            if node.status not in ("queued", "running"):
                continue
            if self._has_live_pipeline_executor(state.job_id, node_name, node):
                continue

            meta = self._pipeline_task_meta(node.task_id)
            if meta is not None and meta.cmd == self._PIPELINE_TASK_CMD:
                if meta.status == "completed":
                    result = getattr(self._task_runner.store, "get_result")(meta.task_id) or {}
                    outputs = result.get("outputs") if isinstance(result, dict) else None
                    node.outputs = outputs if isinstance(outputs, dict) else node.outputs
                    node.status = "done"
                    node.progress = "完成"
                    node.error = None
                    node.finished_at = time.time()
                    changed = True
                    continue
                if meta.status == "cancelled":
                    self._reset_node(node)
                    node.error = "cancelled"
                    node.finished_at = time.time()
                    changed = True
                    continue
                if meta.status == "failed":
                    node.status = "failed"
                    node.error = meta.error or "后台任务失败，请重试"
                    node.finished_at = time.time()
                    changed = True
                    continue

            if self._engine is not None and state.engine_iid and self._engine.store.exists(state.engine_iid):
                step = self._engine.store.get_step_state(state.engine_iid, node_name)
                if step is not None and step.status == "done":
                    node.status = "done"
                    node.outputs = dict(step.outputs)
                    node.progress = "完成"
                    node.error = None
                    node.finished_at = time.time()
                    changed = True
                    continue
                if step is not None and step.status == "failed":
                    node.status = "failed"
                    node.error = step.error or "引擎步骤失败，请重试"
                    node.finished_at = time.time()
                    changed = True
                    continue

            if self._node_age_sec(state, node) < self._ORPHAN_GRACE_SEC:
                continue
            node.status = "failed"
            node.error = "后台执行已中断，请重新执行该节点"
            node.finished_at = time.time()
            changed = True

        if changed:
            self._save(state)
            if emit:
                for node_name, node in state.nodes.items():
                    self._emit(
                        state.job_id,
                        {"type": "node_status", "job_id": state.job_id, "node": node_name, "state": asdict(node)},
                    )
        return state

    async def handle_pipeline_terminal(self, meta: Any, status: str, result: dict[str, Any] | None) -> None:
        """TaskRunner terminal hook: push finished pipeline_node status back to /jobs SSE."""
        if getattr(meta, "cmd", None) != self._PIPELINE_TASK_CMD:
            return
        params = getattr(meta, "params", None) or {}
        job_id = params.get("job_id")
        node_name = params.get("node_name")
        if not isinstance(job_id, str) or not isinstance(node_name, str):
            return
        try:
            state = self.reconcile_runtime_state(self._load(job_id), emit=True)
            node = state.nodes.get(node_name)
            if node is not None and node.status in {"queued", "running"}:
                if status == "completed":
                    outputs = result.get("outputs") if isinstance(result, dict) else None
                    node.outputs = outputs if isinstance(outputs, dict) else node.outputs
                    node.status = "done"
                    node.progress = "完成"
                    node.error = None
                elif status == "failed":
                    node.status = "failed"
                    node.error = getattr(meta, "error", None) or "后台任务失败，请重试"
                elif status == "cancelled":
                    self._reset_node(node)
                    node.error = "cancelled"
                node.finished_at = time.time()
                self._save(state)
                self._emit(
                    job_id,
                    {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(node)},
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pipeline] terminal reconcile failed %s/%s: %s", job_id, node_name, exc)
