"""tests：P3 卧龙分段编排——round 状态机、派单/续跑段、事件接线、对账。"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ncds_opus_factory.common.round_store import RoundStore
from ncds_opus_factory.commands import wolong_rounds


class FakeTransport:
    """测试传输:记录派发,task_id 可预测;结果由测试预置。"""

    def __init__(self):
        self.submits: list[dict[str, Any]] = []
        self.results: dict[str, dict[str, Any]] = {}
        self.reviews: list[dict[str, Any]] = []
        self._n = 0

    def submit(self, cmd, params, source, round_id, intent_key):
        # 幂等:同 intent_key 返回同 id(模拟调度器查重)
        for s in self.submits:
            if s["intent_key"] == intent_key and s["round_id"] == round_id:
                return s["task_id"]
        self._n += 1
        tid = f"t_{1000000000000 + self._n}_{'%08x' % self._n}"
        self.submits.append({"cmd": cmd, "params": params, "source": source,
                             "round_id": round_id, "intent_key": intent_key, "task_id": tid})
        return tid

    def read_result(self, task_id):
        return self.results.get(task_id)

    def review(self, task_id, decision, note, reviewer="wolong"):
        self.reviews.append({"task_id": task_id, "decision": decision,
                             "note": note, "reviewer": reviewer})
        return True


@pytest.fixture()
def bench(tmp_path: Path) -> str:
    p = tmp_path / "all_posts.json"
    p.write_text("[]", encoding="utf-8")
    return str(p)


def _topics(n=5):
    return [{"title": f"选题{i}", "potential": 90 - i} for i in range(n)]


# ---------------------------------------------------------------------------
# RoundStore
# ---------------------------------------------------------------------------

def test_round_store_event_dedup_and_finality(tmp_path: Path):
    rs = RoundStore(tmp_path / "rounds")
    rs.create("round_x", {"count": 1})

    assert rs.append_event("round_x", "terminal", "t_a", status="completed") is True
    assert rs.append_event("round_x", "terminal", "t_a", status="completed") is False  # 去重

    assert rs.append_event("round_x", "decision", "t_b", decision="approved") is True
    # 消费前改判:覆盖
    assert rs.append_event("round_x", "decision", "t_b", decision="rejected", note="重来") is True
    r = rs.load("round_x")
    ev = [e for e in r["events"] if e["kind"] == "decision"][0]
    assert ev["decision"] == "rejected"
    # 消费后改判:定案,忽略(§4.3)
    with rs.mutate("round_x") as r:
        for e in r["events"]:
            e["consumed"] = True
    assert rs.append_event("round_x", "decision", "t_b", decision="approved") is False


def test_round_store_rejects_bad_round_id(tmp_path: Path):
    rs = RoundStore(tmp_path / "rounds")
    with pytest.raises(ValueError):
        rs.path("../pwn")


# ---------------------------------------------------------------------------
# 派单段 / 续跑段
# ---------------------------------------------------------------------------

def test_start_round_dispatches_guiguzi(tmp_path: Path, bench: str):
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=2, benchmark_path=bench, avoid="旧题")

    assert out["stage"] == "topics"
    assert len(tp.submits) == 1
    s = tp.submits[0]
    assert s["cmd"] == "guiguzi" and s["source"] == "wolong" and s["intent_key"] == "guiguzi:0"
    assert s["params"]["avoid"] == ["旧题"]
    r = rs.load(out["round_id"])
    assert r["intents"]["guiguzi:0"]["task_id"] == s["task_id"]


def test_start_round_without_benchmark_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(wolong_rounds, "discover_benchmark", lambda: None)
    with pytest.raises(FileNotFoundError):
        wolong_rounds.start_round(RoundStore(tmp_path / "r"), FakeTransport(),
                                  count=1, benchmark_path="", avoid="")


def test_resume_opens_lines_on_topics_done(tmp_path: Path, bench: str):
    """鬼谷子完成 -> 按 potential 挑 top N 开产线,派柳永。"""
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=2, benchmark_path=bench, avoid="")
    rid, gz = out["round_id"], out["guiguzi_task"]

    tp.results[gz] = {"topics": _topics()}
    rs.append_event(rid, "terminal", gz, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    r = rs.load(rid)
    assert r["stage"] == "scripts" and len(r["lines"]) == 2
    liu = [s for s in tp.submits if s["cmd"] == "liuyong"]
    assert len(liu) == 2
    assert liu[0]["params"]["topic"] == "选题0"  # potential 最高的在前
    assert all(line["status"] == "drafting" and line["task_id"] for line in r["lines"])


def test_resume_rework_then_kill(tmp_path: Path, bench: str):
    """打回带意见 -> 返工(带【打回意见】);超过 MAX_REWORK -> 止损。"""
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=1, benchmark_path=bench, avoid="")
    rid, gz = out["round_id"], out["guiguzi_task"]
    tp.results[gz] = {"topics": _topics()}
    rs.append_event(rid, "terminal", gz, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    for i in range(wolong_rounds.MAX_REWORK):
        tid = rs.load(rid)["lines"][0]["task_id"]
        rs.append_event(rid, "decision", tid, decision="rejected", note=f"第{i}次不行")
        wolong_rounds.resume_round(rs, tp, rid)
        line = rs.load(rid)["lines"][0]
        assert line["rework"] == i + 1 and line["status"] == "drafting"
        rework = [s for s in tp.submits if s["intent_key"] == f"liuyong:0:{i + 1}"]
        assert rework and f"第{i}次不行" in rework[0]["params"]["user_requirements"]

    # 第三次打回:止损 + 全产线落定 -> 战报
    tid = rs.load(rid)["lines"][0]["task_id"]
    rs.append_event(rid, "decision", tid, decision="rejected", note="还是不行")
    result = wolong_rounds.resume_round(rs, tp, rid)
    r = rs.load(rid)
    assert r["lines"][0]["status"] == "killed"
    assert r["status"] == "done" and r["report"]["approved"] == 0
    assert result["report"]["killed"] == 1


def test_resume_all_approved_makes_report(tmp_path: Path, bench: str):
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=2, benchmark_path=bench, avoid="")
    rid, gz = out["round_id"], out["guiguzi_task"]
    tp.results[gz] = {"topics": _topics()}
    rs.append_event(rid, "terminal", gz, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    for line in rs.load(rid)["lines"]:
        rs.append_event(rid, "terminal", line["task_id"], status="completed")
        rs.append_event(rid, "decision", line["task_id"], decision="approved")
    result = wolong_rounds.resume_round(rs, tp, rid)

    r = rs.load(rid)
    assert r["status"] == "done"
    assert result["report"]["approved"] == 2 and result["count"] == 2
    assert "收盘" in result["task_title"]


def test_resume_guiguzi_failure_retries_then_terminates(tmp_path: Path, bench: str):
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=1, benchmark_path=bench, avoid="")
    rid, gz = out["round_id"], out["guiguzi_task"]

    rs.append_event(rid, "terminal", gz, status="failed")
    wolong_rounds.resume_round(rs, tp, rid)
    retry = [s for s in tp.submits if s["intent_key"] == "guiguzi:1"]
    assert len(retry) == 1

    rs.append_event(rid, "terminal", retry[0]["task_id"], status="failed")
    wolong_rounds.resume_round(rs, tp, rid)
    assert rs.load(rid)["status"] == "terminated"


def test_resume_redispatches_orphan_intents(tmp_path: Path, bench: str):
    """上一段崩在派发前(intent 有、task_id 无):下一段补派,幂等不重复。"""
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=1, benchmark_path=bench, avoid="")
    rid = out["round_id"]
    with rs.mutate(rid) as r:
        r["intents"]["guiguzi:9"] = {"cmd": "guiguzi", "params": {"benchmark_path": bench},
                                     "task_id": None, "at": "x"}
    wolong_rounds.resume_round(rs, tp, rid)
    assert rs.load(rid)["intents"]["guiguzi:9"]["task_id"] is not None


# ---------------------------------------------------------------------------
# 事件接线 + 对账(server 侧)
# ---------------------------------------------------------------------------

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


def test_user_decision_triggers_resume(gate_env):
    """闸门信号:round 内任务被人工打回 -> round 记事件 + 投出续跑段(gate)。"""
    client, state, gate = gate_env
    gate.ROUNDS.create("round_g1", {"count": 1})
    meta = state.STORE.create("liuyong", {"topic": "x"}, source="wolong", round_id="round_g1")

    resp = client.post(f"/tasks/{meta.task_id}/review",
                       json={"decision": "rejected", "note": "钩子弱"})
    assert resp.status_code == 200

    r = gate.ROUNDS.load("round_g1")
    assert any(e["kind"] == "decision" and e["task_id"] == meta.task_id for e in r["events"])
    gates = [m for m in state.STORE.list_tasks() if m.source == "gate" and m.round_id == "round_g1"]
    assert len(gates) == 1 and gates[0].cmd == "wolong"


def test_system_review_is_not_gate_signal(gate_env):
    """system 自动归档不是闸门信号:不进 round 事件。"""
    client, state, gate = gate_env
    gate.ROUNDS.create("round_g2", {"count": 1})
    meta = state.STORE.create("guiguzi", {}, source="wolong", round_id="round_g2")

    client.post(f"/tasks/{meta.task_id}/review",
                json={"decision": "approved", "reviewer": "system"})
    r = gate.ROUNDS.load("round_g2")
    assert not r["events"]


def test_resume_merge_no_duplicate_gate(gate_env):
    """已有在途续跑段时,新事件只记账不再投段(§4.4 合并)。"""
    client, state, gate = gate_env
    gate.ROUNDS.create("round_g3", {"count": 1})
    m1 = state.STORE.create("liuyong", {"topic": "a"}, source="wolong", round_id="round_g3")
    m2 = state.STORE.create("liuyong", {"topic": "b"}, source="wolong", round_id="round_g3")

    client.post(f"/tasks/{m1.task_id}/review", json={"decision": "approved"})
    client.post(f"/tasks/{m2.task_id}/review", json={"decision": "approved"})

    gates = [m for m in state.STORE.list_tasks() if m.source == "gate" and m.round_id == "round_g3"]
    assert len(gates) == 1
    r = gate.ROUNDS.load("round_g3")
    assert sum(1 for e in r["events"] if e["kind"] == "decision") == 2


def test_cancel_round_task_emits_event(gate_env):
    client, state, gate = gate_env
    gate.ROUNDS.create("round_g4", {"count": 1})
    meta = state.STORE.create("liuyong", {"topic": "x"}, source="wolong", round_id="round_g4")

    client.post(f"/tasks/{meta.task_id}/cancel")
    r = gate.ROUNDS.load("round_g4")
    assert any(e["kind"] == "terminal" and e["status"] == "cancelled" for e in r["events"])


def test_reconcile_resubmits_lost_resume(gate_env):
    """对账:有未消费事件但无在途续跑段 -> 补投。"""
    client, state, gate = gate_env
    gate.ROUNDS.create("round_g5", {"count": 1})
    gate.ROUNDS.append_event("round_g5", "terminal", "t_lost", status="completed")

    n = asyncio.run(gate.reconcile_once())
    assert n == 1
    gates = [m for m in state.STORE.list_tasks() if m.source == "gate" and m.round_id == "round_g5"]
    assert len(gates) == 1


def test_reconcile_times_out_stale_round(gate_env):
    client, state, gate = gate_env
    gate.ROUNDS.create("round_g6", {"count": 1})
    # mutate 会刷新 updated_at,伪造旧时间要直接写文件
    p = gate.ROUNDS.path("round_g6")
    r = json.loads(p.read_text(encoding="utf-8"))
    r["updated_at"] = "2020-01-01T00:00:00"
    p.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")

    asyncio.run(gate.reconcile_once())
    r = gate.ROUNDS.load("round_g6")
    assert r["status"] == "terminated" and "收尾" in r["report"]["reason"]


def test_restore_denied_for_wolong(gate_env):
    client, state, _ = gate_env
    meta = state.STORE.create("wolong", {"round_id": "r", "resume": True},
                              source="gate", round_id="r")
    state.STORE.update_status(meta.task_id, "cancelled")
    resp = client.post(f"/tasks/{meta.task_id}/restore")
    assert resp.status_code == 409
    assert "卧龙" in resp.json()["detail"]


def test_deferred_event_survives_backfill_window(tmp_path: Path):
    """blocker 回归:崩在派发后回填前,事件先到——暂缓不消费,回填后下一圈接上。"""
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    rs.create("round_w", {"count": 1})
    # 模拟 S1 崩溃现场:意向已登记未回填,产线 pending_intent 悬空
    with rs.mutate("round_w") as r:
        r["stage"] = "scripts"
        r["intents"]["liuyong:0:0"] = {"cmd": "liuyong", "params": {"topic": "题"},
                                       "task_id": None, "at": "x"}
        r["lines"].append({"slot": 0, "topic": {"title": "题"}, "task_id": None,
                           "status": "dispatching", "rework": 0, "history": [],
                           "notes": [], "pending_intent": "liuyong:0:0"})
    # 任务实际已被派出(t_X)且已完成,terminal 事件先于回填到达
    tp.submits.append({"cmd": "liuyong", "params": {"topic": "题"}, "source": "wolong",
                       "round_id": "round_w", "intent_key": "liuyong:0:0",
                       "task_id": "t_X"})
    rs.append_event("round_w", "terminal", "t_X", status="completed")

    wolong_rounds.resume_round(rs, tp, "round_w")

    r = rs.load("round_w")
    line = r["lines"][0]
    assert line["task_id"] == "t_X" and line["status"] == "review"  # 事件没丢
    assert all(e["consumed"] for e in r["events"])


def test_decision_before_terminal_no_rollback(tmp_path: Path, bench: str):
    """乱序守卫:approved 之后到达的 terminal completed 不能把产线回退成 review。"""
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=2, benchmark_path=bench, avoid="")
    rid, gz = out["round_id"], out["guiguzi_task"]
    tp.results[gz] = {"topics": _topics()}
    rs.append_event(rid, "terminal", gz, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    t0 = rs.load(rid)["lines"][0]["task_id"]
    rs.append_event(rid, "decision", t0, decision="approved")  # 决策先到(round 因产线1 未落定保持 active)
    wolong_rounds.resume_round(rs, tp, rid)
    rs.append_event(rid, "terminal", t0, status="completed")   # terminal 后到
    wolong_rounds.resume_round(rs, tp, rid)
    assert rs.load(rid)["lines"][0]["status"] == "approved"  # 没被回退成 review

    # 产线1 正常走完 -> 收盘
    t1 = rs.load(rid)["lines"][1]["task_id"]
    rs.append_event(rid, "terminal", t1, status="completed")
    rs.append_event(rid, "decision", t1, decision="approved")
    result = wolong_rounds.resume_round(rs, tp, rid)
    assert rs.load(rid)["status"] == "done" and result["report"]["approved"] == 2


def test_dispatch_task_id_makes_round_deterministic(tmp_path: Path, bench: str):
    """blocker 回归:派单段重跑(同任务 id)收敛到同一 round,不双倍生产。"""
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out1 = wolong_rounds.start_round(rs, tp, count=1, benchmark_path=bench,
                                     avoid="", dispatch_task_id="t_123_abc")
    assert out1["round_id"] == "round_t_123_abc"
    # 重启重跑同一派单任务
    wolong_rounds.start_round(rs, tp, count=1, benchmark_path=bench,
                              avoid="", dispatch_task_id="t_123_abc")
    rounds_on_disk = list((tmp_path / "rounds").glob("round_*.json"))
    assert len(rounds_on_disk) == 1
    guiguzi_submits = [s for s in tp.submits if s["cmd"] == "guiguzi"]
    assert len(guiguzi_submits) == 1  # intent 幂等,没有第二个鬼谷子


def test_garbage_topics_terminates_not_zombie(tmp_path: Path, bench: str):
    """鬼谷子产出畸形(字符串数组/空 title)->止损,不留 48h 僵尸。"""
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=1, benchmark_path=bench, avoid="")
    rid, gz = out["round_id"], out["guiguzi_task"]
    tp.results[gz] = {"topics": ["裸字符串", {"potential": 9}, {"title": "  "}]}
    rs.append_event(rid, "terminal", gz, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)
    assert rs.load(rid)["status"] == "terminated"


def test_rework_carries_all_notes(tmp_path: Path, bench: str):
    """返工要求全量带历史打回意见,不只最后一条。"""
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=1, benchmark_path=bench, avoid="")
    rid, gz = out["round_id"], out["guiguzi_task"]
    tp.results[gz] = {"topics": _topics()}
    rs.append_event(rid, "terminal", gz, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    for note in ("钩子弱", "结尾拖"):
        tid = rs.load(rid)["lines"][0]["task_id"]
        rs.append_event(rid, "decision", tid, decision="rejected", note=note)
        wolong_rounds.resume_round(rs, tp, rid)
    last = [s for s in tp.submits if s["cmd"] == "liuyong"][-1]
    assert "钩子弱" in last["params"]["user_requirements"]
    assert "结尾拖" in last["params"]["user_requirements"]


def test_cancel_wolong_segment_terminates_round(gate_env):
    """取消卧龙段=终止本轮:在途干活任务被清场取消。"""
    client, state, gate = gate_env
    gate.ROUNDS.create("round_k1", {"count": 1})
    seg = state.STORE.create("wolong", {"round_id": "round_k1", "resume": True},
                             source="gate", round_id="round_k1")
    worker = state.STORE.create("liuyong", {"topic": "x"}, source="wolong", round_id="round_k1")

    client.post(f"/tasks/{seg.task_id}/cancel")

    assert gate.ROUNDS.load("round_k1")["status"] == "terminated"
    assert state.STORE.get_meta(worker.task_id).status == "cancelled"


def test_terminate_endpoint_cleans_up(gate_env):
    client, state, gate = gate_env
    gate.ROUNDS.create("round_k2", {"count": 1})
    worker = state.STORE.create("liuyong", {"topic": "x"}, source="wolong", round_id="round_k2")
    done = state.STORE.create("liuyong", {"topic": "y"}, source="wolong", round_id="round_k2")
    state.STORE.update_status(done.task_id, "completed")

    resp = client.post("/rounds/round_k2/terminate")
    assert resp.status_code == 200

    assert gate.ROUNDS.load("round_k2")["status"] == "terminated"
    assert state.STORE.get_meta(worker.task_id).status == "cancelled"
    # 终态孤儿补 system 归档,不留永久红灯
    review = state.STORE.get_review(done.task_id)
    assert review is not None and review.reviewer == "system"


def test_reconcile_syncs_lost_terminal(gate_env):
    """store↔round 对账:任务已终态而 round 没记 terminal(崩溃窗口)->补账+续跑。"""
    client, state, gate = gate_env
    gate.ROUNDS.create("round_k3", {"count": 1})
    t = state.STORE.create("guiguzi", {}, source="wolong", round_id="round_k3")
    state.STORE.update_status(t.task_id, "completed")
    with gate.ROUNDS.mutate("round_k3") as r:
        r["intents"]["guiguzi:0"] = {"cmd": "guiguzi", "params": {}, "task_id": t.task_id, "at": "x"}

    n = asyncio.run(gate.reconcile_once())
    assert n >= 1
    r = gate.ROUNDS.load("round_k3")
    assert any(e["kind"] == "terminal" and e["task_id"] == t.task_id for e in r["events"])


def test_gate_quota_exhausted_no_garbage_tasks(gate_env, monkeypatch):
    """gate 配额耗尽:续跑静默暂缓,不铸 failed 垃圾任务。"""
    from ncds_opus_factory.server import task_runner as tr
    client, state, gate = gate_env
    monkeypatch.setitem(tr.DAILY_QUOTA, "_gate", 0)
    gate.ROUNDS.create("round_k4", {"count": 1})
    gate.ROUNDS.append_event("round_k4", "terminal", "t_z", status="completed")

    assert asyncio.run(gate.maybe_resume("round_k4")) is False
    assert all(m.round_id != "round_k4" for m in state.STORE.list_tasks())


def test_intent_key_idempotent_submit(gate_env):
    """调度器派发幂等:同 (round_id, intent_key) 第二次提交返回既有任务。"""
    client, state, _ = gate_env
    body = {"cmd": "liuyong", "params": {"topic": "x"}, "source": "wolong",
            "round_id": "round_g7", "intent_key": "liuyong:0:0"}
    t1 = client.post("/tasks", json=body).json()["task_id"]
    t2 = client.post("/tasks", json=body).json()["task_id"]
    assert t1 == t2
    mine = [m for m in state.STORE.list_tasks() if m.round_id == "round_g7"]
    assert len(mine) == 1
