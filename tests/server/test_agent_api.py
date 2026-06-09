"""tests：6 个 agent 接进 HTTP API 后的注册 / schema / 产物详情。

覆盖：
    - COMMAND_REGISTRY 含 6 个 agent
    - GET /tasks 列出它们
    - GET /tasks/{cmd}/schema：liuyong 有 topic 必填字段；未知命令 404
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


def test_schema_endpoint_liuyong():
    from ncds_opus_factory.server.app import app
    client = TestClient(app)
    resp = client.get("/tasks/liuyong/schema")
    assert resp.status_code == 200
    data = resp.json()
    assert data["group"] == "agent"
    topic = next(f for f in data["fields"] if f["name"] == "topic")
    assert topic["required"] is True


def test_schema_endpoint_unknown_404():
    from ncds_opus_factory.server.app import app
    client = TestClient(app)
    assert client.get("/tasks/nope_cmd/schema").status_code == 404


def test_tasks_list_includes_agents():
    from ncds_opus_factory.server.app import app
    client = TestClient(app)
    cmds = set(client.get("/tasks").json()["commands"])
    assert AGENTS <= cmds


@pytest.fixture()
def seeded_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """临时 state + artifacts 根，reload 依赖链，返回 (client, STORE, root)。"""
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.setenv("NOF_ARTIFACTS_ROOT", str(tmp_path))

    from ncds_opus_factory.server import artifacts as art_mod
    importlib.reload(art_mod)
    from ncds_opus_factory.server import state as state_mod
    importlib.reload(state_mod)
    from ncds_opus_factory.server.routes import tasks as tasks_mod
    importlib.reload(tasks_mod)
    from ncds_opus_factory.server.routes import artifacts as art_routes
    importlib.reload(art_routes)
    from ncds_opus_factory.server import app as app_mod
    importlib.reload(app_mod)

    return TestClient(app_mod.app), state_mod.STORE, tmp_path


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
