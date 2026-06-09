"""tests：6 个 agent 接进 HTTP API + REST 拆分（/commands 与 /tasks 分离）。

覆盖：
    - COMMAND_REGISTRY 含 6 个 agent
    - GET /commands 列出命令 catalog（含 6 agent + label/group）
    - GET /commands/{cmd}/schema：liuyong 有 topic 必填字段；未知命令 404
    - POST /tasks（body {cmd, params}）-> 201 + Location 头
    - GET /tasks 列出任务实例（不是命令）
    - GET /tasks/{task_id}：完成态带 artifacts（用预置任务，不真跑 scodex/opus）
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

AGENTS = {"guiguzi", "liuyong", "wudaozi", "boya", "shenkuo", "wolong"}


def test_registry_has_agents():
    from ncds_opus_factory.commands import COMMAND_REGISTRY
    assert AGENTS <= set(COMMAND_REGISTRY)


def test_commands_catalog_lists_agents():
    from ncds_opus_factory.server.app import app
    client = TestClient(app)
    resp = client.get("/commands")
    assert resp.status_code == 200
    items = {c["name"]: c for c in resp.json()["commands"]}
    assert AGENTS <= set(items)
    assert items["liuyong"]["group"] == "agent"
    assert items["wst"]["group"] == "primitive"


def test_command_schema_liuyong():
    from ncds_opus_factory.server.app import app
    client = TestClient(app)
    resp = client.get("/commands/liuyong/schema")
    assert resp.status_code == 200
    data = resp.json()
    assert data["group"] == "agent"
    topic = next(f for f in data["fields"] if f["name"] == "topic")
    assert topic["required"] is True


def test_command_schema_unknown_404():
    from ncds_opus_factory.server.app import app
    client = TestClient(app)
    assert client.get("/commands/nope_cmd/schema").status_code == 404


@pytest.fixture()
def seeded_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """临时 state + artifacts 根，reload 依赖链，返回 (client, STORE, root)。"""
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.setenv("NOF_ARTIFACTS_ROOT", str(tmp_path))

    from ncds_opus_factory.server import artifacts as art_mod
    importlib.reload(art_mod)
    from ncds_opus_factory.server import state as state_mod
    importlib.reload(state_mod)
    from ncds_opus_factory.server.routes import commands as commands_mod
    importlib.reload(commands_mod)
    from ncds_opus_factory.server.routes import tasks as tasks_mod
    importlib.reload(tasks_mod)
    from ncds_opus_factory.server.routes import artifacts as art_routes
    importlib.reload(art_routes)
    from ncds_opus_factory.server import app as app_mod
    importlib.reload(app_mod)

    return TestClient(app_mod.app), state_mod.STORE, tmp_path


def test_create_task_returns_201_and_location(seeded_client, monkeypatch):
    """POST /tasks 创建任务 -> 201 + Location；未知命令 404。"""
    client, store, _ = seeded_client
    # 注入一个秒回的 stub 命令，避免真跑下游(scodex/网络)；submit 阶段即返回 201
    from ncds_opus_factory.server import state as state_mod
    monkeypatch.setitem(state_mod.RUNNER.registry, "__stub__", lambda on_progress, **kw: {"ok": True})

    resp = client.post("/tasks", json={"cmd": "__stub__", "params": {}})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert resp.headers["location"] == f"/tasks/{body['task_id']}"

    assert client.post("/tasks", json={"cmd": "nope_cmd", "params": {}}).status_code == 404


def test_list_tasks_returns_instances_not_commands(seeded_client):
    """GET /tasks 列任务实例（带 task_id/cmd/status），不再是命令清单。"""
    client, store, _ = seeded_client
    m1 = store.create("guiguzi", {"benchmark_path": "x"})
    m2 = store.create("liuyong", {"topic": "y"})
    rows = client.get("/tasks").json()
    ids = {r["task_id"] for r in rows}
    assert {m1.task_id, m2.task_id} <= ids
    assert all("status" in r and "cmd" in r for r in rows)


def test_completed_task_exposes_artifacts(seeded_client):
    """预置一个完成态 guiguzi 任务 -> 详情带 artifacts -> 该 URL 可取到文件。"""
    client, store, root = seeded_client
    topics = root / "state" / "benchmark" / "topics" / "topics.json"
    topics.parent.mkdir(parents=True)
    topics.write_text('[{"title":"x"}]', encoding="utf-8")

    meta = store.create("guiguzi", {"benchmark_path": "x.json"})
    store.write_result(meta.task_id, {"out": str(topics), "topics": [], "raw_len": 0})
    store.update_status(meta.task_id, "completed")

    detail = client.get(f"/tasks/{meta.task_id}").json()
    assert detail["status"] == "completed"
    arts = detail["artifacts"]
    assert arts and arts[0]["url"] == "/artifacts/files/state/benchmark/topics/topics.json"

    # 顺着 URL 真能取到产物（移动端读稿/听音/看片的闭环）
    got = client.get(arts[0]["url"])
    assert got.status_code == 200
    assert got.json() == [{"title": "x"}]
