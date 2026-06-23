"""worker.py 接线单元测试（S3 步5）。

不连真 Redis（conftest autouse fakeredis 注入）。
不真跑 _amain 的无限 await asyncio.Event().wait()。
采用 asyncio.run() 包裹 async 测试，与项目现有风格一致（不引入 pytest-asyncio 依赖）。
"""

from __future__ import annotations

import asyncio
import inspect
import signal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ncds_opus_factory.server import worker
from ncds_opus_factory.server.label_store import LabelStore
from ncds_opus_factory.server.task_store import TaskStore


# ---------------------------------------------------------------------------
# 测试 1：基础 import 与入口可调用性
# ---------------------------------------------------------------------------

def test_worker_imports_and_main_exists():
    """worker 模块可 import，main 可调用，_amain 是 coroutine function。"""
    assert callable(worker.main)
    assert inspect.iscoroutinefunction(worker._amain)


def test_worker_signal_handlers_set_stop_event(monkeypatch):
    """SIGTERM/SIGINT handler 命中 stop_event，供 _amain 退出裸等待。"""
    handlers: dict[signal.Signals, tuple[Any, tuple[Any, ...]]] = {}

    class FakeLoop:
        def add_signal_handler(self, sig, callback, *args):
            handlers[sig] = (callback, args)

    async def run():
        stop_event = asyncio.Event()
        monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())
        installed = worker._install_signal_handlers(stop_event)
        assert installed == [signal.SIGTERM, signal.SIGINT]
        callback, args = handlers[signal.SIGTERM]
        callback(*args)
        assert stop_event.is_set()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 测试 2：_amain 接线——on_terminal 被设置、recover 被调、5 个 loop 被 create_task
# ---------------------------------------------------------------------------

def test_worker_wires_loops(monkeypatch):
    """
    monkeypatch：
    - get_default_queue().ping -> True
    - RUNNER.recover_and_start -> 0
    - asyncio.create_task -> 记录调用次数
    - asyncio.Event().wait -> 立即返回（截断无限等待）
    确认：on_terminal 被设为 rounds_gate.handle_terminal，recover 被调，
    5 个 loop 被 create_task（NOF_* 默认全开）。
    """
    from ncds_opus_factory.server import rounds_gate
    from ncds_opus_factory.server import worker as worker_mod

    # --- 准备 mock_runner ---
    mock_runner = MagicMock()
    mock_runner.on_terminal = None
    mock_runner.recover_and_start = AsyncMock(return_value=0)
    mock_runner.shutdown = AsyncMock(return_value=None)
    mock_runner.list_commands = MagicMock(return_value=["shenkuo"])

    # --- monkeypatch 模块级名称（在 worker 模块命名空间内替换） ---
    monkeypatch.setattr("ncds_opus_factory.server.worker.RUNNER", mock_runner)
    monkeypatch.setattr("ncds_opus_factory.server.worker.STATE_DIR", Path("/tmp/test_state"))

    # --- 记录被 create_task 的协程 ---
    created_tasks: list[Any] = []
    original_create_task = asyncio.create_task

    def capturing_create_task(coro, **kwargs):
        created_tasks.append(coro)
        # 真正 schedule，避免 RuntimeWarning: coroutine never awaited
        return original_create_task(coro, **kwargs)

    # 确保 NOF_* 全部默认开（不设 0）
    monkeypatch.delenv("NOF_SUBSCRIPTIONS", raising=False)
    monkeypatch.delenv("NOF_RETRO", raising=False)
    monkeypatch.delenv("NOF_PLANNER", raising=False)

    async def run():
        # ping 探活 mock（在 async 内 monkeypatch get_default_queue）
        mock_rq = AsyncMock()
        mock_rq.ping = AsyncMock(return_value=True)
        # asyncio.Event 截断无限等待
        mock_event = MagicMock()
        mock_event.wait = AsyncMock(return_value=None)

        with (
            patch("ncds_opus_factory.server.worker.get_default_queue", return_value=mock_rq),
            patch("ncds_opus_factory.server.worker._install_signal_handlers", return_value=[]),
            patch("asyncio.Event", return_value=mock_event),
            patch("asyncio.create_task", side_effect=capturing_create_task),
        ):
            await worker_mod._amain()

    asyncio.run(run())

    # on_terminal 必须被设为 rounds_gate.handle_terminal
    assert mock_runner.on_terminal is rounds_gate.handle_terminal, (
        "on_terminal 没被设成 rounds_gate.handle_terminal"
    )
    # recover_and_start 必须被调
    mock_runner.recover_and_start.assert_awaited_once()
    mock_runner.shutdown.assert_awaited_once()

    # 5 个 loop：_discard_sweeper, _round_reconciler, subscription_loop, retro_loop, planner_loop
    assert len(created_tasks) == 5, (
        f"期望 5 个 create_task，实际 {len(created_tasks)} 个"
    )


# ---------------------------------------------------------------------------
# 测试 3：ping 失败时 fail-fast 退出
# ---------------------------------------------------------------------------

def test_worker_ping_fail_fast(monkeypatch):
    """ping 返回 False 时，_amain 抛 RuntimeError（决策 D：不静默吞任务）。"""
    async def run():
        mock_rq = AsyncMock()
        mock_rq.ping = AsyncMock(return_value=False)
        with patch("ncds_opus_factory.server.worker.get_default_queue", return_value=mock_rq):
            with pytest.raises(RuntimeError, match="Redis ping failed"):
                await worker._amain()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 可选测试 4：maintenance 模块抽取校验
# ---------------------------------------------------------------------------

def test_maintenance_extracted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """maintenance 模块的常量和函数可正常 import，空 STORE 上 backfill_labels_once 返回 0。"""
    from ncds_opus_factory.server import state as state_mod
    from ncds_opus_factory.server.maintenance import (
        CRON_TTL_HOURS,
        DISCARD_TTL_HOURS,
        _DISCARD_SWEEP_INTERVAL_S,
        _ROUND_RECONCILE_INTERVAL_S,
        _discard_sweeper,
        _round_reconciler,
        backfill_labels_once,
        sweep_cron_once,
        sweep_discarded_once,
    )

    monkeypatch.setattr(state_mod, "STORE", TaskStore(tmp_path / "tasks"))
    monkeypatch.setattr(state_mod, "LABELS", LabelStore(tmp_path / "labels"))

    # 常量可访问
    assert isinstance(DISCARD_TTL_HOURS, float)
    assert isinstance(CRON_TTL_HOURS, float)
    assert isinstance(_DISCARD_SWEEP_INTERVAL_S, (int, float))
    assert isinstance(_ROUND_RECONCILE_INTERVAL_S, float)

    # 函数可调用（空 STORE 返回 0，不抛异常）
    # 测试内显式注入空 STORE/LABELS，避免全量测试顺序污染全局 state 单例。
    result = backfill_labels_once()
    assert result == 0

    result_discard = sweep_discarded_once()
    assert result_discard == 0

    result_cron = sweep_cron_once()
    assert result_cron == 0

    # _discard_sweeper / _round_reconciler 是 coroutine function
    assert inspect.iscoroutinefunction(_discard_sweeper)
    assert inspect.iscoroutinefunction(_round_reconciler)
