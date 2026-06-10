"""tests：任务人工决策端点（移动端点同意/拒绝 + 备注）。

覆盖：
    - POST /tasks/{id}/review 写入决策 -> 200 + Review；任务不存在 -> 404
    - GET /tasks/{id} 详情带回 review；未决时 review 为 None
    - 重复提交即改判（覆盖 review.json）
    - GET /tasks 列表回填 decision（待验收收件箱用），未决为 None
    - review.json 与 meta.json 并列落盘，不污染 meta（meta 里不出现 decision）
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def seeded_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """临时 state 根，reload 依赖链，返回 (client, STORE)。"""
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.setenv("NOF_ARTIFACTS_ROOT", str(tmp_path))

    from ncds_opus_factory.server import artifacts as art_mod
    importlib.reload(art_mod)
    from ncds_opus_factory.server import state as state_mod
    importlib.reload(state_mod)
    from ncds_opus_factory.server.routes import tasks as tasks_mod
    importlib.reload(tasks_mod)
    from ncds_opus_factory.server import app as app_mod
    importlib.reload(app_mod)

    return TestClient(app_mod.app), state_mod.STORE


def test_review_unknown_task_404(seeded_client):
    client, _ = seeded_client
    resp = client.post("/tasks/t_does_not_exist/review", json={"decision": "approved"})
    assert resp.status_code == 404


def test_review_approve_then_detail_carries_it(seeded_client):
    """点同意 + 备注 -> 200 Review；详情带回同一决策。"""
    client, store = seeded_client
    meta = store.create("wolong", {"count": 3})

    resp = client.post(
        f"/tasks/{meta.task_id}/review",
        json={"decision": "approved", "note": "配音再快点"},
    )
    assert resp.status_code == 200
    review = resp.json()
    assert review["decision"] == "approved"
    assert review["note"] == "配音再快点"
    assert review["reviewed_at"]

    detail = client.get(f"/tasks/{meta.task_id}").json()
    assert detail["review"]["decision"] == "approved"
    assert detail["review"]["note"] == "配音再快点"


def test_review_defaults_none(seeded_client):
    """未决任务：详情 review 为 None，列表 decision 为 None。"""
    client, store = seeded_client
    meta = store.create("liuyong", {"topic": "x"})

    detail = client.get(f"/tasks/{meta.task_id}").json()
    assert detail["review"] is None

    row = next(r for r in client.get("/tasks").json() if r["task_id"] == meta.task_id)
    assert row.get("decision") is None


def test_review_is_idempotent_overwrite(seeded_client):
    """重复提交即改判：approved -> rejected 覆盖。"""
    client, store = seeded_client
    meta = store.create("guiguzi", {"benchmark_path": "x"})

    client.post(f"/tasks/{meta.task_id}/review", json={"decision": "approved"})
    client.post(
        f"/tasks/{meta.task_id}/review",
        json={"decision": "rejected", "note": "重做"},
    )

    detail = client.get(f"/tasks/{meta.task_id}").json()
    assert detail["review"]["decision"] == "rejected"
    assert detail["review"]["note"] == "重做"


def test_list_tasks_backfills_decision(seeded_client):
    """GET /tasks 给已决任务回填 decision，便于待验收收件箱筛选。"""
    client, store = seeded_client
    approved = store.create("wolong", {"count": 1})
    pending = store.create("wolong", {"count": 1})
    client.post(f"/tasks/{approved.task_id}/review", json={"decision": "approved"})

    rows = {r["task_id"]: r for r in client.get("/tasks").json()}
    assert rows[approved.task_id]["decision"] == "approved"
    assert rows[pending.task_id].get("decision") is None


def test_review_lands_in_own_file_not_meta(seeded_client):
    """决策落 review.json，meta.json 不出现 decision（决策真源单一）。"""
    client, store = seeded_client
    meta = store.create("boya", {"job_dir": "x"})
    client.post(f"/tasks/{meta.task_id}/review", json={"decision": "approved"})

    review_path = store.review_path(meta.task_id)
    assert review_path.exists()
    assert json.loads(review_path.read_text(encoding="utf-8"))["decision"] == "approved"

    meta_on_disk = json.loads(store.meta_path(meta.task_id).read_text(encoding="utf-8"))
    assert "decision" not in meta_on_disk
