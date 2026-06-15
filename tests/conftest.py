"""测试全局隔离。

NOF_STATE_DIR 自动指到 tmp：rubric_store/round_store 等 common 层按 env 解析
默认目录,不隔离的话,开发机上 P4 复盘产出的真实 state/wolong/rubric 会让单测
意外激活预筛(真调 scodex)。需要自定 state 的测试照常用 monkeypatch 覆盖。
"""

from __future__ import annotations

import pytest
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis

from ncds_opus_factory.server import queue as _queue_module
from ncds_opus_factory.server.queue import RedisQueue


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "_isolated_state" / "tasks"))
    monkeypatch.delenv("NOF_LABELS_DIR", raising=False)
    monkeypatch.delenv("NOF_RETRO_FAKE_LLM", raising=False)
    monkeypatch.delenv("NOF_PRESCREEN_FAKE_JUDGE", raising=False)
    # 排产协程不随 server 集成测试的 app startup 拉起:它的补货路径经
    # discover_benchmark() 读仓库根 state/benchmark(有意越过 NOF_STATE_DIR 隔离),
    # 开发机一旦有真实对标数据,TestClient 里的 tick 会派真 cron 任务污染测试 store。
    # planner 行为由 tests/server/test_planner.py 直接驱动 planner_tick 覆盖。
    monkeypatch.setenv("NOF_PLANNER", "0")


@pytest.fixture(autouse=True)
def _fake_redis_queue(monkeypatch):
    """每个 test 注入独立 FakeServer：配额按 test 隔离，绝不连真 Redis。

    实现方式：
    - 每个 test 创建新 FakeServer（内存隔离）
    - 把模块级单例 _default_queue 直接替换成 fakeredis RedisQueue，
      这样所有 TaskRunner(...) 不传 redis_queue 时 get_default_queue() 拿到的也是 fakeredis，
      不会连真 Redis（包括 test_scheduler 里直接 new TaskRunner 的路径）
    - 同时注入 state.RUNNER._rq（集成测试经 app RUNNER 的路径）
    - 不改变 asyncio.Queue 等队列机制（步3 的事）
    """
    # 每 test 一个新 FakeServer = 配额数据互不干扰
    fake_server = FakeServer()
    fake_client = FakeRedis(server=fake_server)
    fake_rq = RedisQueue(client=fake_client)

    # 替换模块级单例为 fakeredis 实例：
    # 任何 get_default_queue() 调用都返回此 fake_rq（不连真 Redis）
    monkeypatch.setattr(_queue_module, "_default_queue", fake_rq)

    # 同时注入 state.RUNNER（集成测试路径经 app 的 RUNNER 跑 _run）
    try:
        from ncds_opus_factory.server import state as _state_module
        monkeypatch.setattr(_state_module.RUNNER, "_rq", fake_rq)
    except Exception:
        pass  # 若 state 未 import（纯单元测试）不影响

    return fake_rq
