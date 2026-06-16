"""tests：P4 卧龙预筛——拦截/探索/降级/冷启动/崩溃重入（docs/WOLONG-DESIGN.md §8.3 第 2 条）。

rubric/labels 目录由 tests/conftest.py 的 NOF_STATE_DIR 隔离 fixture 按测试切到 tmp,
这里直接用 rubric_store 的默认解析写入即可。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ncds_opus_factory.common import rubric_store
from ncds_opus_factory.common.round_store import RoundStore
from ncds_opus_factory.commands import prescreen, wolong_rounds

from tests.server.test_rounds import FakeTransport, _stock_topics, _topics


RUBRIC_TEXT = "# Leader 审美备忘录\n\n- 开头三句内必须有具体钩子,铺垫超过两句会被打回。\n"


@pytest.fixture()
def bench(tmp_path: Path) -> str:
    # all_posts.json + 旁边 collected.json(深采条目带 text + 高赞评论):鬼谷子改评论驱动后
    # 卧龙从 collected.json 取评论作选题种子,start_round 需要它存在。
    (tmp_path / "all_posts.json").write_text("[]", encoding="utf-8")
    (tmp_path / "collected.json").write_text(json.dumps({
        "items": [{"aweme_id": "a1", "digg": 100, "text": "提取文案正文",
                   "top_comments": [{"text": "高赞评论一", "digg": 50}]}],
    }, ensure_ascii=False), encoding="utf-8")
    return str(tmp_path / "all_posts.json")


def _seeded_round(tmp_path: Path, bench: str, count: int = 1):
    """开盘 + 鬼谷子完成 + 产线进入 drafting,返回 (rs, tp, rid)。

    选题入库放在 start_round 之后:P5 起库存够会跳过鬼谷子,这里要走鬼谷子路径。
    """
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=count, benchmark_path=bench, avoid="")
    rid, gz = out["round_id"], out["guiguzi_task"]
    tp.results[gz] = {"topics": _topics()}
    _stock_topics()
    rs.append_event(rid, "terminal", gz, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)
    return rs, tp, rid


def _drafts_result() -> dict[str, Any]:
    return {"drafts": [{"model": "m1", "text": "开头平铺直叙……" * 20}]}


def _judge_returning(prediction: str, reason: str = "钩子弱"):
    def fn(prompt: str, timeout: int) -> str:  # noqa: ARG001
        return json.dumps({"prediction": prediction, "reason": reason})
    return fn


# ---------------------------------------------------------------------------
# 冷启动 / 降级:放行
# ---------------------------------------------------------------------------

def test_no_rubric_cold_start_passthrough(tmp_path, bench, monkeypatch):
    """无 rubric:预筛不调判官,产线直接进闸1。"""
    def must_not_call(*a, **k):
        raise AssertionError("无 rubric 时不得调用判官")
    monkeypatch.setattr(prescreen, "JUDGE_FN", must_not_call)

    rs, tp, rid = _seeded_round(tmp_path, bench)
    tid = rs.load(rid)["lines"][0]["task_id"]
    tp.results[tid] = _drafts_result()
    rs.append_event(rid, "terminal", tid, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    line = rs.load(rid)["lines"][0]
    assert line["status"] == "review"
    assert not tp.reviews


def test_degraded_rubric_passthrough(tmp_path, bench, monkeypatch):
    """degraded=true:同冷启动,整体放行(只警告不拦截)。"""
    rubric_store.write_version(RUBRIC_TEXT)
    cur_path = rubric_store.default_rubric_dir() / "current.json"
    cur = json.loads(cur_path.read_text(encoding="utf-8"))
    cur["degraded"] = True
    cur_path.write_text(json.dumps(cur), encoding="utf-8")

    monkeypatch.setattr(prescreen, "JUDGE_FN",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("降级时不得调用判官")))
    rs, tp, rid = _seeded_round(tmp_path, bench)
    tid = rs.load(rid)["lines"][0]["task_id"]
    tp.results[tid] = _drafts_result()
    rs.append_event(rid, "terminal", tid, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)
    assert rs.load(rid)["lines"][0]["status"] == "review"


# ---------------------------------------------------------------------------
# 拦截 / 探索 / fail-open
# ---------------------------------------------------------------------------

def test_intercept_writes_wolong_review_and_reworks(tmp_path, bench, monkeypatch):
    """预测必被拒且非探索:写 reviewer=wolong 的 rejected review + 段内驱动返工。"""
    rubric_store.write_version(RUBRIC_TEXT)
    monkeypatch.setattr(prescreen, "JUDGE_FN", _judge_returning("rejected", "开头铺垫太长"))
    monkeypatch.setattr(prescreen, "is_explore", lambda tid: False)

    rs, tp, rid = _seeded_round(tmp_path, bench)
    tid = rs.load(rid)["lines"][0]["task_id"]
    tp.results[tid] = _drafts_result()
    rs.append_event(rid, "terminal", tid, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    assert tp.reviews == [{"task_id": tid, "decision": "rejected",
                           "note": "开头铺垫太长", "reviewer": "wolong"}]
    line = rs.load(rid)["lines"][0]
    assert line["rework"] == 1
    assert line["status"] == "drafting" and line["task_id"] != tid  # 同段内已派返工稿
    rework = [s for s in tp.submits if s["intent_key"] == "liuyong:0:1"]
    assert rework and "预筛" in rework[0]["params"]["user_requirements"]
    assert line["prescreen_intercepts"] == 1


def test_explore_releases_without_review_and_records(tmp_path, bench, monkeypatch):
    """探索流量:不写任何 review(写了就被归档,探索失效),照常进待验收,line 记预测。"""
    rubric_store.write_version(RUBRIC_TEXT)
    monkeypatch.setattr(prescreen, "JUDGE_FN", _judge_returning("rejected"))
    monkeypatch.setattr(prescreen, "is_explore", lambda tid: True)

    rs, tp, rid = _seeded_round(tmp_path, bench)
    tid = rs.load(rid)["lines"][0]["task_id"]
    tp.results[tid] = _drafts_result()
    rs.append_event(rid, "terminal", tid, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    line = rs.load(rid)["lines"][0]
    assert not tp.reviews
    assert line["status"] == "review"
    assert line["prescreen"]["prediction"] == "rejected"
    assert line["prescreen"]["explore"] is True
    assert line["prescreen"]["task_id"] == tid


def test_judge_failure_fails_open(tmp_path, bench, monkeypatch):
    """判官超时/异常:按放行处理,预筛不能成为产线单点。"""
    rubric_store.write_version(RUBRIC_TEXT)
    def boom(*a, **k):
        raise RuntimeError("scodex timeout")
    monkeypatch.setattr(prescreen, "JUDGE_FN", boom)

    rs, tp, rid = _seeded_round(tmp_path, bench)
    tid = rs.load(rid)["lines"][0]["task_id"]
    tp.results[tid] = _drafts_result()
    rs.append_event(rid, "terminal", tid, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    line = rs.load(rid)["lines"][0]
    assert line["status"] == "review"
    assert line["prescreen"]["prediction"] == "approved"
    assert not tp.reviews


def test_unparseable_judge_output_fails_open():
    assert prescreen._extract_verdict("我觉得不行") is None
    assert prescreen._extract_verdict('{"prediction":"maybe"}') is None
    v = prescreen._extract_verdict('前缀```json\n{"prediction":"rejected","reason":"x"}\n```')
    assert v == {"prediction": "rejected", "reason": "x"}


def test_is_explore_deterministic_and_bounded():
    assert prescreen.is_explore("t_1") == prescreen.is_explore("t_1")
    hits = sum(prescreen.is_explore(f"t_{i}") for i in range(1000))
    assert 50 < hits < 300  # EXPLORE_PCT=15 ± 抽样波动


# ---------------------------------------------------------------------------
# 战报与降级统计
# ---------------------------------------------------------------------------

def test_false_negative_into_report_and_fn_stats(tmp_path, bench, monkeypatch):
    """探索放行后被用户 approved = 假阴性,进 report 并回写 current.json fn_stats。"""
    rubric_store.write_version(RUBRIC_TEXT)
    monkeypatch.setattr(prescreen, "JUDGE_FN", _judge_returning("rejected"))
    monkeypatch.setattr(prescreen, "is_explore", lambda tid: True)

    rs, tp, rid = _seeded_round(tmp_path, bench)
    tid = rs.load(rid)["lines"][0]["task_id"]
    tp.results[tid] = _drafts_result()
    rs.append_event(rid, "terminal", tid, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)
    rs.append_event(rid, "decision", tid, decision="approved")
    result = wolong_rounds.resume_round(rs, tp, rid)

    rep = result["report"]
    assert rep["rubric_version"] == 1
    assert rep["prescreen"] == {"intercepted": 0, "explore": 1, "false_negatives": 1}
    assert rep["reject_rate"] == 0.0 and rep["rework_rate"] == 0.0
    stats = rubric_store.read_current()["fn_stats"]
    assert stats["explore_total"] == 1 and stats["fn_total"] == 1


def test_fn_stats_degrades_over_threshold():
    """窗口内探索 ≥5 且假阴性率 >30% -> degraded=true;active_rubric 随之失效。"""
    rubric_store.write_version(RUBRIC_TEXT)
    for i in range(5):
        rubric_store.update_fn_stats(f"round_{i}", 1, explore=1, fn=1)
    cur = rubric_store.read_current()
    assert cur["degraded"] is True
    assert cur["fn_stats"]["explore_total"] == 5
    assert rubric_store.active_rubric() is None  # 预筛/注入随之放行


def test_fn_stats_version_mismatch_skipped():
    rubric_store.write_version(RUBRIC_TEXT)   # v1
    rubric_store.write_version(RUBRIC_TEXT)   # v2 = current
    assert rubric_store.update_fn_stats("round_x", 1, explore=3, fn=3) is None
    assert rubric_store.read_current()["degraded"] is False


def test_fn_stats_window_and_idempotency():
    rubric_store.write_version(RUBRIC_TEXT)
    for i in range(8):  # 超出窗口的旧轮被挤出
        rubric_store.update_fn_stats(f"round_{i}", 1, explore=1, fn=0)
    stats = rubric_store.read_current()["fn_stats"]
    assert len(stats["rounds"]) == rubric_store.FN_WINDOW
    rubric_store.update_fn_stats("round_7", 1, explore=2, fn=0)  # 重复收盘覆盖同 round
    stats = rubric_store.read_current()["fn_stats"]
    assert len(stats["rounds"]) == rubric_store.FN_WINDOW
    assert [r for r in stats["rounds"] if r["round_id"] == "round_7"][0]["explore"] == 2


# ---------------------------------------------------------------------------
# 崩溃安全与用户竞态
# ---------------------------------------------------------------------------

def test_pending_work_counts_prescreen_pending():
    """段在判定前被杀:prescreen_pending 算积压,对账协程能补投段救活。"""
    from ncds_opus_factory.server import rounds_gate
    r = {"events": [], "intents": {}, "lines": [{"status": "prescreen_pending"}]}
    assert rounds_gate._has_pending_work(r) is True
    r["lines"][0]["status"] = "review"
    assert rounds_gate._has_pending_work(r) is False


def test_segment_killed_before_judge_resumes(tmp_path, bench, monkeypatch):
    """崩溃现场:line 停在 prescreen_pending、事件已消费——重入段重判并推进。"""
    rubric_store.write_version(RUBRIC_TEXT)
    monkeypatch.setattr(prescreen, "JUDGE_FN", _judge_returning("approved"))

    rs, tp, rid = _seeded_round(tmp_path, bench)
    tid = rs.load(rid)["lines"][0]["task_id"]
    tp.results[tid] = _drafts_result()
    with rs.mutate(rid) as r:  # 手工伪造崩溃后的盘面
        r["lines"][0]["status"] = "prescreen_pending"
    wolong_rounds.resume_round(rs, tp, rid)
    assert rs.load(rid)["lines"][0]["status"] == "review"


def test_segment_killed_after_verdict_no_rejudge(tmp_path, bench, monkeypatch):
    """判定已持久化的重入:不重判(防 LLM 漂移),直接按记录行动。"""
    rubric_store.write_version(RUBRIC_TEXT)
    calls = {"n": 0}
    def counting_judge(*a, **k):
        calls["n"] += 1
        return json.dumps({"prediction": "rejected", "reason": "x"})
    monkeypatch.setattr(prescreen, "JUDGE_FN", counting_judge)
    monkeypatch.setattr(prescreen, "is_explore", lambda tid: False)

    rs, tp, rid = _seeded_round(tmp_path, bench)
    tid = rs.load(rid)["lines"][0]["task_id"]
    tp.results[tid] = _drafts_result()
    with rs.mutate(rid) as r:
        r["lines"][0]["status"] = "prescreen_pending"
        r["lines"][0]["prescreen"] = {"task_id": tid, "prediction": "rejected",
                                      "reason": "已记录", "explore": False, "at": "x"}
    wolong_rounds.resume_round(rs, tp, rid)
    assert calls["n"] == 0
    assert tp.reviews and tp.reviews[0]["task_id"] == tid
    assert rs.load(rid)["lines"][0]["rework"] == 1


def test_user_decision_wins_over_prescreen(tmp_path, bench, monkeypatch):
    """用户在预筛窗口抢先验收:预筛让位,不写 review、不动产线。"""
    rubric_store.write_version(RUBRIC_TEXT)
    monkeypatch.setattr(prescreen, "JUDGE_FN", _judge_returning("rejected"))
    monkeypatch.setattr(prescreen, "is_explore", lambda tid: False)

    rs, tp, rid = _seeded_round(tmp_path, bench)
    tid = rs.load(rid)["lines"][0]["task_id"]
    tp.results[tid] = _drafts_result()
    rs.append_event(rid, "terminal", tid, status="completed")
    rs.append_event(rid, "decision", tid, decision="approved")  # 用户先到
    result = wolong_rounds.resume_round(rs, tp, rid)

    assert not tp.reviews
    assert rs.load(rid)["lines"][0]["status"] == "approved"
    assert result["report"]["approved"] == 1


def test_review_409_converges_to_user_decision(tmp_path, bench, monkeypatch):
    """review 写入吃到 409(用户在判定后、写入前抢先验收):预筛跳过转移,
    用户的 decision 事件在下一段消费,产线按用户判定落定——不卡死、不返工。"""
    rubric_store.write_version(RUBRIC_TEXT)
    monkeypatch.setattr(prescreen, "JUDGE_FN", _judge_returning("rejected"))
    monkeypatch.setattr(prescreen, "is_explore", lambda tid: False)

    rs, tp, rid = _seeded_round(tmp_path, bench)
    tid = rs.load(rid)["lines"][0]["task_id"]
    tp.results[tid] = _drafts_result()
    rs.append_event(rid, "terminal", tid, status="completed")

    def conflict_review(task_id, decision, note, reviewer="wolong"):
        # 模拟路由 409:用户已决策。竞态语义:用户 review 已写,decision 事件随之产生
        rs.append_event(rid, "decision", task_id, decision="approved")
        return False
    tp.review = conflict_review
    wolong_rounds.resume_round(rs, tp, rid)   # 本段:判定记录,409 让位
    result = wolong_rounds.resume_round(rs, tp, rid)  # 下一段:消费用户 decision

    line = rs.load(rid)["lines"][0]
    assert line["status"] == "approved" and line["rework"] == 0
    assert result["report"]["approved"] == 1


def test_review_write_failure_keeps_line_pending(tmp_path, bench, monkeypatch):
    """拦截 review 写失败:不动产线,留给下一段/对账协程重试。"""
    rubric_store.write_version(RUBRIC_TEXT)
    monkeypatch.setattr(prescreen, "JUDGE_FN", _judge_returning("rejected"))
    monkeypatch.setattr(prescreen, "is_explore", lambda tid: False)

    rs, tp, rid = _seeded_round(tmp_path, bench)
    tid = rs.load(rid)["lines"][0]["task_id"]
    tp.results[tid] = _drafts_result()
    def failing_review(*a, **k):
        raise RuntimeError("server hiccup")
    tp.review = failing_review
    rs.append_event(rid, "terminal", tid, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    line = rs.load(rid)["lines"][0]
    assert line["status"] == "prescreen_pending"  # 没转移
    assert line["rework"] == 0
