"""tests：案卷库 + 溯源字段（docs/WOLONG-DESIGN.md P1）。

覆盖：
    - POST /tasks/{id}/review 同步落案卷（decision/note/reviewer/note_origin）
    - 改判 -> revised=true；撤销(DELETE review) -> 案卷保留 revoked=true；再决策复位
    - 清扫协程删任务目录前补写案卷；案卷在,任务目录亡
    - POST /tasks 透传 source/parent_task_id/round_id -> meta 落盘 + 列表读回
    - 旧式请求（不带新字段）行为不变，meta.json 不出现新键（exclude_none）
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """临时 state 根，reload 依赖链，返回 (client, state_mod, app_mod)。"""
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.setenv("NOF_ARTIFACTS_ROOT", str(tmp_path))
    # 必须先于 state reload：registry 在 import 期定型，本文件会真发 POST /tasks，
    # 不 mock 的话后台 asyncio 任务会真调 guiguzi/scodex
    monkeypatch.setenv("NOF_MOCK_AGENTS", "all")

    from ncds_opus_factory.server import artifacts as art_mod
    importlib.reload(art_mod)
    from ncds_opus_factory.server import state as state_mod
    importlib.reload(state_mod)
    from ncds_opus_factory.server.routes import tasks as tasks_mod
    importlib.reload(tasks_mod)
    from ncds_opus_factory.server import app as app_mod
    importlib.reload(app_mod)

    return TestClient(app_mod.app), state_mod, app_mod


def test_review_writes_label(env):
    """打回 -> 案卷落盘：decision/note/reviewer 缺省 user/note_origin 推定 user。"""
    client, state, _ = env
    meta = state.STORE.create("liuyong", {"topic": "钱去哪了"})
    state.STORE.write_result(meta.task_id, {"drafts": [{"model": "m1", "text": "开头三句话……"}]})

    resp = client.post(
        f"/tasks/{meta.task_id}/review",
        json={"decision": "rejected", "note": "开头太绕"},
    )
    assert resp.status_code == 200
    assert resp.json()["reviewer"] == "user"

    label = state.LABELS.get(meta.task_id)
    assert label is not None
    assert label["decision"] == "rejected"
    assert label["note"] == "开头太绕"
    assert label["reviewer"] == "user"
    assert label["note_origin"] == "user"
    assert label["revised"] is False
    assert label["revoked"] is False
    assert "开头三句话" in label["artifact_digest"]
    assert label["params_digest"] == {"topic": "钱去哪了"}


def test_relabel_marks_revised(env):
    """改判（decision 变化）-> revised=true。"""
    client, state, _ = env
    meta = state.STORE.create("guiguzi", {"benchmark_path": "x"})

    client.post(f"/tasks/{meta.task_id}/review", json={"decision": "approved"})
    client.post(f"/tasks/{meta.task_id}/review", json={"decision": "rejected", "note": "撞题"})

    label = state.LABELS.get(meta.task_id)
    assert label["decision"] == "rejected"
    assert label["revised"] is True


def test_revoke_keeps_label_marked(env):
    """撤销决策 -> review.json 删除,案卷保留且 revoked=true；再决策复位 revoked。"""
    client, state, _ = env
    meta = state.STORE.create("liuyong", {"topic": "x"})

    client.post(f"/tasks/{meta.task_id}/review", json={"decision": "approved"})
    client.delete(f"/tasks/{meta.task_id}/review")

    assert state.STORE.get_review(meta.task_id) is None
    label = state.LABELS.get(meta.task_id)
    assert label is not None and label["revoked"] is True

    client.post(f"/tasks/{meta.task_id}/review", json={"decision": "rejected"})
    assert state.LABELS.get(meta.task_id)["revoked"] is False


def test_machine_note_origin_passthrough(env):
    """客户端模板备注显式标 machine（柳永「采用 X 稿」），不冒充人工语料。"""
    client, state, _ = env
    meta = state.STORE.create("liuyong", {"topic": "x"})

    client.post(
        f"/tasks/{meta.task_id}/review",
        json={"decision": "approved", "note": "采用 m1 稿", "note_origin": "machine"},
    )
    assert state.LABELS.get(meta.task_id)["note_origin"] == "machine"


def test_sweep_archives_label_before_rmtree(env):
    """清扫超期弃用件：任务目录删除前补写案卷,标签存活。"""
    client, state, app_mod = env
    from ncds_opus_factory.server.schemas import Review

    old = state.STORE.create("shenkuo", {"author": "MS4w_old"})
    fresh = state.STORE.create("shenkuo", {"author": "MS4w_new"})
    stale_ts = (datetime.now() - timedelta(hours=200)).isoformat()
    # 直接落 review.json（绕过路由,模拟历史数据:有决策、无案卷）
    state.STORE.write_review(old.task_id, Review(decision="rejected", reviewed_at=stale_ts))
    state.STORE.write_review(
        fresh.task_id, Review(decision="rejected", reviewed_at=datetime.now().isoformat())
    )

    removed = app_mod.sweep_discarded_once()

    assert removed == 1
    assert not state.STORE.task_dir(old.task_id).exists()
    label = state.LABELS.get(old.task_id)
    assert label is not None and label["decision"] == "rejected" and label["cmd"] == "shenkuo"
    # 未超期的不动
    assert state.STORE.task_dir(fresh.task_id).exists()


def test_create_task_carries_provenance(env):
    """POST /tasks 透传溯源三件套 -> meta.json 落盘 + GET /tasks 读回。"""
    client, state, _ = env
    parent = state.STORE.create("wolong", {}, round_id="round_001")

    resp = client.post(
        "/tasks",
        json={
            "cmd": "guiguzi",
            "params": {"benchmark_path": "x"},
            "source": "wolong",
            "parent_task_id": parent.task_id,
            "round_id": "round_001",
        },
    )
    assert resp.status_code == 201
    task_id = resp.json()["task_id"]

    on_disk = json.loads(state.STORE.meta_path(task_id).read_text(encoding="utf-8"))
    assert on_disk["source"] == "wolong"
    assert on_disk["parent_task_id"] == parent.task_id
    assert on_disk["round_id"] == "round_001"

    row = next(r for r in client.get("/tasks").json() if r["task_id"] == task_id)
    assert row["source"] == "wolong"
    assert row["round_id"] == "round_001"


def test_legacy_request_unchanged(env):
    """旧式请求不带新字段：201 正常,meta.json 不出现 source 等键（exclude_none）。"""
    client, state, _ = env
    resp = client.post("/tasks", json={"cmd": "guiguzi", "params": {"benchmark_path": "x"}})
    assert resp.status_code == 201
    on_disk = json.loads(
        state.STORE.meta_path(resp.json()["task_id"]).read_text(encoding="utf-8")
    )
    assert "source" not in on_disk
    assert "parent_task_id" not in on_disk
    assert "round_id" not in on_disk


def test_invalid_source_rejected(env):
    """source 不在白名单 -> 422（Literal 校验）。"""
    client, _, _ = env
    resp = client.post(
        "/tasks", json={"cmd": "guiguzi", "params": {}, "source": "alien"}
    )
    assert resp.status_code == 422


def test_sweep_skips_delete_when_label_write_fails(env, monkeypatch):
    """核心不变量：案卷写不成 -> 跳过删除，任务目录保留（标签不能丢）。"""
    client, state, app_mod = env
    from ncds_opus_factory.server.schemas import Review

    old = state.STORE.create("shenkuo", {"author": "MS4w_x"})
    stale_ts = (datetime.now() - timedelta(hours=200)).isoformat()
    state.STORE.write_review(old.task_id, Review(decision="rejected", reviewed_at=stale_ts))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(state.LABELS, "write", boom)
    removed = app_mod.sweep_discarded_once()

    assert removed == 0
    assert state.STORE.task_dir(old.task_id).exists()


def test_review_succeeds_when_label_write_fails(env, monkeypatch):
    """落卷失败不挡验收：review 仍 200 且 review.json 已写（对账轮稍后补卷）。"""
    client, state, _ = env
    meta = state.STORE.create("liuyong", {"topic": "x"})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(state.LABELS, "write", boom)
    resp = client.post(f"/tasks/{meta.task_id}/review", json={"decision": "approved"})

    assert resp.status_code == 200
    assert state.STORE.get_review(meta.task_id).decision == "approved"


def test_backfill_fills_missing_labels(env):
    """对账轮：有决策无案卷（存量/落卷失败）-> 幂等补写。"""
    client, state, app_mod = env
    from ncds_opus_factory.server.schemas import Review

    meta = state.STORE.create("liuyong", {"topic": "存量"})
    # 直接落 review.json（模拟 P1 上线前的存量决策,无案卷）
    state.STORE.write_review(
        meta.task_id, Review(decision="approved", reviewed_at=datetime.now().isoformat())
    )
    assert state.LABELS.get(meta.task_id) is None

    assert app_mod.backfill_labels_once() == 1
    assert state.LABELS.get(meta.task_id)["decision"] == "approved"
    # 幂等：第二轮不重复写
    assert app_mod.backfill_labels_once() == 0


def test_label_degrades_without_result(env):
    """result.json 缺失（failed/cancelled 任务被打 decision）-> artifact_digest 为 None,params 仍在。"""
    client, state, _ = env
    meta = state.STORE.create("liuyong", {"topic": "x"})
    client.post(f"/tasks/{meta.task_id}/review", json={"decision": "rejected"})

    label = state.LABELS.get(meta.task_id)
    assert label["artifact_digest"] is None
    assert label["params_digest"] == {"topic": "x"}


def test_reviewer_firewall_passthrough(env):
    """防火墙字段：reviewer=wolong 的带 note 决策,note_origin 推定 inferred 而非 user。"""
    client, state, _ = env
    meta = state.STORE.create("liuyong", {"topic": "x"})

    client.post(
        f"/tasks/{meta.task_id}/review",
        json={"decision": "rejected", "note": "预测钩子弱", "reviewer": "wolong"},
    )
    label = state.LABELS.get(meta.task_id)
    assert label["reviewer"] == "wolong"
    assert label["note_origin"] == "inferred"

    client.post(
        f"/tasks/{meta.task_id}/review",
        json={"decision": "approved", "reviewer": "system"},
    )
    label = state.LABELS.get(meta.task_id)
    assert label["reviewer"] == "system"
    assert label["note_origin"] is None


def test_revised_is_sticky(env):
    """revised 粘滞：改判过一次后,后续同向覆盖不抹掉改判史。"""
    client, state, _ = env
    meta = state.STORE.create("guiguzi", {"benchmark_path": "x"})

    client.post(f"/tasks/{meta.task_id}/review", json={"decision": "approved"})
    client.post(f"/tasks/{meta.task_id}/review", json={"decision": "rejected"})
    client.post(f"/tasks/{meta.task_id}/review", json={"decision": "rejected", "note": "补理由"})

    assert state.LABELS.get(meta.task_id)["revised"] is True


def test_revoke_self_heals(env, monkeypatch):
    """撤销打标失败后重试自愈：第二次 DELETE 虽 removed=False,案卷仍被补标 revoked。"""
    client, state, _ = env
    meta = state.STORE.create("liuyong", {"topic": "x"})
    client.post(f"/tasks/{meta.task_id}/review", json={"decision": "approved"})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(state.LABELS, "mark_revoked", boom)
    client.delete(f"/tasks/{meta.task_id}/review")
    monkeypatch.undo()
    assert state.LABELS.get(meta.task_id)["revoked"] is False  # 首次失败

    client.delete(f"/tasks/{meta.task_id}/review")  # removed=False 的重试
    assert state.LABELS.get(meta.task_id)["revoked"] is True


def test_review_traversal_blocked(env, tmp_path):
    """非法 task_id（路径穿越）-> 404,labels 目录外不产生文件。"""
    client, state, _ = env
    resp = client.post("/tasks/..%2F..%2Fpwn/review", json={"decision": "approved"})
    assert resp.status_code == 404
    assert not (tmp_path / "state" / "wolong" / "labels" / ".." / "pwn.json").resolve().exists()


def test_detail_carries_provenance(env):
    """GET /tasks/{id} 详情同样带溯源三件套（详情角标/round 重试策略要读）。"""
    client, state, _ = env
    resp = client.post(
        "/tasks",
        json={"cmd": "guiguzi", "params": {}, "source": "cron", "round_id": "r1"},
    )
    task_id = resp.json()["task_id"]
    detail = client.get(f"/tasks/{task_id}").json()
    assert detail["source"] == "cron"
    assert detail["round_id"] == "r1"
