"""task_runner 配额下沉到 Redis 的单元测试（S3 步2）。

所有测试用 fakeredis 注入，不连真 Redis。
conftest.py 的 _fake_redis_queue fixture 已对 state.RUNNER 做隔离注入；
此文件直接构造独立 TaskRunner 实例，更精确控制 fakeredis 共享/隔离场景。

场景：
1. test_quota_exceeded_in_run —— 超额：submit N+1 个(N=limit)，_run 时前 N 个放行，
   第 N+1 个 _run 走 failed（断言 status+error）。
2. test_quota_remaining_after_partial_use —— quota_remaining 在消耗一半配额后返回正确剩余。
3. test_quota_cross_process_no_double_count —— 跨进程一致：两个 TaskRunner 共享同一
   FakeServer，各自 _run 同桶，总放行数 == limit（不翻倍）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis

from ncds_opus_factory.server.queue import RedisQueue
from ncds_opus_factory.server.task_store import TaskStore


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _make_runner(store: TaskStore, fake_server: FakeServer, cmd: str):
    """构造使用 fakeredis 的 TaskRunner，registry 只有一个同步 no-op 命令。"""
    from ncds_opus_factory.server.task_runner import TaskRunner

    def run_fn(on_progress, **kwargs) -> dict[str, Any]:
        return {"ok": True}

    fake_client = FakeRedis(server=fake_server)
    rq = RedisQueue(client=fake_client)
    return TaskRunner(store, {cmd: run_fn}, redis_queue=rq)


# ---------------------------------------------------------------------------
# 测试 1：超额 —— 第 limit+1 个 _run 走 failed
# ---------------------------------------------------------------------------

def test_quota_exceeded_in_run(tmp_path):
    """submit N+1 个任务后跑 _run：前 N 个 completed，第 N+1 个 failed。"""
    import ncds_opus_factory.server.task_runner as _tr_mod

    cmd = "shenkuo"
    original = dict(_tr_mod.DAILY_QUOTA)
    _tr_mod.DAILY_QUOTA[cmd] = 3
    limit = 3

    async def run():
        store = TaskStore(tmp_path / "tasks")
        fake_server = FakeServer()
        runner = _make_runner(store, fake_server, cmd)

        # submit limit+1 个任务（source=None → 走 shenkuo 配额桶，非 cron）
        task_ids = []
        for i in range(limit + 1):
            tid = await runner.submit(cmd, {"aweme": f"v_{i}"}, source=None)
            task_ids.append(tid)

        # 所有任务此时都是 pending（submit 不再判配额）
        for tid in task_ids:
            meta = store.get_meta(tid)
            assert meta is not None and meta.status == "pending", (
                f"{tid} 应为 pending，实际 {meta.status}"
            )

        # 逐一调 _run（不走 asyncio.Queue，直接测配额判定路径）
        for tid in task_ids:
            await runner._run(tid, cmd)

        # 统计结果
        completed = [t for t in task_ids if store.get_meta(t).status == "completed"]
        failed = [t for t in task_ids if store.get_meta(t).status == "failed"]

        assert len(completed) == limit, (
            f"期望 {limit} 个 completed，实际 {len(completed)}"
        )
        assert len(failed) == 1, f"期望 1 个 failed，实际 {len(failed)}"

        # failed 的那个任务：error 消息含"配额"
        failed_meta = store.get_meta(failed[0])
        assert "配额" in (failed_meta.error or ""), "error 消息应包含'配额'"
        # source=None → 非 cron/retro/round → _maybe_auto_archive 不写 review
        review = store.get_review(failed[0])
        assert review is None, "普通任务超额不自动归档，review 应为 None"

    try:
        asyncio.run(run())
    finally:
        _tr_mod.DAILY_QUOTA.update(original)


# ---------------------------------------------------------------------------
# 测试 2：quota_remaining 在消耗一半配额后返回正确剩余
# ---------------------------------------------------------------------------

def test_quota_remaining_after_partial_use(tmp_path):
    """incr 到一半配额后，quota_remaining 返回正确剩余值。"""
    import ncds_opus_factory.server.task_runner as _tr_mod
    from ncds_opus_factory.server.task_runner import _today

    cmd = "guiguzi"
    original_limit = _tr_mod.DAILY_QUOTA.get(cmd, _tr_mod.DAILY_QUOTA["_default"])
    test_limit = 6
    _tr_mod.DAILY_QUOTA[cmd] = test_limit

    async def run():
        store = TaskStore(tmp_path / "tasks")
        fake_server = FakeServer()
        runner = _make_runner(store, fake_server, cmd)

        half = test_limit // 2  # 3

        # 直接用 incr_quota 模拟消耗 half 个配额
        for _ in range(half):
            n, ok = await runner._rq.incr_quota(cmd, _today(), test_limit)
            assert ok, f"前 {half} 次 incr 应放行"

        remaining = await runner.quota_remaining(cmd, source=None)
        assert remaining == test_limit - half, (
            f"期望剩余 {test_limit - half}，实际 {remaining}"
        )

    try:
        asyncio.run(run())
    finally:
        _tr_mod.DAILY_QUOTA[cmd] = original_limit


# ---------------------------------------------------------------------------
# 测试 3：跨进程一致 —— 两个 TaskRunner 共享同一 FakeServer，总放行 == limit
# ---------------------------------------------------------------------------

def test_quota_cross_process_no_double_count(tmp_path):
    """模拟 8810 + worker 两进程：两个 TaskRunner 共享同一 FakeServer，
    各自分别 _run 同一配额桶，总放行数恰好等于 limit，不翻倍。
    """
    import ncds_opus_factory.server.task_runner as _tr_mod

    cmd = "wolong"
    original_limit = _tr_mod.DAILY_QUOTA.get(cmd, _tr_mod.DAILY_QUOTA["_default"])
    test_limit = 4
    _tr_mod.DAILY_QUOTA[cmd] = test_limit

    async def run():
        # 同一 FakeServer 模拟共享 Redis
        fake_server = FakeServer()
        # 共享同一 TaskStore（模拟同磁盘）
        store = TaskStore(tmp_path / "tasks")

        def run_fn(on_progress, **kwargs) -> dict[str, Any]:
            return {"ok": True}

        def _build_runner() -> Any:
            from ncds_opus_factory.server.task_runner import TaskRunner
            client = FakeRedis(server=fake_server)
            rq = RedisQueue(client=client)
            return TaskRunner(store, {cmd: run_fn}, redis_queue=rq)

        runner_a = _build_runner()
        runner_b = _build_runner()

        total = test_limit + 2  # 6 个任务，只有 4 个应放行

        # 用 runner_a 提交所有任务（submit 不判配额）
        task_ids = []
        for i in range(total):
            tid = await runner_a.submit(cmd, {"_dispatch_task_id": f"fake_{i}"}, source=None)
            task_ids.append(tid)

        # runner_a 和 runner_b 分批分担出队执行（模拟两进程）
        for tid in task_ids[:total // 2]:
            await runner_a._run(tid, cmd)
        for tid in task_ids[total // 2:]:
            await runner_b._run(tid, cmd)

        # 统计最终状态
        completed = sum(
            1 for tid in task_ids
            if store.get_meta(tid) and store.get_meta(tid).status == "completed"
        )
        failed = sum(
            1 for tid in task_ids
            if store.get_meta(tid) and store.get_meta(tid).status == "failed"
        )

        assert completed == test_limit, (
            f"两 runner 共享 fakeredis，总放行应 == {test_limit}，实际 {completed}"
        )
        assert failed == total - test_limit, (
            f"超额数应 == {total - test_limit}，实际 {failed}"
        )

    try:
        asyncio.run(run())
    finally:
        _tr_mod.DAILY_QUOTA[cmd] = original_limit
