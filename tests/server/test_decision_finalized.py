"""tests：P5 改判入口——GET /tasks/{id} 的 decision_finalized 定案判定。

口径(§8.4 已裁定):
    - 无 round 的任务恒 false;
    - round 文件不存在 -> false(别因脏 round_id 误锁改判);
    - round active 且 decision 未被续跑段消费 -> false(消费前改判合法,§4.3);
    - decision 已 consumed -> true(定案,改判只影响案卷不回卷流程);
    - round 终局(done/terminated) -> true(append_event 一律拒收,等同定案)。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def gate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.setenv("NOF_ARTIFACTS_ROOT", str(tmp_path))
    monkeypatch.setenv("NOF_MOCK_AGENTS", "all")

    from ncds_opus_factory.server import artifacts as art_mod
    importlib.reload(art_mod)
    from ncds_opus_factory.server import state as state_mod
    importlib.reload(state_mod)
    from ncds_opus_factory.server import rounds_gate as gate_mod
    importlib.reload(gate_mod)
    from ncds_opus_factory.server.routes import tasks as tasks_mod
    importlib.reload(tasks_mod)
    from ncds_opus_factory.server.routes import rounds as rounds_routes_mod
    importlib.reload(rounds_routes_mod)
    from ncds_opus_factory.server.routes import subscriptions as subs_routes_mod
    importlib.reload(subs_routes_mod)
    from ncds_opus_factory.server import app as app_mod
    importlib.reload(app_mod)

    return TestClient(app_mod.app), state_mod, gate_mod


def _detail(client, task_id: str) -> dict:
    resp = client.get(f"/tasks/{task_id}")
    assert resp.status_code == 200
    return resp.json()


def test_task_without_round_is_never_finalized(gate_env):
    client, state, _ = gate_env
    meta = state.STORE.create("liuyong", {"topic": "x"})
    assert _detail(client, meta.task_id)["decision_finalized"] is False


def test_missing_round_file_is_not_finalized(gate_env):
    """round_id 指向不存在的文件:不锁改判(load 返回 None -> false)。"""
    client, state, _ = gate_env
    meta = state.STORE.create("liuyong", {"topic": "x"},
                              source="wolong", round_id="round_df_missing")
    assert _detail(client, meta.task_id)["decision_finalized"] is False


def test_dirty_round_id_does_not_500(gate_env):
    """round_id 含非法字符(POST /tasks 自报字段):按"不可读"口径 false,详情不能 500。"""
    client, state, _ = gate_env
    meta = state.STORE.create("liuyong", {"topic": "x"},
                              source="wolong", round_id="a/b 脏id")
    assert _detail(client, meta.task_id)["decision_finalized"] is False


def test_active_round_unconsumed_decision_not_finalized(gate_env):
    """active round + decision 未消费:消费前改判合法,不算定案。"""
    client, state, gate = gate_env
    gate.ROUNDS.create("round_df1", {"count": 1})
    meta = state.STORE.create("liuyong", {"topic": "x"},
                              source="wolong", round_id="round_df1")
    # 走真实闸门接线:user 决策 -> decision 事件(consumed=False)
    resp = client.post(f"/tasks/{meta.task_id}/review",
                       json={"decision": "rejected", "note": "钩子弱"})
    assert resp.status_code == 200
    r = gate.ROUNDS.load("round_df1")
    assert any(e["kind"] == "decision" and not e.get("consumed") for e in r["events"])
    assert _detail(client, meta.task_id)["decision_finalized"] is False


def test_consumed_decision_is_finalized(gate_env):
    client, state, gate = gate_env
    gate.ROUNDS.create("round_df2", {"count": 1})
    meta = state.STORE.create("liuyong", {"topic": "x"},
                              source="wolong", round_id="round_df2")
    other = state.STORE.create("liuyong", {"topic": "y"},
                               source="wolong", round_id="round_df2")
    gate.ROUNDS.append_event("round_df2", "decision", meta.task_id, decision="approved")
    with gate.ROUNDS.mutate("round_df2") as r:
        for e in r["events"]:
            e["consumed"] = True
    assert _detail(client, meta.task_id)["decision_finalized"] is True
    # 同 round 内没有 decision 事件的任务不连坐
    assert _detail(client, other.task_id)["decision_finalized"] is False


@pytest.mark.parametrize("final_status", ["done", "terminated"])
def test_finished_round_is_finalized(gate_env, final_status: str):
    """round 终局后 append_event 一律拒收,改判不可能回卷 -> 等同定案。"""
    client, state, gate = gate_env
    rid = f"round_df_{final_status}"
    gate.ROUNDS.create(rid, {"count": 1})
    meta = state.STORE.create("liuyong", {"topic": "x"}, source="wolong", round_id=rid)
    with gate.ROUNDS.mutate(rid) as r:
        r["status"] = final_status
    assert _detail(client, meta.task_id)["decision_finalized"] is True
