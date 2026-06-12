"""tests：P5 排产策略协程——事件消费/offset 语义/链推进/配额停/库存补货。

目录解析全部走 NOF_STATE_DIR(tests/conftest.py 已隔离到 tmp)。
鸭子假件(同 test_retro.py):不起 uvicorn,不碰真 TaskRunner。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from ncds_opus_factory.common import topic_store
from ncds_opus_factory.server import planner
from ncds_opus_factory.server.schemas import TaskMeta


# ---------------------------------------------------------------------------
# 假件
# ---------------------------------------------------------------------------
class _FakeRunner:
    def __init__(self, quota: int | dict[str, int] = 99, store=None):
        self.quota = quota
        self.store = store  # 真 RUNNER.submit 返回前 meta 已落盘,假件保持同一契约
        self.submits: list[dict[str, Any]] = []
        self.quota_calls: list[tuple[str, Any]] = []  # 自查必须走 cron 桶(§8.4)

    def quota_remaining(self, cmd, source=None):
        self.quota_calls.append((cmd, source))
        if isinstance(self.quota, dict):
            return self.quota.get(cmd, 99)
        return self.quota

    async def submit(self, cmd, params, source=None, **kw):  # noqa: ARG002
        self.submits.append({"cmd": cmd, "params": params, "source": source})
        task_id = f"t_{cmd}_{len(self.submits)}"
        if self.store is not None:
            self.store._metas.append(_meta(task_id, cmd, params, status="pending",
                                           source=source))
        return task_id


class _FakeStore:
    def __init__(self, metas=()):
        self._metas = list(metas)

    def list_tasks(self):
        return list(self._metas)

    def get_meta(self, task_id):
        for m in self._metas:
            if m.task_id == task_id:
                return m
        return None


def _meta(task_id: str, cmd: str, params: dict, status="pending",
          source="cron", created_at: str | None = None) -> TaskMeta:
    return TaskMeta(task_id=task_id, cmd=cmd, params=params, status=status,
                    created_at=created_at or datetime.now().isoformat(), source=source)


# ---------------------------------------------------------------------------
# 造数据
# ---------------------------------------------------------------------------
def _event(aweme: str, sec_uid: str, etype="new_post") -> str:
    return json.dumps({"type": etype, "aweme_id": aweme, "sec_uid": sec_uid,
                       "ts": 1750000000, "desc": "测试事件"}, ensure_ascii=False)

def _write_events(lines: list[str], tail: str | None = None) -> None:
    """整批写事件文件;tail 是不带 \\n 的不完整尾行。"""
    d = planner.shenkuo_dir()
    d.mkdir(parents=True, exist_ok=True)
    payload = "".join(line + "\n" for line in lines) + (tail or "")
    with planner.events_path().open("a", encoding="utf-8") as f:
        f.write(payload)


def _seed_chain(aweme: str, sec_uid: str, task_id: str,
                created_at: str | None = None) -> None:
    chains = planner.load_chains()
    chains.append({"aweme_id": aweme, "sec_uid": sec_uid,
                   "shenkuo_task_id": task_id,
                   "created_at": created_at or datetime.now().isoformat()})
    planner.save_chains(chains)


def _make_all_posts(sec_uid: str) -> str:
    p = planner.benchmark_path_for(sec_uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[]", encoding="utf-8")
    return str(p)


def _stock_topics(n: int) -> None:
    topic_store.merge([{"title": f"库存选题{i}", "potential": 5 + i} for i in range(n)],
                      source="guiguzi")


def _tick(runner, store) -> dict[str, int]:
    return asyncio.run(planner.planner_tick(runner, store))


def _no_benchmark(monkeypatch) -> None:
    """模拟「无对标数据」:开发机仓库根 state/ 可能有真数据,必须隔离。"""
    monkeypatch.setattr(planner.wolong_rounds, "discover_benchmark", lambda: None)


# ---------------------------------------------------------------------------
# 冷启动:三缺失静默空转
# ---------------------------------------------------------------------------
def test_cold_start_silent_noop(monkeypatch):
    _no_benchmark(monkeypatch)
    runner = _FakeRunner()
    stats = _tick(runner, _FakeStore())
    assert runner.submits == []
    assert stats == {"events_consumed": 0, "guiguzi_dispatched": 0,
                     "restock_dispatched": 0}
    # offset/链文件都不强制创建
    assert not planner.offset_path().exists()
    assert not planner.chains_path().exists()


# ---------------------------------------------------------------------------
# 事件消费
# ---------------------------------------------------------------------------
def test_events_dispatch_shenkuo_and_register_chains(monkeypatch):
    _no_benchmark(monkeypatch)
    # 第三行重复 aweme a1:同批内防重,链按 aweme_id 幂等
    _write_events([_event("a1", "uidA"), _event("a2", "uidB", "spike"),
                   _event("a1", "uidA", "spike")])
    store = _FakeStore()
    runner = _FakeRunner(store=store)
    stats = _tick(runner, store)

    assert stats["events_consumed"] == 3
    assert [s["cmd"] for s in runner.submits] == ["shenkuo", "shenkuo"]
    assert runner.submits[0] == {"cmd": "shenkuo", "params": {"aweme": "a1"},
                                 "source": "cron"}
    assert runner.submits[1]["params"] == {"aweme": "a2"}
    chains = planner.load_chains()
    assert [(c["aweme_id"], c["sec_uid"]) for c in chains] == [("a1", "uidA"),
                                                               ("a2", "uidB")]
    assert all(c["shenkuo_task_id"] and c["created_at"] for c in chains)
    assert planner.read_offset() == planner.events_path().stat().st_size

    # crash 重放语义:offset 回拨到 0(模拟 submit 后 offset 前崩溃),
    # store 里已有同 aweme 的在途 cron 任务 -> 防重索引兜住,0 新派发
    planner.write_offset(0)
    store2 = _FakeStore([
        _meta("t_shenkuo_1", "shenkuo", {"aweme": "a1"}, status="running"),
        _meta("t_shenkuo_2", "shenkuo", {"aweme": "a2"}, status="running"),
    ])
    runner2 = _FakeRunner(store=store2)
    _tick(runner2, store2)
    assert [s for s in runner2.submits if s["cmd"] == "shenkuo"] == []
    assert len(planner.load_chains()) == 2  # 链幂等,没重复登记
    assert planner.read_offset() == planner.events_path().stat().st_size


def test_dedup_skip_still_registers_missing_chain(monkeypatch):
    """crash 在 submit 后、链落盘前:重放时防重命中也要补登记链,事件不丢。"""
    _no_benchmark(monkeypatch)
    _write_events([_event("a9", "uidZ")])
    store = _FakeStore([_meta("t_old", "shenkuo", {"aweme": "a9"}, status="running")])
    runner = _FakeRunner()
    _tick(runner, store)
    assert runner.submits == []
    chains = planner.load_chains()
    assert len(chains) == 1 and chains[0]["shenkuo_task_id"] == "t_old"


def test_bad_line_skipped_offset_advances(monkeypatch):
    _no_benchmark(monkeypatch)
    _write_events(["{这不是json", _event("a1", "uidA"),
                   json.dumps({"type": "unknown_event"})])
    store = _FakeStore()
    runner = _FakeRunner(store=store)
    stats = _tick(runner, store)
    assert stats["events_consumed"] == 3
    assert len(runner.submits) == 1 and runner.submits[0]["params"] == {"aweme": "a1"}
    assert planner.read_offset() == planner.events_path().stat().st_size


def test_offset_beyond_file_size_resets(monkeypatch):
    _no_benchmark(monkeypatch)
    _write_events([_event("a1", "uidA")])
    planner.write_offset(99999)  # 文件被换的形态
    store = _FakeStore()
    runner = _FakeRunner(store=store)
    _tick(runner, store)
    assert len(runner.submits) == 1
    assert planner.read_offset() == planner.events_path().stat().st_size


def test_incomplete_tail_line_not_consumed(monkeypatch):
    _no_benchmark(monkeypatch)
    _write_events([_event("a1", "uidA")], tail='{"type":"new_post","aweme_id":"a2"')
    store = _FakeStore()
    runner = _FakeRunner(store=store)
    stats = _tick(runner, store)
    assert stats["events_consumed"] == 1
    assert len(runner.submits) == 1
    complete_len = len((_event("a1", "uidA") + "\n").encode("utf-8"))
    assert planner.read_offset() == complete_len  # 停在完整行之后

    # 尾行补全后,续读只消费新完成的那行
    with planner.events_path().open("a", encoding="utf-8") as f:
        f.write(',"sec_uid":"uidB","ts":1}\n')
    runner2 = _FakeRunner(store=store)
    _tick(runner2, store)
    assert len(runner2.submits) == 1 and runner2.submits[0]["params"] == {"aweme": "a2"}
    assert planner.read_offset() == planner.events_path().stat().st_size


def test_quota_exhausted_stops_events_offset_frozen(monkeypatch):
    _no_benchmark(monkeypatch)
    _write_events([_event("a1", "uidA"), _event("a2", "uidB")])
    runner = _FakeRunner(quota=0)
    _tick(runner, _FakeStore())
    assert runner.submits == []
    # 一行都没成功处理 -> offset 不推进(文件也不创建)
    assert planner.read_offset() == 0
    assert not planner.chains_path().exists()


def test_quota_midway_offset_advances_to_last_processed(monkeypatch):
    """配额只够一条:offset 推进到已成功处理的最后一行之后,不过推。"""
    _no_benchmark(monkeypatch)
    line1, line2 = _event("a1", "uidA"), _event("a2", "uidB")
    _write_events([line1, line2])

    class _OneShot(_FakeRunner):
        def quota_remaining(self, cmd, source=None):  # noqa: ARG002
            return 1 - len([s for s in self.submits if s["cmd"] == cmd])

    store = _FakeStore()
    runner = _OneShot(store=store)
    _tick(runner, store)
    assert len(runner.submits) == 1
    assert planner.read_offset() == len((line1 + "\n").encode("utf-8"))
    assert len(planner.load_chains()) == 1  # 已处理那条的链已落盘


def test_event_submit_exception_freezes_offset(monkeypatch):
    """派发异常(非配额):该行不消费,offset 停在已成功行后,下轮重试不丢事件。"""
    _no_benchmark(monkeypatch)
    line1, line2 = _event("a1", "uidA"), _event("a2", "uidB")
    _write_events([line1, line2])

    class _Boom(_FakeRunner):
        async def submit(self, cmd, params, source=None, **kw):
            if params.get("aweme") == "a2":
                raise RuntimeError("boom")
            return await super().submit(cmd, params, source=source, **kw)

    store = _FakeStore()
    runner = _Boom(store=store)
    _tick(runner, store)
    assert len(runner.submits) == 1
    assert planner.read_offset() == len((line1 + "\n").encode("utf-8"))
    assert len(planner.load_chains()) == 1  # 成功那条的链已落盘

    # 下轮恢复:续读只剩失败那行
    runner2 = _FakeRunner(store=store)
    _tick(runner2, store)
    assert len(runner2.submits) == 1 and runner2.submits[0]["params"] == {"aweme": "a2"}
    assert planner.read_offset() == planner.events_path().stat().st_size


def test_quota_check_passes_cron_bucket(monkeypatch):
    """配额自查必须走 cron 桶(§8.4):quota_remaining 收到 source=\"cron\"。"""
    _no_benchmark(monkeypatch)
    _write_events([_event("a1", "uidA")])
    _make_all_posts("uidB")
    _seed_chain("a2", "uidB", "t_s2")
    store = _FakeStore([_meta("t_s2", "shenkuo", {"aweme": "a2"}, status="completed")])
    runner = _FakeRunner(store=store)
    _tick(runner, store)
    assert ("shenkuo", "cron") in runner.quota_calls
    assert ("guiguzi", "cron") in runner.quota_calls


# ---------------------------------------------------------------------------
# 链推进
# ---------------------------------------------------------------------------
def test_chain_advances_to_guiguzi_on_terminal(monkeypatch):
    _no_benchmark(monkeypatch)
    _stock_topics(2)  # < 低水位,但链派发后 inflight 置位 -> 补货也不会双派
    bench = _make_all_posts("uidA")
    _seed_chain("a1", "uidA", "t_s1")
    store = _FakeStore([_meta("t_s1", "shenkuo", {"aweme": "a1"}, status="failed")])
    runner = _FakeRunner()
    stats = _tick(runner, store)

    assert stats["guiguzi_dispatched"] == 1 and stats["restock_dispatched"] == 0
    assert len(runner.submits) == 1
    sub = runner.submits[0]
    assert sub["cmd"] == "guiguzi" and sub["source"] == "cron"
    assert sub["params"]["benchmark_path"] == bench
    assert sub["params"]["category"] is None  # 显式 None,默认 'growth' 会 ValueError
    assert sorted(sub["params"]["avoid"]) == sorted(topic_store.active_titles())
    assert "库存选题0" in sub["params"]["avoid"]
    assert planner.load_chains() == []  # 派发成功即销链

    # 幂等:链已销,重跑不再派
    runner2 = _FakeRunner()
    _tick(runner2, store)
    assert [s for s in runner2.submits if s["cmd"] == "guiguzi"
            and s["params"]["benchmark_path"] == bench] == []


def test_chain_waits_until_terminal(monkeypatch):
    _no_benchmark(monkeypatch)
    _seed_chain("a1", "uidA", "t_s1")
    _make_all_posts("uidA")
    store = _FakeStore([_meta("t_s1", "shenkuo", {"aweme": "a1"}, status="running")])
    runner = _FakeRunner()
    _tick(runner, store)
    assert runner.submits == []
    assert len(planner.load_chains()) == 1  # 未终态,链保留


def test_chain_dropped_when_all_posts_missing(monkeypatch):
    _no_benchmark(monkeypatch)
    _seed_chain("a1", "uidNoData", "t_s1")
    store = _FakeStore([_meta("t_s1", "shenkuo", {"aweme": "a1"}, status="completed")])
    runner = _FakeRunner()
    _tick(runner, store)
    assert runner.submits == []
    assert planner.load_chains() == []  # 对标缺失 -> 丢链(警告)


def test_chain_24h_dedup_destroys_without_dispatch(monkeypatch):
    _no_benchmark(monkeypatch)
    bench = _make_all_posts("uidA")
    _seed_chain("a1", "uidA", "t_s1")
    store = _FakeStore([
        _meta("t_s1", "shenkuo", {"aweme": "a1"}, status="completed"),
        # 24h 内已有同 benchmark_path 的 cron 鬼谷子(在途,兼挡补货)
        _meta("t_g1", "guiguzi", {"benchmark_path": bench, "category": None},
              status="pending"),
    ])
    runner = _FakeRunner()
    _tick(runner, store)
    assert runner.submits == []
    assert planner.load_chains() == []  # 直接销链不派


def test_chain_over_48h_force_dropped(monkeypatch):
    _no_benchmark(monkeypatch)
    _make_all_posts("uidA")
    old = (datetime.now() - timedelta(hours=49)).isoformat()
    _seed_chain("a1", "uidA", "t_s1", created_at=old)
    store = _FakeStore([_meta("t_s1", "shenkuo", {"aweme": "a1"}, status="running")])
    runner = _FakeRunner()
    _tick(runner, store)
    assert runner.submits == []
    assert planner.load_chains() == []


def test_chain_submit_exception_keeps_chain(monkeypatch):
    """鬼谷子派发异常:链保留,下轮重试(宁可重派不丢事件)。"""
    _no_benchmark(monkeypatch)
    _make_all_posts("uidA")
    _seed_chain("a1", "uidA", "t_s1")
    store = _FakeStore([_meta("t_s1", "shenkuo", {"aweme": "a1"}, status="completed")])

    class _Boom(_FakeRunner):
        async def submit(self, cmd, params, source=None, **kw):
            if cmd == "guiguzi":
                raise RuntimeError("boom")
            return await super().submit(cmd, params, source=source, **kw)

    runner = _Boom(store=store)
    stats = _tick(runner, store)
    assert stats["guiguzi_dispatched"] == 0
    assert len(planner.load_chains()) == 1


def test_chain_quota_exhausted_keeps_chain(monkeypatch):
    _no_benchmark(monkeypatch)
    _make_all_posts("uidA")
    _seed_chain("a1", "uidA", "t_s1")
    store = _FakeStore([_meta("t_s1", "shenkuo", {"aweme": "a1"}, status="completed")])
    runner = _FakeRunner(quota={"shenkuo": 99, "guiguzi": 0})
    _tick(runner, store)
    assert runner.submits == []
    assert len(planner.load_chains()) == 1  # 配额停:链保留下轮再推


# ---------------------------------------------------------------------------
# 库存补货
# ---------------------------------------------------------------------------
def test_restock_when_below_low_water(monkeypatch):
    _stock_topics(2)  # fresh=2 < 5
    monkeypatch.setattr(planner.wolong_rounds, "discover_benchmark",
                        lambda: "/tmp/bench/author_x/all_posts.json")
    runner = _FakeRunner()
    stats = _tick(runner, _FakeStore())
    assert stats["restock_dispatched"] == 1
    assert len(runner.submits) == 1
    sub = runner.submits[0]
    assert sub["cmd"] == "guiguzi" and sub["source"] == "cron"
    assert sub["params"]["benchmark_path"] == "/tmp/bench/author_x/all_posts.json"
    assert sub["params"]["category"] is None
    assert sorted(sub["params"]["avoid"]) == sorted(topic_store.active_titles())


def test_restock_skipped_when_stock_sufficient(monkeypatch):
    _stock_topics(6)  # fresh=6 >= 5
    monkeypatch.setattr(planner.wolong_rounds, "discover_benchmark",
                        lambda: "/tmp/bench/author_x/all_posts.json")
    runner = _FakeRunner()
    _tick(runner, _FakeStore())
    assert runner.submits == []


def test_restock_skipped_when_inflight_guiguzi(monkeypatch):
    _stock_topics(1)
    monkeypatch.setattr(planner.wolong_rounds, "discover_benchmark",
                        lambda: "/tmp/bench/author_x/all_posts.json")
    store = _FakeStore([_meta("t_g1", "guiguzi", {"benchmark_path": "/x"},
                              status="running")])
    runner = _FakeRunner()
    _tick(runner, store)
    assert runner.submits == []  # 在途 cron 鬼谷子,防堆积


def test_restock_cooldown_same_benchmark(monkeypatch):
    """补货防重(与链推进同口径):24h 内已有同对标 cron 鬼谷子 -> 冷却不派。"""
    _stock_topics(1)  # fresh=1 < 5
    bench = "/tmp/bench/author_x/all_posts.json"
    monkeypatch.setattr(planner.wolong_rounds, "discover_benchmark", lambda: bench)
    store = _FakeStore([_meta("t_g0", "guiguzi", {"benchmark_path": bench},
                              status="completed")])
    runner = _FakeRunner()
    _tick(runner, store)
    assert runner.submits == []  # 同对标刚跑过:不反复烧配额


def test_restock_quota_exhausted_no_submit(monkeypatch):
    _stock_topics(1)
    monkeypatch.setattr(planner.wolong_rounds, "discover_benchmark",
                        lambda: "/tmp/bench/author_x/all_posts.json")
    runner = _FakeRunner(quota=0)
    _tick(runner, _FakeStore())
    assert runner.submits == []
