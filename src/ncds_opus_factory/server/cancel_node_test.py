"""单测：PipelineRunner S2 cancel 落盘化（watcher + cancel_node 文件标记）。

测试策略：
- 用 tmp_path 构造最简 PipelineRunner，monkey-patch 掉真实执行体
- 验证 cancel_node 会写文件标记
- 验证 watcher 检测到 flag 后 running 节点被 cancel 并状态落盘 idle
- 用 asyncio.run 驱动异步测试（与项目其余异步测试保持一致）
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from ncds_opus_factory.server.pipeline_runner import PipelineRunner
from ncds_opus_core.common.cancel import clear_flag, is_flagged, set_flag


# ---------------------------------------------------------------------------
# 最简 PipelineRunner fixture
# ---------------------------------------------------------------------------

def _make_runner(tmp_path: Path) -> PipelineRunner:
    return PipelineRunner(tmp_path / "video-jobs")


def _seed_job(runner: PipelineRunner, job_id: str, node: str) -> None:
    """在 video-jobs/{job_id}/pipeline_state.json 写最简 JobState，
    让 runner._load / _save 可用。"""
    job_dir = runner.video_jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "job_id": job_id,
        "pipeline_id": "final_preview",
        "title": "test",
        "created_at": time.time(),
        "updated_at": time.time(),
        "inputs": {},
        "nodes": {
            node: {
                "name": node,
                "status": "idle",
                "started_at": None,
                "finished_at": None,
                "progress": "",
                "outputs": {},
                "error": None,
                "task_id": None,
            }
        },
        "node_positions": {},
        "node_configs": {},
        "mock": False,
        "engine_iid": None,
    }
    (job_dir / "pipeline_state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# cancel_node 写文件标记
# ---------------------------------------------------------------------------

def test_cancel_node_writes_flag_ghost(tmp_path: Path) -> None:
    """cancel_node 幽灵路径：内存无 running task 且节点非 running，
    flag 被写入后立即 clear（幽灵路径完成后清标记）。"""
    runner = _make_runner(tmp_path)
    job_id = "job-001"
    node = "rw"
    _seed_job(runner, job_id, node)

    async def _run() -> bool:
        return await runner.cancel_node(job_id, node)

    result = asyncio.run(_run())
    assert result is True
    # 幽灵路径（节点 idle，非 running）：cancel_node 写完又 clear（幽灵路径末尾清理）
    flag = runner._cancel_flag(job_id, node)
    assert not is_flagged(flag), "幽灵路径完成后 flag 应已被 clear"


def test_cancel_node_writes_flag_for_running_task(tmp_path: Path) -> None:
    """内存有活 task 时，cancel_node 先写文件标记、再 cancel task。
    验证：cancel_node 返回后 flag 存在（watcher 尚未清）。"""
    runner = _make_runner(tmp_path)
    job_id = "job-002"
    node = "tts"
    _seed_job(runner, job_id, node)

    async def _run() -> bool:
        # 塞一个永不结束的 task 模拟 running 节点
        never_done: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(9999))
        runner._running_nodes[(job_id, node)] = never_done
        try:
            result = await runner.cancel_node(job_id, node)
            flag = runner._cancel_flag(job_id, node)
            # cancel_node 写 flag 后立即走内存 cancel；此时 watcher 还没运行，flag 应在
            assert is_flagged(flag), "cancel_node 应先写 flag 再 task.cancel()"
            return result
        finally:
            never_done.cancel()
            await asyncio.gather(never_done, return_exceptions=True)
            clear_flag(runner._cancel_flag(job_id, node))

    result = asyncio.run(_run())
    assert result is True


# ---------------------------------------------------------------------------
# watcher：flag 命中后节点被 cancel 且任务以 CancelledError 结束
# ---------------------------------------------------------------------------

def test_watcher_cancels_node_on_flag(tmp_path: Path) -> None:
    """模拟：节点正在跑，外部写文件标记 → watcher 检测到 → CancelledError。

    用极短 watcher 间隔（0.01s）让测试快速通过。
    fake_execute_real 无 checkpoint（sleep 9999），宽限期内不会协作式停止，
    故设 _CANCEL_GRACE_SEC=0.05 走强制 fallback，确保测试在 2s 内完成。
    """
    runner = _make_runner(tmp_path)
    runner._CANCEL_WATCHER_INTERVAL_SEC = 0.01  # 加速 watcher 轮询（仅测试用）
    runner._CANCEL_GRACE_SEC = 0.05  # 加速宽限期超时走强制 fallback（仅测试用）
    job_id = "job-003"
    node = "image"
    _seed_job(runner, job_id, node)

    execution_started = asyncio.Event()

    async def _fake_execute_real(jid: str, nname: str) -> None:
        """模拟长耗时任务：持续 sleep，标记自己已启动。"""
        execution_started.set()
        await asyncio.sleep(9999)

    async def _run() -> None:
        runner._execute_real = _fake_execute_real  # type: ignore[method-assign]

        task: asyncio.Task[None] = asyncio.create_task(
            runner._execute_real_with_flag_watcher(job_id, node)
        )

        # 等执行体启动
        await asyncio.wait_for(execution_started.wait(), timeout=2.0)

        # 写文件标记（模拟 cancel_node 的跨进程写端）
        flag = runner._cancel_flag(job_id, node)
        set_flag(flag)

        # 等待 task 结束（watcher 应在 ~0.01s 检测到 flag 并 cancel inner）
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.CancelledError:
            pass  # 预期

        assert task.done(), "watcher 命中 flag 后 task 应结束"
        # task 应以 cancelled 或 CancelledError 异常结束
        cancelled_ok = task.cancelled()
        exc_ok = (not task.cancelled()) and isinstance(task.exception(), asyncio.CancelledError)
        assert cancelled_ok or exc_ok, (
            f"task 应以 CancelledError 结束，实际: cancelled={task.cancelled()}, "
            f"exception={task.exception() if not task.cancelled() else 'N/A'}"
        )

    asyncio.run(_run())


def test_watcher_does_not_cancel_on_normal_completion(tmp_path: Path) -> None:
    """没有 flag 时，节点正常结束，watcher 静默停止（不泄漏，不误 cancel）。"""
    runner = _make_runner(tmp_path)
    runner._CANCEL_WATCHER_INTERVAL_SEC = 0.01
    job_id = "job-004"
    node = "lines"
    _seed_job(runner, job_id, node)

    async def _fast_execute_real(jid: str, nname: str) -> None:
        await asyncio.sleep(0.02)  # 比 watcher 间隔长一点确保 watcher 至少轮一次

    async def _run() -> None:
        runner._execute_real = _fast_execute_real  # type: ignore[method-assign]

        task: asyncio.Task[None] = asyncio.create_task(
            runner._execute_real_with_flag_watcher(job_id, node)
        )
        await asyncio.wait_for(task, timeout=2.0)

        assert task.done()
        assert not task.cancelled()
        exc = task.exception()
        assert exc is None, f"正常结束不应有异常: {exc}"

    asyncio.run(_run())
