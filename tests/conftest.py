"""测试全局隔离。

NOF_STATE_DIR 自动指到 tmp：rubric_store/round_store 等 common 层按 env 解析
默认目录,不隔离的话,开发机上 P4 复盘产出的真实 state/wolong/rubric 会让单测
意外激活预筛(真调 scodex)。需要自定 state 的测试照常用 monkeypatch 覆盖。
"""

from __future__ import annotations

import pytest


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
