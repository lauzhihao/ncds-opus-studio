"""tests：P2 订阅传感器——配置读写、派发 tick 去重、HTTP 配置面。"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ncds_opus_factory.server.subscriptions import (
    load_subscriptions,
    run_subscription_tick,
    save_subscriptions,
)
from ncds_opus_factory.server.task_store import TaskStore


class StubRunner:
    """记录 submit 调用的假 runner（鸭子类型,tick 只用 submit）。"""

    def __init__(self):
        self.calls: list[tuple] = []

    async def submit(self, cmd, params, source=None, **kw):
        self.calls.append((cmd, params, source))
        return "t_stub"


def test_load_missing_returns_default(tmp_path: Path):
    cfg = load_subscriptions(tmp_path / "nope.json")
    assert cfg["authors"] == [] and cfg["interval_hours"] > 0


def test_save_load_roundtrip(tmp_path: Path):
    p = tmp_path / "subs.json"
    save_subscriptions(p, {"interval_hours": 4, "authors": [{"sec_uid": "S1", "enabled": True}]})
    cfg = load_subscriptions(p)
    assert cfg["interval_hours"] == 4 and cfg["authors"][0]["sec_uid"] == "S1"


def test_tick_submits_enabled_only(tmp_path: Path):
    """tick 只给启用的作者派 refresh_only 刷新,source=cron。"""
    p = tmp_path / "subs.json"
    save_subscriptions(p, {"authors": [
        {"sec_uid": "S1", "enabled": True},
        {"sec_uid": "S2", "enabled": False},
        {"sec_uid": "  ", "enabled": True},   # 空 sec_uid 跳过
    ]})
    store = TaskStore(tmp_path / "tasks")
    runner = StubRunner()

    n = asyncio.run(run_subscription_tick(runner, store, p))

    assert n == 1
    cmd, params, source = runner.calls[0]
    assert cmd == "shenkuo"
    assert params == {"author": "S1", "platform": "douyin", "refresh_only": True}
    assert source == "cron"


def test_tick_skips_author_with_active_refresh(tmp_path: Path):
    """同作者已有在途 cron 刷新 -> 本轮跳过,防堆积。"""
    p = tmp_path / "subs.json"
    save_subscriptions(p, {"authors": [{"sec_uid": "S1", "enabled": True}]})
    store = TaskStore(tmp_path / "tasks")
    store.create("shenkuo", {"author": "S1", "refresh_only": True}, source="cron")  # pending 在途
    runner = StubRunner()

    n = asyncio.run(run_subscription_tick(runner, store, p))

    assert n == 0 and runner.calls == []


def test_tick_respects_interval(tmp_path: Path):
    """同作者上次刷新距今不足一个周期 -> 跳过(服务频繁重启不烧配额)。"""
    p = tmp_path / "subs.json"
    save_subscriptions(p, {"interval_hours": 2, "authors": [{"sec_uid": "S1", "enabled": True}]})
    store = TaskStore(tmp_path / "tasks")
    done = store.create("shenkuo", {"author": "S1", "refresh_only": True}, source="cron")
    store.update_status(done.task_id, "completed")  # 刚完成,created_at=now

    n = asyncio.run(run_subscription_tick(StubRunner(), store, p))
    assert n == 0

    # 把上次记录改老(3 小时前) -> 周期已过,正常派发
    from datetime import datetime, timedelta
    m = store.get_meta(done.task_id)
    m.created_at = (datetime.now() - timedelta(hours=3)).isoformat()
    store._write_meta(m)
    runner = StubRunner()
    n2 = asyncio.run(run_subscription_tick(runner, store, p))
    assert n2 == 1


def test_tick_stops_when_quota_exhausted(tmp_path: Path):
    """cron 配额耗尽 -> 整轮停止,不制造注定 failed 的任务行。"""
    p = tmp_path / "subs.json"
    save_subscriptions(p, {"authors": [
        {"sec_uid": "S1", "enabled": True}, {"sec_uid": "S2", "enabled": True},
    ]})
    store = TaskStore(tmp_path / "tasks")

    class NoQuotaRunner(StubRunner):
        async def quota_remaining(self, cmd, source=None):
            return 0

    runner = NoQuotaRunner()
    n = asyncio.run(run_subscription_tick(runner, store, p))
    assert n == 0 and runner.calls == []


def test_load_sanitizes_hand_edited_file(tmp_path: Path):
    """手编坏文件:非法条目丢弃、interval 回退默认,GET 路由不至于 500。"""
    p = tmp_path / "subs.json"
    p.write_text(
        '{"interval_hours": "abc", "authors": ['
        '{"sec_uid": " S1 ", "note": 123}, "garbage", {"note": "没有sec_uid"}]}',
        encoding="utf-8",
    )
    cfg = load_subscriptions(p)
    assert cfg["interval_hours"] > 0
    assert cfg["authors"] == [
        {"sec_uid": "S1", "note": None, "enabled": True, "platform": "douyin", "interval_hours": None}
    ]


def test_tick_respects_per_account_interval(tmp_path: Path):
    """per-account interval_hours 优先于全局：账号设 1h，上次刷新 2h 前 -> 该派（全局即便很大也不挡）。"""
    p = tmp_path / "subs.json"
    save_subscriptions(p, {"interval_hours": 24, "authors": [
        {"sec_uid": "S1", "enabled": True, "platform": "douyin", "interval_hours": 1},
    ]})
    store = TaskStore(tmp_path / "tasks")
    done = store.create("shenkuo", {"author": "S1", "refresh_only": True}, source="cron")
    store.update_status(done.task_id, "completed")
    from datetime import datetime, timedelta
    m = store.get_meta(done.task_id)
    m.created_at = (datetime.now() - timedelta(hours=2)).isoformat()
    store._write_meta(m)
    # 全局 24h 会挡，但账号自己设 1h，2h 前的刷新已过期 -> 应派
    n = asyncio.run(run_subscription_tick(StubRunner(), store, p))
    assert n == 1


def test_tick_dispatches_supported_platforms(tmp_path: Path):
    """抖音/TK/油管都派沈括刷新，platform 随任务参数下发。"""
    p = tmp_path / "subs.json"
    save_subscriptions(p, {"authors": [
        {"sec_uid": "DY", "enabled": True, "platform": "douyin"},
        {"sec_uid": "TTSEC", "unique_id": "tt_handle", "enabled": True, "platform": "tiktok"},
        {"sec_uid": "YT", "unique_id": "@yt_handle", "enabled": True, "platform": "youtube"},
    ]})
    store = TaskStore(tmp_path / "tasks")
    runner = StubRunner()
    n = asyncio.run(run_subscription_tick(runner, store, p))
    assert n == 3
    assert [c[1] for c in runner.calls] == [
        {"author": "DY", "platform": "douyin", "refresh_only": True},
        {"author": "tt_handle", "platform": "tiktok", "refresh_only": True},
        {"author": "@yt_handle", "platform": "youtube", "refresh_only": True},
    ]


@pytest.fixture()
def client_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.setenv("NOF_ARTIFACTS_ROOT", str(tmp_path))
    monkeypatch.setenv("NOF_MOCK_AGENTS", "all")

    from ncds_opus_factory.server import artifacts as art_mod
    importlib.reload(art_mod)
    from ncds_opus_factory.server import state as state_mod
    importlib.reload(state_mod)
    from ncds_opus_factory.server.routes import tasks as tasks_mod
    importlib.reload(tasks_mod)
    from ncds_opus_factory.server.routes import subscriptions as subs_routes_mod
    importlib.reload(subs_routes_mod)
    from ncds_opus_factory.server import app as app_mod
    importlib.reload(app_mod)

    return TestClient(app_mod.app), state_mod


def test_http_get_put_roundtrip(client_env):
    client, _ = client_env
    assert client.get("/subscriptions").json()["authors"] == []

    resp = client.put("/subscriptions", json={
        "interval_hours": 3,
        "authors": [{"sec_uid": "MS4wTest", "note": "对标A", "enabled": True}],
    })
    assert resp.status_code == 200
    got = client.get("/subscriptions").json()
    assert got["interval_hours"] == 3 and got["authors"][0]["sec_uid"] == "MS4wTest"

    bad = client.put("/subscriptions", json={"authors": [{"sec_uid": "  "}]})
    assert bad.status_code == 422


def test_http_tick_endpoint(client_env):
    """手动 tick:派发任务带 source=cron,落在任务列表里。"""
    client, state = client_env
    client.put("/subscriptions", json={"authors": [{"sec_uid": "MS4wTick", "enabled": True}]})

    resp = client.post("/subscriptions/tick")
    assert resp.status_code == 200 and resp.json()["submitted"] == 1

    rows = [r for r in client.get("/tasks").json() if r.get("source") == "cron"]
    assert len(rows) == 1 and rows[0]["cmd"] == "shenkuo"


def test_load_migrates_inline_snapshot_to_library(tmp_path: Path):
    """老订阅文件内联名片 -> load 时一次性搬进作者库；返回的引用已瘦身(无快照)。"""
    from ncds_opus_factory.common import authors_repo

    p = tmp_path / "subs.json"
    p.write_text(json.dumps({"authors": [{
        "sec_uid": "MS4wOLD", "enabled": True, "platform": "douyin",
        "nickname": "老号", "avatar": "https://x/a.jpg",
        "follower_count": 42, "like_count": 9, "works_count": 3, "refreshed_at": 1000.0,
    }]}), encoding="utf-8")

    a = load_subscriptions(p)["authors"][0]
    # 引用瘦身:身份+订阅设置保留,展示快照不再内联
    assert a["sec_uid"] == "MS4wOLD" and a["enabled"] is True
    assert "nickname" not in a and "follower_count" not in a and "refreshed_at" not in a
    # 名片已迁进作者库,且保留原 refreshed_at
    prof = authors_repo.load_profile("douyin", "MS4wOLD")
    assert prof["nickname"] == "老号" and prof["follower_count"] == 42 and prof["refreshed_at"] == 1000.0


def test_load_migration_does_not_clobber_fresher_library(tmp_path: Path):
    """库里已有(可能 worker 刚刷新过的)档案时,迁移跳过,不被内联旧值覆盖。"""
    from ncds_opus_factory.common import authors_repo

    authors_repo.save_profile("douyin", "MS4wKEEP", {"nickname": "新号", "follower_count": 999})
    p = tmp_path / "subs.json"
    p.write_text(json.dumps({"authors": [{
        "sec_uid": "MS4wKEEP", "platform": "douyin", "nickname": "旧号", "follower_count": 1,
    }]}), encoding="utf-8")

    load_subscriptions(p)
    assert authors_repo.load_profile("douyin", "MS4wKEEP")["nickname"] == "新号"


def test_get_hydrates_snapshot_from_library(client_env, tmp_path: Path):
    """GET 把作者库名片拼回订阅条目(前端零改动);名单本身只存引用。"""
    from ncds_opus_factory.common import authors_repo
    from ncds_opus_factory.server.subscriptions import save_subscriptions, subscriptions_path

    authors_repo.save_profile("douyin", "MS4wHY", {
        "nickname": "拼接号", "avatar": "https://x/h.jpg", "follower_count": 88,
    })
    save_subscriptions(subscriptions_path(tmp_path / "state" / "tasks"),
                       {"authors": [{"sec_uid": "MS4wHY", "enabled": True, "platform": "douyin"}]})

    client, _ = client_env
    a = client.get("/subscriptions").json()["authors"][0]
    assert a["sec_uid"] == "MS4wHY"
    assert a["nickname"] == "拼接号" and a["follower_count"] == 88


def test_put_splits_snapshot_into_library(client_env, tmp_path: Path):
    """PUT 携带名片 -> 名片进作者库、文件只存瘦引用、返回仍是完整形状。"""
    from ncds_opus_factory.common import authors_repo
    from ncds_opus_factory.server.subscriptions import subscriptions_path

    client, _ = client_env
    resp = client.put("/subscriptions", json={"authors": [{
        "sec_uid": "MS4wPUT", "enabled": True, "platform": "douyin", "unique_id": "put_dy",
        "nickname": "投递号", "avatar": "https://x/p.jpg",
        "follower_count": 12, "like_count": 3, "works_count": 1, "refreshed_at": 5000.0,
    }]})
    assert resp.status_code == 200
    a = resp.json()["authors"][0]
    assert a["nickname"] == "投递号" and a["follower_count"] == 12  # 返回仍完整形状

    # 文件里只剩瘦引用(无展示快照)
    raw = json.loads(subscriptions_path(tmp_path / "state" / "tasks").read_text(encoding="utf-8"))
    fa = raw["authors"][0]
    assert fa["sec_uid"] == "MS4wPUT" and fa["unique_id"] == "put_dy"
    assert "nickname" not in fa and "follower_count" not in fa
    # 名片进了作者库
    prof = authors_repo.load_profile("douyin", "MS4wPUT")
    assert prof["nickname"] == "投递号" and prof["follower_count"] == 12
