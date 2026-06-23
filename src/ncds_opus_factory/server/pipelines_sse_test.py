"""SSE endpoint 测试：stream_events 的 snapshot→replay→tail 顺序、since_seq 分支、
断开处理。

测试机制：直接调用 stream_events 内部 gen() async generator（通过 monkeypatch 掉
routes.pipelines.PIPELINE_RUNNER），不需要启动完整 ASGI app。gen() 返回的每项
{"data": "<json-str>"}，断言解析后的 JSON 内容。

本项目没有 pytest-asyncio，异步测试一律用 asyncio.run() 包裹（与 cancel_node_test.py 一致）。

覆盖：
- (a) 默认无 since_seq：首帧 snapshot；之后 _emit 的增量能被 tail 收到（缺口1竞态修复验证）
- (b) since_seq=N：snapshot 后重放 seq>N 的历史，且与 tail 不重不漏
- (c) 客户端断开（CancelledError）→ gen() 正常退出，不抛未捕获异常
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

# 被测 route 模块（patch 的目标在此命名空间）
import ncds_opus_factory.server.routes.pipelines as pipelines_mod
from ncds_opus_factory.server.pipeline_runner import PipelineRunner
from ncds_opus_factory.server.routes.pipelines import stream_events


# ---------------------------------------------------------------------------
# 辅助：构造最简 job state 文件
# ---------------------------------------------------------------------------

def _seed_job(runner: PipelineRunner, job_id: str) -> None:
    """在 video-jobs/{job_id}/pipeline_state.json 写最简 JobState，
    以便 runner.get_job() 可正常读取。"""
    job_dir = runner.video_jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "job_id": job_id,
        "pipeline_id": "final_preview",
        "title": "test",
        "created_at": time.time(),
        "updated_at": time.time(),
        "inputs": {},
        "nodes": {},
        "node_positions": {},
        "node_configs": {},
        "mock": False,
        "engine_iid": None,
    }
    (job_dir / "pipeline_state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


def _parse_data(item: dict) -> dict:
    """从 gen() yield 的 {"data": "<json>"} 中解析 JSON。"""
    return json.loads(item["data"])


async def _get_gen(job_id: str, since_seq: int | None = None) -> Any:
    """调用 stream_events，取出内部 body_iterator（= gen()）。"""
    resp = await stream_events(job_id=job_id, since_seq=since_seq)
    # sse_starlette.EventSourceResponse 把 generator 存在 body_iterator
    return resp.body_iterator


# ---------------------------------------------------------------------------
# (a) 默认无 since_seq：首帧 snapshot + 后续 tail 增量不丢（缺口1核心验证）
# ---------------------------------------------------------------------------

class TestStreamEventsDefault:
    def test_first_frame_is_snapshot(self, tmp_path: Path) -> None:
        """首帧必须是 type=="snapshot"，包含 job_id 和 state。"""
        runner = PipelineRunner(video_jobs_dir=tmp_path)
        job_id = "sse-a-001"
        _seed_job(runner, job_id)

        async def _run() -> None:
            with patch.object(pipelines_mod, "PIPELINE_RUNNER", runner):
                gen = await _get_gen(job_id)
                first = await gen.__anext__()
                parsed = _parse_data(first)
                assert parsed["type"] == "snapshot", (
                    f"首帧 type 应为 snapshot，got: {parsed}"
                )
                assert parsed["job_id"] == job_id
                assert "state" in parsed
                await gen.aclose()

        asyncio.run(_run())

    def test_incremental_emit_reaches_tail(self, tmp_path: Path) -> None:
        """竞态修复验证（缺口1）：
        连接后 _emit 的新事件必须通过 tail 到达客户端（不丢）。

        策略：
        1. 收到 snapshot 帧后立即 _emit 一个 node_status；
        2. 继续迭代 gen，设置短超时等待 tail 轮询拿到该事件；
        3. 断言 tail 发来的帧 type=="node_status"，seq==1。
        """
        runner = PipelineRunner(video_jobs_dir=tmp_path)
        job_id = "sse-a-002"
        _seed_job(runner, job_id)

        async def _run() -> None:
            # tail 轮询间隔临时改短加速测试
            original_interval = pipelines_mod._TAIL_POLL_INTERVAL
            pipelines_mod._TAIL_POLL_INTERVAL = 0.05
            try:
                with patch.object(pipelines_mod, "PIPELINE_RUNNER", runner):
                    gen = await _get_gen(job_id)

                    # 首帧 snapshot
                    first = await gen.__anext__()
                    assert _parse_data(first)["type"] == "snapshot"

                    # snapshot 后立即 emit（竞态窗口内的增量）
                    runner._emit(job_id, {
                        "type": "node_status",
                        "job_id": job_id,
                        "node": "asr",
                        "state": {"status": "running"},
                    })

                    # tail 轮询应在 0.05s 内拿到事件
                    second = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
                    parsed_second = _parse_data(second)
                    assert parsed_second["type"] == "node_status", (
                        f"tail 应发出 node_status，实际: {parsed_second}"
                    )
                    assert parsed_second["node"] == "asr"
                    assert parsed_second["seq"] == 1
                    await gen.aclose()
            finally:
                pipelines_mod._TAIL_POLL_INTERVAL = original_interval

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# (b) since_seq=N：snapshot 后重放 seq>N，且与 tail 不重不漏
# ---------------------------------------------------------------------------

class TestStreamEventsSinceSeq:
    def test_since_seq_replay_and_no_duplicate(self, tmp_path: Path) -> None:
        """since_seq=3 时：先 snapshot，再重放 seq 4..5，tail 后续增量不重放历史。

        写 5 条历史 → 连接 since_seq=3 → 预期：snapshot + replay(seq=4,5) + tail(seq=6...)。
        """
        runner = PipelineRunner(video_jobs_dir=tmp_path)
        job_id = "sse-b-001"
        _seed_job(runner, job_id)

        # 先写 5 条历史事件
        for i in range(5):
            runner._emit(job_id, {
                "type": "node_status",
                "job_id": job_id,
                "node": f"n{i}",
                "state": {"status": "done"},
            })

        async def _run() -> None:
            original_interval = pipelines_mod._TAIL_POLL_INTERVAL
            pipelines_mod._TAIL_POLL_INTERVAL = 0.05
            try:
                with patch.object(pipelines_mod, "PIPELINE_RUNNER", runner):
                    gen = await _get_gen(job_id, since_seq=3)

                    # 首帧：snapshot
                    first = await gen.__anext__()
                    assert _parse_data(first)["type"] == "snapshot"

                    # 重放：seq > 3 → 应收到 seq=4 和 seq=5
                    replay4 = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
                    replay5 = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
                    p4 = _parse_data(replay4)
                    p5 = _parse_data(replay5)
                    assert p4["seq"] == 4, f"第一条 replay 应为 seq=4，got {p4['seq']}"
                    assert p5["seq"] == 5, f"第二条 replay 应为 seq=5，got {p5['seq']}"

                    # 写一条新增量（seq=6）
                    runner._emit(job_id, {
                        "type": "node_status",
                        "job_id": job_id,
                        "node": "rw",
                        "state": {"status": "running"},
                    })

                    # tail 拿到 seq=6，不重放历史
                    new_item = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
                    p_new = _parse_data(new_item)
                    assert p_new["seq"] == 6, (
                        f"tail 新增应为 seq=6，got {p_new['seq']}"
                    )
                    await gen.aclose()
            finally:
                pipelines_mod._TAIL_POLL_INTERVAL = original_interval

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# (c) 客户端断开（CancelledError）→ gen() 正常退出，不抛未捕获异常
# ---------------------------------------------------------------------------

class TestStreamEventsDisconnect:
    def test_client_disconnect_no_uncaught_exception(self, tmp_path: Path) -> None:
        """断开（aclose）不应留下未捕获异常，CancelledError 正常吸收。"""
        runner = PipelineRunner(video_jobs_dir=tmp_path)
        job_id = "sse-c-001"
        _seed_job(runner, job_id)

        async def _run() -> None:
            with patch.object(pipelines_mod, "PIPELINE_RUNNER", runner):
                gen = await _get_gen(job_id)

                # 拿第一帧（snapshot）
                first = await gen.__anext__()
                assert _parse_data(first)["type"] == "snapshot"

                # 模拟客户端断开：aclose() → gen 内部 CancelledError → raise → 正常退出
                # 若 gen 内部有未捕获异常，aclose() 会重新抛出
                await gen.aclose()  # 不应抛异常

        asyncio.run(_run())

    def test_cancelled_error_propagated_correctly(self, tmp_path: Path) -> None:
        """通过 asyncio.Task.cancel() 触发取消，验证 gen 能正确退出 tail 循环。"""
        runner = PipelineRunner(video_jobs_dir=tmp_path)
        job_id = "sse-c-002"
        _seed_job(runner, job_id)

        async def _run() -> None:
            original_interval = pipelines_mod._TAIL_POLL_INTERVAL
            pipelines_mod._TAIL_POLL_INTERVAL = 0.05
            try:
                with patch.object(pipelines_mod, "PIPELINE_RUNNER", runner):
                    gen = await _get_gen(job_id)

                    async def _consume() -> None:
                        async for _ in gen:
                            break  # 拿到 snapshot 后停，让 task 在 tail sleep 时被 cancel

                    task = asyncio.create_task(_consume())
                    # 短暂等待让 gen 进入 tail sleep
                    await asyncio.sleep(0.15)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass  # 预期：任务被取消

                    assert task.done(), "task 应已结束"
            finally:
                pipelines_mod._TAIL_POLL_INTERVAL = original_interval
                try:
                    await gen.aclose()
                except Exception:
                    pass

        asyncio.run(_run())
