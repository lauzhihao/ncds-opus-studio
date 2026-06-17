"""tests：对标账号作品列表薄读端点 GET /accounts/{sec_uid}/posts。

只读端点：读 state/benchmark/author_{sec_uid}/all_posts.json（合并 collected.json 采集状态）。
缺文件返回空列表（不 500）。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.setenv("NOF_ARTIFACTS_ROOT", str(tmp_path))
    monkeypatch.setenv("NOF_MOCK_AGENTS", "all")

    from ncds_opus_factory.server import artifacts as art_mod
    importlib.reload(art_mod)
    from ncds_opus_factory.server import state as state_mod
    importlib.reload(state_mod)
    # accounts 在 import 时按 STATE_DIR.parent 算 benchmark 根，必须在 state 之后 reload
    from ncds_opus_factory.server.routes import accounts as accounts_mod
    importlib.reload(accounts_mod)
    from ncds_opus_factory.server import app as app_mod
    importlib.reload(app_mod)

    bench = tmp_path / "state" / "benchmark"
    return TestClient(app_mod.app), bench


def _seed(bench: Path, sec_uid: str, posts: list[dict], collected_ids: list[str]) -> None:
    d = bench / f"author_{sec_uid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "all_posts.json").write_text(json.dumps(posts, ensure_ascii=False), encoding="utf-8")
    (d / "collected.json").write_text(
        json.dumps({"generated_at": 1, "items": [{"aweme_id": a} for a in collected_ids]}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_missing_account_returns_empty(client_env):
    client, _ = client_env
    resp = client.get("/accounts/NOPE/posts")
    assert resp.status_code == 200
    assert resp.json() == {"sec_uid": "NOPE", "posts": []}


def test_posts_merged_sorted_and_share_url(client_env):
    client, bench = client_env
    # 带 cover_url（非空）避免端点触发"封面回填"走网络
    _seed(
        bench,
        "S1",
        posts=[
            {"aweme_id": "111", "desc": "低赞", "digg": 10, "comment": 1, "share": 2, "collect": 3, "cover_url": "http://c/111.jpg"},
            {"aweme_id": "222", "desc": "高赞", "digg": 999, "comment": 9, "share": 8, "collect": 7, "cover_url": "http://c/222.jpg"},
            {"aweme_id": "", "desc": "无 id 应丢弃", "digg": 5, "cover_url": "http://c/x.jpg"},
        ],
        collected_ids=["222"],
    )
    resp = client.get("/accounts/S1/posts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sec_uid"] == "S1"
    posts = body["posts"]
    # 无 aweme_id 的被丢弃；按 digg 降序
    assert [p["aweme_id"] for p in posts] == ["222", "111"]
    top = posts[0]
    assert top["collected"] is True
    assert top["share_url"] == "https://www.douyin.com/video/222"
    assert top["cover_url"] == "http://c/222.jpg"
    assert top["comment"] == 9 and top["share"] == 8 and top["collect"] == 7
    assert posts[1]["collected"] is False


def test_resolve_sec_uid_offline_paths():
    """完整 user URL / 裸 sec_uid 走纯解析（无网络）；无可解析内容返回 None。"""
    from ncds_opus_factory.common import tikhub_client

    sid = "MS4wLjABAAAADZQWu__mECzbikseSgWY5mqYOeIApXsfK1W0L92cNo8p1_GoaE_vjPfscdHyj5Ub"
    # 完整主页 URL（sec_uid 在 path，带 query）
    assert (
        tikhub_client.resolve_sec_uid(f"https://www.douyin.com/user/{sid}?from_tab_name=main&vid=123")
        == sid
    )
    # 裸 sec_uid
    assert tikhub_client.resolve_sec_uid(sid) == sid
    # 整段文本里夹着 /user/ 链接
    assert tikhub_client.resolve_sec_uid(f"看看这个号 https://www.douyin.com/user/{sid} 不错") == sid
    # 没有链接也不是 sec_uid -> None
    assert tikhub_client.resolve_sec_uid("随便一段文字") is None
    assert tikhub_client.resolve_sec_uid("") is None


def test_resolve_tiktok_handle_offline():
    """TikTok 主页链接 -> 抽 @handle（无网络）；非 TikTok 返回 None。"""
    from ncds_opus_factory.common import tikhub_client

    assert tikhub_client.resolve_tiktok_handle("https://www.tiktok.com/@mfw_3qj") == "mfw_3qj"
    assert tikhub_client.resolve_tiktok_handle("看这个 https://www.tiktok.com/@some.user_1/video/123") == "some.user_1"
    assert tikhub_client.resolve_tiktok_handle("https://www.douyin.com/user/MS4wAbc") is None
    assert tikhub_client.resolve_tiktok_handle("随便文字") is None


def test_resolve_endpoint_422_on_garbage(client_env):
    """解析不出账号 -> 422（不 500）。"""
    client, _ = client_env
    resp = client.post("/accounts/resolve", json={"text": "这不是链接"})
    assert resp.status_code == 422


def test_resolve_fresh_cache_hit_skips_tikhub(client_env, monkeypatch):
    """作者库命中且新鲜 -> 直接回缓存, 不打 TikHub。纯离线。"""
    from ncds_opus_factory.common import authors_repo, tikhub_client

    authors_repo.save_profile("douyin", "MS4wFRESH", {
        "nickname": "新鲜号", "avatar": "https://x/a.jpg", "unique_id": "fresh_dy",
        "follower_count": 100, "like_count": 200, "works_count": 3,
    })
    monkeypatch.setattr(tikhub_client, "fetch_douyin_profile",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("命中新鲜缓存不该打 TikHub")))
    client, _ = client_env
    b = client.post("/accounts/resolve", json={"text": "MS4wFRESH"}).json()
    assert b["cached"] is True and b.get("stale") is None
    assert b["nickname"] == "新鲜号" and b["follower_count"] == 100 and b["unique_id"] == "fresh_dy"


def test_resolve_miss_fetches_and_caches(client_env, monkeypatch):
    """未命中 -> 打一次 TikHub 落库；再解析命中缓存(cached=True),不再打。"""
    from ncds_opus_factory.common import authors_repo, tikhub_client

    calls = {"n": 0}

    def fake(sec_uid, token=None):
        calls["n"] += 1
        return {"platform": "douyin", "sec_uid": sec_uid, "nickname": "实拉号",
                "unique_id": "miss_dy", "avatar": "", "follower_count": 7,
                "like_count": 0, "works_count": 1}

    monkeypatch.setattr(tikhub_client, "fetch_douyin_profile", fake)
    client, _ = client_env

    b1 = client.post("/accounts/resolve", json={"text": "MS4wMISS"}).json()
    assert b1["cached"] is False and b1["nickname"] == "实拉号" and calls["n"] == 1
    assert authors_repo.load_profile("douyin", "MS4wMISS")["follower_count"] == 7

    b2 = client.post("/accounts/resolve", json={"text": "MS4wMISS"}).json()
    assert b2["cached"] is True and calls["n"] == 1  # 命中缓存,未再打


def test_resolve_stale_returns_old_and_dispatches_refresh(client_env, tmp_path: Path, monkeypatch):
    """命中但过期 + 已关注 -> 先回旧档案(stale=True) + 派 worker 刷新(cron shenkuo)。"""
    import time as _t

    from ncds_opus_factory.common import authors_repo
    from ncds_opus_factory.server import state as state_mod
    from ncds_opus_factory.server.subscriptions import save_subscriptions, subscriptions_path

    # 库里旧档案(3h 前 -> 超默认 2h TTL)
    authors_repo.save_profile("douyin", "MS4wSTALE", {"nickname": "旧号", "follower_count": 5},
                              refreshed_at=_t.time() - 3 * 3600)
    # 该号已关注(派发前置条件)
    save_subscriptions(subscriptions_path(tmp_path / "state" / "tasks"),
                       {"authors": [{"sec_uid": "MS4wSTALE", "enabled": True, "platform": "douyin"}]})

    submitted: list[tuple] = []

    async def spy(cmd, params, source=None, **kw):
        submitted.append((cmd, params, source))
        return "t_spy"

    monkeypatch.setattr(state_mod.RUNNER, "submit", spy)

    client, _ = client_env
    b = client.post("/accounts/resolve", json={"text": "MS4wSTALE"}).json()
    assert b["cached"] is True and b["stale"] is True and b["nickname"] == "旧号"
    assert submitted == [("shenkuo", {"author": "MS4wSTALE", "refresh_only": True}, "cron")]


def test_bad_all_posts_json_returns_empty(client_env):
    client, bench = client_env
    d = bench / "author_BAD"
    d.mkdir(parents=True, exist_ok=True)
    (d / "all_posts.json").write_text("{ not json", encoding="utf-8")
    resp = client.get("/accounts/BAD/posts")
    assert resp.status_code == 200
    assert resp.json()["posts"] == []
