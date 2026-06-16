"""tests：P5 选题库 v2（common/topic_store）+ 消费方改造（§8.4 第 2 条）。

库文件路径按 NOF_STATE_DIR 父目录解析,tests/conftest.py 的 autouse fixture
已把它隔离到 tmp,这里直接用默认解析读写即可。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ncds_opus_factory.common import topic_store
from ncds_opus_factory.common.round_store import RoundStore
from ncds_opus_factory.commands import guiguzi, wolong_rounds
from ncds_opus_factory.server.mock_agents import mock_guiguzi

from tests.server.test_rounds import FakeTransport, _topics


@pytest.fixture()
def bench(tmp_path: Path) -> str:
    # all_posts.json + 旁边 collected.json(深采条目带 text + 高赞评论):
    # 鬼谷子改评论驱动后卧龙从 collected.json 取评论作选题种子,fixture 要备齐。
    (tmp_path / "all_posts.json").write_text("[]", encoding="utf-8")
    (tmp_path / "collected.json").write_text(json.dumps({
        "items": [{"aweme_id": "a1", "digg": 100, "text": "提取文案正文",
                   "top_comments": [{"text": "高赞评论一", "digg": 50},
                                    {"text": "高赞评论二", "digg": 30}]}],
    }, ensure_ascii=False), encoding="utf-8")
    return str(tmp_path / "all_posts.json")


def _age_topic(title: str, days: int) -> None:
    """把库内某条选题的 created_at 拨旧(直接改文件,模拟时间流逝)。"""
    path = topic_store.default_topics_path()
    doc = json.loads(path.read_text(encoding="utf-8"))
    for t in doc["topics"]:
        if t["title"] == title:
            t["created_at"] = (datetime.now() - timedelta(days=days)).isoformat()
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# 旧格式迁移
# ---------------------------------------------------------------------------

def test_legacy_list_migration():
    """旧裸 list 自动迁移:topic_id 补算/status 按 consumed_by 推断/created_at
    取迁移时刻/potential 容 float/source 缺省 legacy(自带的保留)。"""
    path = topic_store.default_topics_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = [
        {"title": "老题A", "motif": "m", "source": "对标X", "why": "w", "potential": 8.5},
        {"title": "老题B", "potential": "7", "consumed_by": "round_old"},
        {"title": "老题C"},
        "裸字符串",
        {"title": "  "},
    ]
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    topics = {t["title"]: t for t in topic_store.load()["topics"]}
    assert set(topics) == {"老题A", "老题B", "老题C"}
    a = topics["老题A"]
    assert a["topic_id"] == topic_store.topic_id_for("老题A")
    assert a["status"] == "fresh" and a["potential"] == 8.5
    assert a["source"] == "对标X"  # 自带 source(对标来源标题)缺省语义下保留
    assert a["created_at"]
    b = topics["老题B"]
    assert b["status"] == "consumed" and b["consumed_by"] == "round_old"
    assert b["potential"] == 7.0
    assert topics["老题C"]["source"] == "legacy"

    # 读不写盘;下一次写盘把迁移持久化成 v2 dict
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), list)
    topic_store.merge([], source="guiguzi")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["version"] == 2 and len(doc["topics"]) == 3


def test_real_legacy_sample_migrates():
    """仓库里真实的旧格式样本(state/mock-demo/guiguzi/topics.json)能整体迁移。"""
    sample = Path(__file__).resolve().parents[2] / "state" / "mock-demo" / "guiguzi" / "topics.json"
    if not sample.is_file():
        pytest.skip("旧格式样本不存在")
    path = topic_store.default_topics_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
    topics = topic_store.load()["topics"]
    assert len(topics) == 8
    assert all(t["topic_id"] and t["status"] == "fresh" for t in topics)
    assert topic_store.fresh()[0]["potential"] == 9  # potential 降序


# ---------------------------------------------------------------------------
# merge / consume / active_titles / 过期 / 容错
# ---------------------------------------------------------------------------

def test_merge_dedups_by_title():
    assert topic_store.merge(_topics(3), source="guiguzi") == {"added": 3, "skipped": 0}
    # 同 title 重复 + 一条新题
    again = _topics(3) + [{"title": "新题X", "potential": 50}]
    assert topic_store.merge(again, source="guiguzi") == {"added": 1, "skipped": 3}
    lib = topic_store.load()["topics"]
    assert len(lib) == 4
    t0 = [t for t in lib if t["title"] == "选题0"][0]
    assert t0["status"] == "fresh" and t0["source"] == "guiguzi"
    assert t0["topic_id"] == topic_store.topic_id_for("选题0")


def test_merge_moves_bench_source_aside():
    """条目自带 source(鬼谷子 schema 的对标来源标题)挪到 bench_source,
    库的 source 字段只放溯源。"""
    topic_store.merge([{"title": "题S", "source": "对标爆款标题", "potential": 9}],
                      source="guiguzi")
    t = topic_store.load()["topics"][0]
    assert t["source"] == "guiguzi" and t["bench_source"] == "对标爆款标题"


def test_fresh_sorted_and_limited():
    topic_store.merge(_topics(5), source="guiguzi")
    top2 = topic_store.fresh(limit=2)
    assert [t["title"] for t in top2] == ["选题0", "选题1"]
    assert topic_store.count_fresh() == 5


def test_consume_marks_and_does_not_steal():
    topic_store.merge(_topics(3), source="guiguzi")
    ids = [t["topic_id"] for t in topic_store.fresh(limit=2)]
    assert topic_store.consume(ids, "round_a") == 2
    lib = {t["title"]: t for t in topic_store.load()["topics"]}
    assert lib["选题0"]["status"] == "consumed" and lib["选题0"]["consumed_by"] == "round_a"
    assert topic_store.count_fresh() == 1
    # 已 consumed 不被后来者抢占
    assert topic_store.consume(ids, "round_b") == 0
    assert topic_store.load()["topics"][0]["consumed_by"] == "round_a"


def test_take_atomic_and_reclaims_same_round():
    """take:单锁挑+标;round 落盘前崩溃的重放按 consumed_by 原样捡回预占题。"""
    topic_store.merge([{"title": f"选题{i}", "potential": 9 - i} for i in range(5)],
                      source="guiguzi")
    first = topic_store.take(3, "round_crash")
    assert [t["title"] for t in first] == ["选题0", "选题1", "选题2"]
    assert topic_store.count_fresh() == 2
    # 重放(round 文件没落盘):回收同一批,不吃剩余 fresh
    replay = topic_store.take(3, "round_crash")
    assert [t["title"] for t in replay] == [t["title"] for t in first]
    assert topic_store.count_fresh() == 2
    # 其他 round 拿不到已预占的题,只分到剩余
    other = topic_store.take(3, "round_other")
    assert [t["title"] for t in other] == ["选题3", "选题4"]
    assert topic_store.count_fresh() == 0


def test_count_available_includes_own_claims():
    """可用库存 = fresh + 本 round 预占:开盘重放不误走鬼谷子路径。"""
    topic_store.merge([{"title": f"选题{i}", "potential": 9 - i} for i in range(4)],
                      source="guiguzi")
    topic_store.take(3, "round_mine")
    assert topic_store.count_fresh() == 1
    assert topic_store.count_available("round_mine") == 4
    assert topic_store.count_available("round_other") == 1


def test_lazy_expiration_and_active_titles():
    topic_store.merge(_topics(3), source="guiguzi")
    topic_store.consume([topic_store.fresh()[0]["topic_id"]], "round_x")  # 选题0 consumed
    _age_topic("选题1", days=20)  # 超 14 天 TTL -> 读时标 expired

    assert topic_store.count_fresh() == 1  # 只剩 选题2
    # active_titles = 非 expired(fresh+consumed),给 avoid 用
    assert set(topic_store.active_titles()) == {"选题0", "选题2"}
    # 读操作不写盘;写操作把 expired 持久化
    on_disk = json.loads(topic_store.default_topics_path().read_text(encoding="utf-8"))
    assert [t for t in on_disk["topics"] if t["title"] == "选题1"][0]["status"] == "fresh"
    topic_store.merge([{"title": "补货题", "potential": 1}], source="guiguzi")
    on_disk = json.loads(topic_store.default_topics_path().read_text(encoding="utf-8"))
    assert [t for t in on_disk["topics"] if t["title"] == "选题1"][0]["status"] == "expired"


def test_merge_revives_expired_topic():
    """expired 老题允许被重新提出(avoid 刻意不含它):merge 原位复活成 fresh。"""
    topic_store.merge(_topics(1), source="guiguzi")
    _age_topic("选题0", days=20)
    assert topic_store.count_fresh() == 0
    assert topic_store.merge(_topics(1), source="guiguzi") == {"added": 1, "skipped": 0}
    assert topic_store.count_fresh() == 1
    assert len(topic_store.load()["topics"]) == 1  # 复活不是新增第二条


def test_ttl_env_tolerant(monkeypatch):
    monkeypatch.setenv("NOF_TOPIC_TTL_DAYS", "not-a-number")
    topic_store.merge(_topics(1), source="guiguzi")
    assert topic_store.count_fresh() == 1  # 非法 env 回默认 14 天,不炸不误杀


def test_corrupt_json_tolerated_with_backup():
    path = topic_store.default_topics_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json!!!", encoding="utf-8")

    assert topic_store.load()["topics"] == []  # 按空库处理
    backup = path.with_suffix(path.suffix + ".corrupt")
    assert backup.read_text(encoding="utf-8") == "{not json!!!"  # 原文不丢
    assert topic_store.merge(_topics(1), source="guiguzi")["added"] == 1
    assert topic_store.count_fresh() == 1


# ---------------------------------------------------------------------------
# 消费方:start_round 跳过鬼谷子 / 库存不足 / _plan_scripts 读库
# ---------------------------------------------------------------------------

def test_start_round_skips_guiguzi_when_stocked(tmp_path: Path, monkeypatch):
    """库存 >= count:不派鬼谷子、不需要 benchmark,直开产线并 consume。"""
    monkeypatch.setattr(wolong_rounds, "discover_benchmark", lambda: None)
    topic_store.merge(_topics(5), source="guiguzi")
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()

    out = wolong_rounds.start_round(rs, tp, count=3, benchmark_path="", avoid="")

    assert out["skipped_guiguzi"] is True and out["stage"] == "scripts"
    rid = out["round_id"]
    r = rs.load(rid)
    assert r["goal"]["benchmark_path"] == ""
    assert "guiguzi:0" not in r["intents"]
    assert not [s for s in tp.submits if s["cmd"] == "guiguzi"]
    assert r["stage"] == "scripts" and len(r["lines"]) == 3
    # potential 降序挑题,resume_round 已完成派发(dispatching -> drafting)
    assert [ln["topic"]["title"] for ln in r["lines"]] == ["选题0", "选题1", "选题2"]
    assert all(ln["status"] == "drafting" and ln["task_id"] for ln in r["lines"])
    assert len([s for s in tp.submits if s["cmd"] == "liuyong"]) == 3
    consumed = [t for t in topic_store.load()["topics"] if t["status"] == "consumed"]
    assert len(consumed) == 3 and all(t["consumed_by"] == rid for t in consumed)
    assert topic_store.count_fresh() == 2

    # 走完闸1 -> 正常收盘,不是 48h 僵尸
    for ln in r["lines"]:
        rs.append_event(rid, "terminal", ln["task_id"], status="completed")
        rs.append_event(rid, "decision", ln["task_id"], decision="approved")
    result = wolong_rounds.resume_round(rs, tp, rid)
    assert result["report"]["approved"] == 3
    assert rs.load(rid)["status"] == "done"


def test_start_round_replays_empty_shell_after_crash(tmp_path: Path, monkeypatch):
    """开盘段在 take 预占后、round 落盘前崩溃:重放重走开盘,预占题原样捡回。"""
    monkeypatch.setattr(wolong_rounds, "discover_benchmark", lambda: None)
    topic_store.merge(_topics(3), source="guiguzi")
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    rid = "round_t_crash"
    # 崩溃现场:空壳 round 已建 + 题已预占,产线/意向没来得及落盘
    rs.create(rid, {"count": 3, "benchmark_path": "", "avoid": ""})
    topic_store.take(3, rid)
    assert topic_store.count_fresh() == 0

    out = wolong_rounds.start_round(rs, tp, count=3, benchmark_path="", avoid="",
                                    dispatch_task_id="t_crash")

    assert out["skipped_guiguzi"] is True and out["lines"] == 3
    assert not [s for s in tp.submits if s["cmd"] == "guiguzi"]
    r = rs.load(rid)
    assert r["stage"] == "scripts" and len(r["lines"]) == 3
    assert [ln["topic"]["title"] for ln in r["lines"]] == ["选题0", "选题1", "选题2"]
    consumed = [t for t in topic_store.load()["topics"] if t["status"] == "consumed"]
    assert len(consumed) == 3 and all(t["consumed_by"] == rid for t in consumed)


def test_low_stock_dispatches_guiguzi_with_comment_items(tmp_path: Path, bench: str):
    """库存 < count:照旧派鬼谷子;评论驱动后 params 携带 collected.json 取的 items。"""
    topic_store.merge(_topics(3), source="guiguzi")
    topic_store.consume([topic_store.fresh()[0]["topic_id"]], "round_z")  # 选题0 consumed
    _age_topic("选题1", days=20)                                          # 选题1 expired
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()

    out = wolong_rounds.start_round(rs, tp, count=3, benchmark_path=bench, avoid="旧题, 选题2")
    assert out["stage"] == "topics"
    s = tp.submits[0]
    assert s["cmd"] == "guiguzi" and s["intent_key"] == "guiguzi:0"
    # 评论驱动:items = collected.json 里高赞评论 + 提取文案(不再传 avoid)
    assert s["params"]["items"] == [
        {"text": "提取文案正文", "comment": "高赞评论一"},
        {"text": "提取文案正文", "comment": "高赞评论二"},
    ]


def test_plan_scripts_reads_store_not_task_result(tmp_path: Path, bench: str):
    """鬼谷子完成后选题来源是库:task result 的 topics 不再被读。"""
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=2, benchmark_path=bench, avoid="")
    rid, gz = out["round_id"], out["guiguzi_task"]

    tp.results[gz] = {"topics": []}  # result 为空也不影响:库里有货
    topic_store.merge(_topics(4), source="guiguzi")
    rs.append_event(rid, "terminal", gz, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    r = rs.load(rid)
    assert r["stage"] == "scripts" and len(r["lines"]) == 2
    assert [ln["topic"]["title"] for ln in r["lines"]] == ["选题0", "选题1"]
    assert all(ln["topic"]["topic_id"] for ln in r["lines"])  # line 存库内完整 dict
    consumed = [t for t in topic_store.load()["topics"] if t["status"] == "consumed"]
    assert len(consumed) == 2 and all(t["consumed_by"] == rid for t in consumed)


def test_plan_scripts_empty_store_terminates(tmp_path: Path, bench: str):
    """鬼谷子完成但库内无 fresh(产出全被去重/拒收)-> 止损,不留僵尸。"""
    rs = RoundStore(tmp_path / "rounds")
    tp = FakeTransport()
    out = wolong_rounds.start_round(rs, tp, count=1, benchmark_path=bench, avoid="")
    rid, gz = out["round_id"], out["guiguzi_task"]
    tp.results[gz] = {"topics": _topics()}  # result 有题但没入库:不是选题来源
    rs.append_event(rid, "terminal", gz, status="completed")
    wolong_rounds.resume_round(rs, tp, rid)

    r = rs.load(rid)
    assert r["status"] == "terminated"
    assert "选题库" in r["report"]["reason"]


# ---------------------------------------------------------------------------
# 生产入库方:guiguzi.run 与 mock_guiguzi
# ---------------------------------------------------------------------------

def _fake_caller(topics_json):
    """两步流 fake：analyze 阶段(prompt 含 hook_reason)返分析对象,出题阶段返 topics 数组。"""
    def caller(prompt, **kw):
        if "hook_reason" in prompt:  # analyze prompt schema 带该字段
            return json.dumps({"hook_reason": "因为反差", "audience": "打工人",
                               "hooks": ["钩子1", "钩子2"], "direction": "做反差"}, ensure_ascii=False)
        return topics_json
    return caller


def test_guiguzi_run_two_step_merges_and_keeps_result_shape(monkeypatch):
    """两步流:run = analyze + generate_topics;candidates 双栏 + topics 扁平入库 + 带 analysis。"""
    captured = {}

    opus_topics = json.dumps([
        {"index": 1, "title": "题A", "angle": "反差视角", "why": "w", "potential": 9},
        {"index": 2, "title": "题B", "angle": "升维", "why": "w2", "potential": 7},
    ], ensure_ascii=False)
    deepseek_topics = json.dumps([
        {"index": 1, "title": "题A", "angle": "另一视角", "potential": 6},
        {"index": 2, "title": "题C", "angle": "博弈", "potential": 8},
    ], ensure_ascii=False)

    def fake_opus(prompt, **kw):
        if "hook_reason" not in prompt:
            captured["opus_topic_prompt"] = prompt
        return _fake_caller(opus_topics)(prompt, **kw)

    monkeypatch.setattr(guiguzi, "call_opus", fake_opus)
    monkeypatch.setattr(guiguzi, "call_deepseek", _fake_caller(deepseek_topics))
    items = [{"text": "搞钱认知破局原文", "comment": "评论一"},
             {"text": "搞钱认知破局原文", "comment": "评论二"}]
    res = guiguzi.run(items=items)

    # 出题 prompt 带评论 + 自动选定的分析(爆款原因作指导)
    assert "评论一" in captured["opus_topic_prompt"]
    assert "因为反差" in captured["opus_topic_prompt"]
    # 双模型分析都在;chosen_analysis 自动取 opus
    assert res["analysis"]["opus"]["analysis"]["hook_reason"] == "因为反差"
    assert res["chosen_analysis"]["audience"] == "打工人"
    # 双栏各 2 题(锚定评论以入参为准)
    assert [t["title"] for t in res["candidates"]["opus"]["topics"]] == ["题A", "题B"]
    assert res["candidates"]["opus"]["topics"][0]["anchor_comment"] == "评论一"
    assert res["candidates"]["opus"]["topics"][0]["source_model"] == "opus"
    assert [t["title"] for t in res["candidates"]["deepseek"]["topics"]] == ["题A", "题C"]
    # topics 扁平 = opus + deepseek;out 指向库文件;入库按 title 去重(题A/题B/题C)
    assert len(res["topics"]) == 4
    assert res["out"] == str(topic_store.default_topics_path())
    lib = topic_store.load()["topics"]
    assert {t["title"] for t in lib} == {"题A", "题B", "题C"}
    assert all(t["status"] == "fresh" and t["source"] == "guiguzi" for t in lib)


def test_mock_guiguzi_merges_into_store():
    res = mock_guiguzi()
    assert res["out"] == str(topic_store.default_topics_path())
    assert topic_store.count_fresh() == 5
    mock_guiguzi()  # 再跑一次:按 title 去重,库不翻倍
    assert topic_store.count_fresh() == 5
    # 溯源如实记 mock:误开 mock 混入生产库时可按 source 清理
    assert all(t["source"] == "mock" for t in topic_store.load()["topics"])
