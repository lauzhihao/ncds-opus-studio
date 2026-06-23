"""步9 降级单元测试（fakeredis autouse，不连真 Redis）。

Part A — submit 入队失败 → EnqueueUnavailable + meta failed + POST 503
Part B — loop ping-guard：ping False 时 loop 一个 tick 不 create meta、不抛
Part C — brpop TimeoutError 当正常空轮询（退避不增大）vs ConnectionError（退避增大）

技术笔记（socket_timeout）：
  RedisQueue 默认用 aioredis.from_url(url) 不传 socket_timeout，
  所以生产环境的正常空轮询走服务端 nil 返回（result=None 分支），不会抛 RedisTimeoutError。
  但若调用方（或 redis-py 默认）设了 socket_timeout <= brpop timeout，
  socket 层会在服务端 nil 到达之前先超时，抛 RedisTimeoutError——此即步8 目睹的
  "Timeout reading from ..." WARNING 的根因。Part C 的修复把这类超时当正常空轮询处理。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import redis.exceptions
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from fastapi.testclient import TestClient

from ncds_opus_factory.server.queue import RedisQueue, _BACKOFF_INIT
from ncds_opus_factory.server.task_store import TaskStore


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _make_runner(store: TaskStore, cmd: str, fake_server: "FakeServer | None" = None):
    """构造独立 TaskRunner（fakeredis），registry 注册一个 no-op 同步命令。"""
    from ncds_opus_factory.server.task_runner import TaskRunner

    def run_fn(on_progress=None, **kwargs) -> dict[str, Any]:
        return {"ok": True}

    fs = fake_server or FakeServer()
    fake_client = FakeRedis(server=fs)
    rq = RedisQueue(client=fake_client)
    return TaskRunner(store, {cmd: run_fn}, redis_queue=rq), rq


# ---------------------------------------------------------------------------
# Part A — submit 入队失败 → EnqueueUnavailable + meta failed
# ---------------------------------------------------------------------------

class _AlwaysErrorClient:
    """所有操作都抛 ConnectionError 的假 client，模拟 Redis 完全不可达。"""
    async def lpush(self, *a, **kw): raise redis.exceptions.ConnectionError("down")
    async def brpop(self, *a, **kw): raise redis.exceptions.ConnectionError("down")
    async def hset(self, *a, **kw): raise redis.exceptions.ConnectionError("down")
    async def hget(self, *a, **kw): raise redis.exceptions.ConnectionError("down")
    async def hgetall(self, *a, **kw): raise redis.exceptions.ConnectionError("down")
    async def sadd(self, *a, **kw): raise redis.exceptions.ConnectionError("down")
    async def srem(self, *a, **kw): raise redis.exceptions.ConnectionError("down")
    async def smembers(self, *a, **kw): raise redis.exceptions.ConnectionError("down")
    async def sismember(self, *a, **kw): raise redis.exceptions.ConnectionError("down")
    async def ping(self, *a, **kw): raise redis.exceptions.ConnectionError("down")
    async def aclose(self): pass


def test_submit_raises_enqueue_unavailable_and_marks_failed(tmp_path: Path):
    """submit 时 lpush 抛 ConnectionError → meta status=failed + 抛 EnqueueUnavailable(task_id 对)。

    验证：
    1. EnqueueUnavailable 被抛出
    2. e.task_id 非空且与磁盘 meta.task_id 一致
    3. 磁盘 meta.status == "failed"（不留幽灵 pending）
    """
    from ncds_opus_factory.server.task_runner import EnqueueUnavailable, TaskRunner

    def run_fn(on_progress=None, **kwargs) -> dict[str, Any]:
        return {"ok": True}

    store = TaskStore(tmp_path / "tasks")
    # 注入必定失败的 client
    rq = RedisQueue(client=_AlwaysErrorClient())
    runner = TaskRunner(store, {"shenkuo": run_fn}, redis_queue=rq)
    runner._started = True  # 跳过 runner 未启动告警

    async def run():
        with pytest.raises(EnqueueUnavailable) as exc_info:
            await runner.submit("shenkuo", {"author": "test"})
        e = exc_info.value
        # task_id 非空
        assert e.task_id, "EnqueueUnavailable.task_id 不能为空"
        # 磁盘 meta 已被标 failed（不留幽灵 pending）
        meta = store.get_meta(e.task_id)
        assert meta is not None, "meta 应当已创建"
        assert meta.status == "failed", f"meta.status 应为 failed，实际为 {meta.status!r}"
        # error 字段已写入说明原因
        assert meta.error and "redis" in meta.error.lower(), (
            f"meta.error 应包含 redis 相关说明，实际: {meta.error!r}"
        )

    asyncio.run(run())


def test_post_tasks_returns_503_when_redis_down(tmp_path: Path, monkeypatch):
    """经 TestClient POST /tasks：redis 不可达时返回 503 + body.task_id + status=failed。

    通过 monkeypatch 把 state.RUNNER 替换成注入了必定失败 client 的 runner，
    再 monkeypatch state.RUNNER._rq 为必定失败 client（路由调用的是 RUNNER.submit）。
    """
    from ncds_opus_factory.server.task_runner import EnqueueUnavailable, TaskRunner
    from ncds_opus_factory.server import state as state_mod

    # 替换 RUNNER._rq 为必定失败 client（不替换整个 RUNNER 防止初始化副作用）
    error_rq = RedisQueue(client=_AlwaysErrorClient())
    monkeypatch.setattr(state_mod.RUNNER, "_rq", error_rq)
    # 确保 RUNNER._started=True（跳过告警，但不影响测试核心逻辑）
    monkeypatch.setattr(state_mod.RUNNER, "_started", True)

    from ncds_opus_factory.server.app import app
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/tasks", json={"cmd": "shenkuo", "params": {"author": "u1"}})

    assert resp.status_code == 503, f"期望 503，实际 {resp.status_code}"
    body = resp.json()
    # body 应包含 task_id，让前端可追踪
    assert "task_id" in body and body["task_id"], f"body 缺 task_id: {body}"
    # status 应为 failed
    assert body.get("status") == "failed", f"body.status 应为 failed: {body}"

    # 独立读磁盘验证 meta 已落盘（meta 在 state_mod.STORE 里）
    task_id = body["task_id"]
    meta = state_mod.STORE.get_meta(task_id)
    assert meta is not None, f"磁盘 meta 不存在: {task_id}"
    assert meta.status == "failed", f"磁盘 meta.status 应为 failed，实际 {meta.status!r}"


# ---------------------------------------------------------------------------
# Part B — loop ping-guard：ping False 时 loop 一个 tick 不 create meta、不抛
# ---------------------------------------------------------------------------

def test_subscription_loop_ping_guard_no_submit(tmp_path: Path):
    """ping False 时，subscription_loop 跳过 tick，不调 runner.submit，不产 meta。

    让 loop 跑一个 tick（ping=False → sleep → CancelledError 截断），
    断言 store 里无新 meta。
    """
    from ncds_opus_factory.server.subscriptions import subscription_loop
    from ncds_opus_factory.server.task_runner import TaskRunner

    store = TaskStore(tmp_path / "tasks")
    runner, rq = _make_runner(store, "shenkuo")
    sub_path = tmp_path / "subscriptions.json"

    # 写一个有订阅的配置，以确认真正是 ping-guard 跳过（而非「无订阅空转」）
    sub_path.write_text(
        '{"interval_hours": 0.25, "authors": [{"sec_uid": "test123", "enabled": true}]}',
        encoding="utf-8",
    )

    # mock ping 永远 False
    pinged = {"count": 0}

    async def run():
        import fakeredis.aioredis as fake_aioredis
        from ncds_opus_factory.server.queue import get_default_queue

        fake_rq = RedisQueue(client=_AlwaysErrorClient())

        async def run_one_tick():
            # 让 loop 跑一个 tick，然后在 sleep 阶段 cancel
            task = asyncio.create_task(
                subscription_loop(runner, store, sub_path)
            )
            await asyncio.sleep(0.05)   # 让 loop 启动并到达 sleep
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        with patch(
            "ncds_opus_factory.server.subscriptions.get_default_queue",
            return_value=fake_rq,
        ):
            await run_one_tick()

    asyncio.run(run())

    # 断言 store 里无新 meta（ping false 时不 submit）
    tasks = store.list_tasks()
    assert len(tasks) == 0, (
        f"ping false 时 subscription_loop 不应创建任何 meta，实际 {len(tasks)} 个"
    )


def test_retro_loop_ping_guard_no_submit(tmp_path: Path):
    """ping False 时，retro_loop 跳过 tick，不调 runner.submit，不产 meta。"""
    from ncds_opus_factory.server.retro_trigger import retro_loop
    from ncds_opus_factory.server.task_runner import TaskRunner

    store = TaskStore(tmp_path / "tasks")
    runner, rq = _make_runner(store, "wolong")

    fake_rq = RedisQueue(client=_AlwaysErrorClient())

    async def run():
        task = asyncio.create_task(retro_loop(runner, store))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    with patch(
        "ncds_opus_factory.server.retro_trigger.get_default_queue",
        return_value=fake_rq,
    ):
        asyncio.run(run())

    tasks = store.list_tasks()
    assert len(tasks) == 0, (
        f"ping false 时 retro_loop 不应创建任何 meta，实际 {len(tasks)} 个"
    )


def test_planner_loop_ping_guard_no_submit(tmp_path: Path):
    """ping False 时，planner_loop 跳过 tick，不调 runner.submit，不产 meta。"""
    from ncds_opus_factory.server.planner import planner_loop
    from ncds_opus_factory.server.task_runner import TaskRunner

    store = TaskStore(tmp_path / "tasks")
    runner, rq = _make_runner(store, "shenkuo")

    fake_rq = RedisQueue(client=_AlwaysErrorClient())

    async def run():
        task = asyncio.create_task(planner_loop(runner, store))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    with patch(
        "ncds_opus_factory.server.planner.get_default_queue",
        return_value=fake_rq,
    ):
        asyncio.run(run())

    tasks = store.list_tasks()
    assert len(tasks) == 0, (
        f"ping false 时 planner_loop 不应创建任何 meta，实际 {len(tasks)} 个"
    )


def test_round_reconciler_ping_guard_no_submit(tmp_path: Path, monkeypatch):
    """ping False 时，_round_reconciler 跳过 tick，不调 reconcile_once，不产副作用。"""
    from ncds_opus_factory.server import maintenance as maint_mod
    from ncds_opus_factory.server import rounds_gate

    fake_rq = RedisQueue(client=_AlwaysErrorClient())
    reconcile_calls = []

    async def fake_reconcile():
        reconcile_calls.append(1)
        return 0

    async def run():
        task = asyncio.create_task(maint_mod._round_reconciler())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    with (
        patch("ncds_opus_factory.server.maintenance.get_default_queue", return_value=fake_rq),
        patch("ncds_opus_factory.server.rounds_gate.reconcile_once", side_effect=fake_reconcile),
    ):
        asyncio.run(run())

    assert len(reconcile_calls) == 0, (
        f"ping false 时 _round_reconciler 不应调 reconcile_once，实际调用 {len(reconcile_calls)} 次"
    )


# ---------------------------------------------------------------------------
# Part C — brpop TimeoutError 正常空轮询 vs ConnectionError 真故障
# ---------------------------------------------------------------------------

class _TimeoutErrorClient:
    """所有 brpop 都抛 RedisTimeoutError 的假 client（模拟 socket_timeout 空轮询）。"""
    async def brpop(self, *a, **kw):
        raise redis.exceptions.TimeoutError("socket timeout")
    async def lpush(self, *a, **kw): pass
    async def hset(self, *a, **kw): pass
    async def hget(self, *a, **kw): return None
    async def hgetall(self, *a, **kw): return {}
    async def sadd(self, *a, **kw): pass
    async def srem(self, *a, **kw): pass
    async def smembers(self, *a, **kw): return set()
    async def sismember(self, *a, **kw): return False
    async def ping(self, *a, **kw): return True
    async def aclose(self): pass


def test_brpop_timeout_error_resets_backoff_returns_none():
    """brpop 抛 RedisTimeoutError（正常空轮询）：退避不增大（仍 _BACKOFF_INIT），返回 None，不告警。

    这是 Part C 核心：修复前 TimeoutError 走 ConnectionError 分支 → 退避增大 + WARNING；
    修复后 TimeoutError 单独截获 → 退避重置 + 无 WARNING + 返回 None。
    """
    async def _run():
        q = RedisQueue(client=_TimeoutErrorClient())
        # 初始退避应为 _BACKOFF_INIT
        assert q._brpop_backoff == _BACKOFF_INIT

        # brpop 抛 TimeoutError → 应静默返回 None，退避不增大
        result = await q.brpop("shenkuo", timeout=5)
        assert result is None, f"TimeoutError 时 brpop 应返回 None，实际 {result!r}"
        # 退避应被重置回 _BACKOFF_INIT（不是增大）
        assert q._brpop_backoff == _BACKOFF_INIT, (
            f"TimeoutError 后退避应为 {_BACKOFF_INIT}，实际 {q._brpop_backoff}"
        )

    asyncio.run(_run())


def test_brpop_timeout_error_no_warning(caplog):
    """brpop 抛 RedisTimeoutError 时不应打 WARNING 日志（对比 ConnectionError 会打）。"""
    async def _run():
        q = RedisQueue(client=_TimeoutErrorClient())
        with caplog.at_level(logging.WARNING):
            await q.brpop("shenkuo", timeout=5)

    asyncio.run(_run())
    # 不应有 WARNING 级别日志
    warning_msgs = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warning_msgs) == 0, (
        f"TimeoutError 空轮询不应产生 WARNING，实际日志: {[r.message for r in warning_msgs]}"
    )


def test_brpop_connection_error_escalates_backoff():
    """brpop 抛 ConnectionError（真故障）：退避增大到 _BACKOFF_INIT * 2。

    验证 Part C 的修复没有破坏真故障的退避逻辑。
    """
    async def _run():
        q = RedisQueue(client=_AlwaysErrorClient())
        assert q._brpop_backoff == _BACKOFF_INIT

        result = await q.brpop("shenkuo", timeout=1)
        assert result is None

        # 真 ConnectionError 后退避应增大
        assert q._brpop_backoff == _BACKOFF_INIT * 2, (
            f"ConnectionError 后退避应翻倍为 {_BACKOFF_INIT * 2}，实际 {q._brpop_backoff}"
        )

    asyncio.run(_run())


def test_brpop_timeout_multiple_calls_backoff_stays_init():
    """连续多次 TimeoutError 空轮询：退避始终保持 _BACKOFF_INIT（不累积）。"""
    async def _run():
        q = RedisQueue(client=_TimeoutErrorClient())
        for _ in range(5):
            result = await q.brpop("shenkuo", timeout=5)
            assert result is None
            assert q._brpop_backoff == _BACKOFF_INIT, (
                f"连续 TimeoutError 后退避应恒为 {_BACKOFF_INIT}，实际 {q._brpop_backoff}"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Part A — requeue 入队失败 → EnqueueUnavailable + meta failed
# ---------------------------------------------------------------------------

def test_requeue_raises_enqueue_unavailable_and_marks_failed(tmp_path: Path):
    """requeue 时 lpush 抛 ConnectionError → meta status=failed + 抛 EnqueueUnavailable。

    确保 restore 时 redis 挂也不留幽灵 pending/cancelled 任务。
    """
    from ncds_opus_factory.server.task_runner import EnqueueUnavailable, TaskRunner
    from ncds_opus_factory.server.task_store import TaskStore

    store = TaskStore(tmp_path / "tasks")

    # 先用正常 client 创建一个 cancelled 任务（模拟 restore 的前提）
    ok_fs = FakeServer()
    ok_client = FakeRedis(server=ok_fs)
    ok_rq = RedisQueue(client=ok_client)

    def run_fn(on_progress=None, **kwargs) -> dict[str, Any]:
        return {"ok": True}

    runner_ok = TaskRunner(store, {"shenkuo": run_fn}, redis_queue=ok_rq)
    runner_ok._started = True

    async def setup():
        tid = await runner_ok.submit("shenkuo", {"author": "u"})
        store.update_status(tid, "cancelled")
        return tid

    task_id = asyncio.run(setup())

    # 换掉 _rq 为必定失败的 client
    error_rq = RedisQueue(client=_AlwaysErrorClient())
    runner_ok._rq = error_rq

    async def do_requeue():
        with pytest.raises(EnqueueUnavailable) as exc_info:
            await runner_ok.requeue(task_id, "shenkuo")
        e = exc_info.value
        assert e.task_id == task_id, f"task_id 不匹配: {e.task_id!r} != {task_id!r}"
        meta = store.get_meta(task_id)
        assert meta is not None
        assert meta.status == "failed", f"requeue 失败后 meta.status 应为 failed，实际 {meta.status!r}"

    asyncio.run(do_requeue())
