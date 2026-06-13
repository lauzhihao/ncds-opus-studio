"""E1-b1 HTTP 路由测试：把引擎 driver API 暴露为 /instances，端到端走 FastAPI TestClient。

hermetic：把生产引擎单例的 base_dir 指到 tmp、注入 fake 配方 + stub 派发（不真跑命令）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ncds_opus_factory.server.engine.types import Recipe, RecipeStep


def _fake_ok(on_progress, **params):
    on_progress("done")
    return {"ok": True, "echo": params}


# in(直通) → work(performer) → gate(decision_only) → edit(无 performer 的 content_edit) → out(直通)
_FAKE_RECIPE = Recipe(
    recipe_id="rt",
    name="route test",
    steps=[
        RecipeStep(step_id="in", kind="input"),
        RecipeStep(step_id="work", cmd="stub_ok", deps=["in"]),
        RecipeStep(step_id="gate", cmd="stub_ok", deps=["work"], intervention="decision_only"),
        RecipeStep(step_id="edit", deps=["gate"], intervention="content_edit"),
        RecipeStep(step_id="out", kind="output", deps=["edit"]),
    ],
)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from ncds_opus_factory.server.app import app
    from ncds_opus_factory.server.routes import instances as inst_mod

    inst_dir = tmp_path / "instances"
    inst_dir.mkdir(parents=True, exist_ok=True)
    # 直接 patch **路由模块**引用的那对单例：routes/instances 从不被 reload，引用稳定，
    # 不受其它测试 reload state（致 state.INSTANCE_RUNNER 换新对象）影响。
    # INSTANCE_STORE 与 INSTANCE_RUNNER.store 是同一对象，改 base_dir 即隔离到 tmp。
    monkeypatch.setattr(inst_mod.INSTANCE_STORE, "base_dir", inst_dir)
    monkeypatch.setattr(inst_mod.INSTANCE_RUNNER, "recipes",
                        {**inst_mod.INSTANCE_RUNNER.recipes, "rt": _FAKE_RECIPE})
    monkeypatch.setattr(inst_mod.INSTANCE_RUNNER, "registry",
                        {**inst_mod.INSTANCE_RUNNER.registry, "stub_ok": _fake_ok})
    return TestClient(app)


def _create(client: TestClient, **body) -> str:
    body.setdefault("recipe_id", "rt")
    resp = client.post("/instances", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["instance_id"]


# --------------------------------------------------------------------------- #
# create / get / list
# --------------------------------------------------------------------------- #
def test_create_returns_201_location_and_get(client: TestClient):
    resp = client.post("/instances", json={"recipe_id": "rt", "inputs": {"u": 1}, "title": "T"})
    assert resp.status_code == 201
    body = resp.json()
    iid = body["instance_id"]
    assert iid.startswith("i_") and body["status"] == "pending"
    assert resp.headers["location"] == f"/instances/{iid}"

    got = client.get(f"/instances/{iid}")
    assert got.status_code == 200
    state = got.json()
    assert state["meta"]["recipe_id"] == "rt"
    assert state["inputs"] == {"u": 1}
    assert set(state["steps"]) == {"in", "work", "gate", "edit", "out"}
    assert all(s["status"] == "idle" for s in state["steps"].values())


def test_create_unknown_recipe_404(client: TestClient):
    assert client.post("/instances", json={"recipe_id": "nope"}).status_code == 404


def test_get_missing_instance_404(client: TestClient):
    assert client.get("/instances/i_nope").status_code == 404


def test_list_with_filters(client: TestClient):
    a = _create(client, owner_id="u1")
    b = _create(client, owner_id="u2")
    all_ids = {m["instance_id"] for m in client.get("/instances").json()}
    assert {a, b} <= all_ids
    # owner 过滤（store 层）
    u1 = client.get("/instances", params={"owner_id": "u1"}).json()
    assert [m["instance_id"] for m in u1] == [a]
    # recipe 过滤（route 层）
    assert all(m["recipe_id"] == "rt" for m in client.get("/instances", params={"recipe_id": "rt"}).json())
    # status 过滤
    assert {m["instance_id"] for m in client.get("/instances", params={"status": "pending"}).json()} >= {a, b}


# --------------------------------------------------------------------------- #
# runnable / run
# --------------------------------------------------------------------------- #
def test_runnable_and_run_follow_deps(client: TestClient):
    iid = _create(client)
    assert client.get(f"/instances/{iid}/runnable").json()["runnable"] == ["in"]
    assert client.post(f"/instances/{iid}/steps/in/run", json={}).json()["status"] == "done"
    assert client.get(f"/instances/{iid}/runnable").json()["runnable"] == ["work"]
    r = client.post(f"/instances/{iid}/steps/work/run", json={"step_inputs": {"x": 9}})
    assert r.status_code == 200
    st = r.json()
    assert st["status"] == "done" and st["outputs"] == {"ok": True, "echo": {"x": 9}}
    # 实例被推到 running
    assert client.get(f"/instances/{iid}").json()["meta"]["status"] == "running"


def test_run_config_persists_not_into_performer(client: TestClient):
    iid = _create(client)
    st = client.post(f"/instances/{iid}/steps/work/run",
                     json={"step_inputs": {"x": 1}, "config": {"model": "gpt"}}).json()
    assert st["config"] == {"model": "gpt"}        # 溯源
    assert st["outputs"]["echo"] == {"x": 1}       # config 不进 performer


def test_run_nonrunnable_returns_409(client: TestClient):
    iid = _create(client)
    client.post(f"/instances/{iid}/steps/work/run", json={})         # → done
    r = client.post(f"/instances/{iid}/steps/work/run", json={})     # done→running 非法
    assert r.status_code == 409


def test_run_missing_instance_or_step_404(client: TestClient):
    iid = _create(client)
    assert client.post(f"/instances/{iid}/steps/ghost/run", json={}).status_code == 404
    assert client.post("/instances/i_nope/steps/work/run", json={}).status_code == 404


def test_run_without_body_uses_defaults(client: TestClient):
    # body 全可选；无 body 的 POST（如直通步）应按缺省跑，不该 422
    iid = _create(client)
    r = client.post(f"/instances/{iid}/steps/in/run")
    assert r.status_code == 200 and r.json()["status"] == "done"


def test_run_failed_step_returns_200_with_failed_status(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    # 引擎把 performer 异常收成 step.failed（不冒泡炸 driver）→ HTTP 仍 200，body.status=failed
    from ncds_opus_factory.server.routes import instances as inst_mod

    def _boom(on_progress, **p):
        raise RuntimeError("nope")

    monkeypatch.setitem(inst_mod.INSTANCE_RUNNER.registry, "stub_ok", _boom)
    iid = _create(client)
    r = client.post(f"/instances/{iid}/steps/work/run", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed" and body["error"]


# --------------------------------------------------------------------------- #
# approve / 闸门
# --------------------------------------------------------------------------- #
def test_decision_only_gate_approve(client: TestClient):
    iid = _create(client)
    gate = client.post(f"/instances/{iid}/steps/gate/run", json={}).json()
    assert gate["status"] == "awaiting_review"
    done = client.post(f"/instances/{iid}/steps/gate/approve",
                       json={"decision": "approved", "note": "ok"}).json()
    assert done["status"] == "done"
    assert done["review"]["decision"] == "approved" and done["review"]["note"] == "ok"


def test_performerless_content_edit_gate_and_edit(client: TestClient):
    iid = _create(client)
    edit = client.post(f"/instances/{iid}/steps/edit/run",
                       json={"step_inputs": {"beats": [1]}}).json()
    assert edit["status"] == "awaiting_review"     # 无 performer 也停闸门
    assert edit["draft"] == {"beats": [1]}
    done = client.post(f"/instances/{iid}/steps/edit/approve",
                       json={"decision": "approved", "edited_draft": {"beats": [9]}}).json()
    assert done["status"] == "done"
    assert done["outputs"] == {"beats": [9]} and done["draft_source"] == "user"


def test_approve_non_awaiting_returns_409(client: TestClient):
    iid = _create(client)
    r = client.post(f"/instances/{iid}/steps/work/approve", json={"decision": "approved"})
    assert r.status_code == 409


def test_approve_unknown_step_404(client: TestClient):
    # 不在 recipe 里的 step（含潜在穿越 id）经 approve 的 recipe 预检 → 404，不裸奔进 store
    iid = _create(client)
    r = client.post(f"/instances/{iid}/steps/ghost/approve", json={"decision": "approved"})
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# reset / finalize
# --------------------------------------------------------------------------- #
def test_reset_cascades_downstream(client: TestClient):
    iid = _create(client)
    client.post(f"/instances/{iid}/steps/in/run", json={})
    client.post(f"/instances/{iid}/steps/work/run", json={})
    reset = client.post(f"/instances/{iid}/steps/work/reset").json()["reset"]
    assert reset == ["work", "gate", "edit", "out"]    # work + 全部传递下游，in 不动
    assert client.get(f"/instances/{iid}").json()["steps"]["in"]["status"] == "done"
    assert client.get(f"/instances/{iid}").json()["steps"]["work"]["status"] == "idle"


def test_reset_missing_step_404(client: TestClient):
    iid = _create(client)
    assert client.post(f"/instances/{iid}/steps/ghost/reset").status_code == 404


def test_finalize_completed(client: TestClient):
    iid = _create(client)
    client.post(f"/instances/{iid}/steps/in/run", json={})
    client.post(f"/instances/{iid}/steps/work/run", json={})
    client.post(f"/instances/{iid}/steps/gate/run", json={})
    client.post(f"/instances/{iid}/steps/gate/approve", json={"decision": "approved"})
    client.post(f"/instances/{iid}/steps/edit/run", json={})
    client.post(f"/instances/{iid}/steps/edit/approve", json={"decision": "approved"})
    client.post(f"/instances/{iid}/steps/out/run", json={})
    meta = client.post(f"/instances/{iid}/finalize").json()
    assert meta["status"] == "completed"


def test_finalize_missing_404(client: TestClient):
    assert client.post("/instances/i_nope/finalize").status_code == 404


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #
def test_sse_missing_instance_404(client: TestClient):
    with client.stream("GET", "/instances/i_nope/events") as r:
        assert r.status_code == 404


# SSE 是无终止流，仓库惯例不用 client 测流式（/jobs、/tasks 的 SSE 同样未做流测）。
# 改测路由依赖的两个可断言机制：level 解析 + 按 level 订阅 runner.bus 的事件中继。
# 首条 snapshot 内容 = get_state 序列化，已由 GET /instances/{id} 覆盖。
def test_parse_levels():
    from ncds_opus_factory.server.routes.instances import _parse_levels
    assert _parse_levels(None) == {"meta", "step"}        # 默认决策级
    assert _parse_levels("meta,step,detail") == {"meta", "step", "detail"}
    assert _parse_levels(" meta , step ") == {"meta", "step"}


def test_sse_bus_relays_level_filtered_events(client: TestClient):
    import asyncio

    from ncds_opus_factory.server.routes import instances as inst_mod

    runner = inst_mod.INSTANCE_RUNNER
    iid = _create(client)

    async def _drive():
        q = runner.bus.subscribe(iid, {"meta", "step"})    # route 默认订阅级
        await runner.run_step(iid, "work", {"x": 1})
        evs = []
        while not q.empty():
            evs.append(q.get_nowait())
        runner.bus.unsubscribe(iid, q)
        return evs

    evs = asyncio.run(_drive())
    assert any(e.level == "step" and e.payload.get("status") == "done" for e in evs)
    assert all(e.level != "detail" for e in evs)   # detail 不进总线（b1 取舍）
