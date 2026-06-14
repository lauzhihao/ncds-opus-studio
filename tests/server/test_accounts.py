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


def test_resolve_reuses_monitored_snapshot(client_env, tmp_path: Path):
    """已监控的账号（按 handle 全局命中）-> 直接复用快照, 不打 TikHub。纯离线。"""
    from ncds_opus_factory.server.subscriptions import save_subscriptions, subscriptions_path

    sp = subscriptions_path(tmp_path / "state" / "tasks")
    save_subscriptions(sp, {"authors": [{
        "sec_uid": "TTSEC", "platform": "tiktok", "unique_id": "mfw_3qj",
        "nickname": "缓存昵称", "avatar": "https://x/a.jpg",
        "follower_count": 100, "like_count": 200, "works_count": 3,
    }]})
    client, _ = client_env
    resp = client.post("/accounts/resolve", json={"text": "https://www.tiktok.com/@mfw_3qj"})
    assert resp.status_code == 200
    b = resp.json()
    assert b.get("cached") is True
    assert b["nickname"] == "缓存昵称" and b["unique_id"] == "mfw_3qj"
    assert b["follower_count"] == 100 and b["works_count"] == 3


def test_bad_all_posts_json_returns_empty(client_env):
    client, bench = client_env
    d = bench / "author_BAD"
    d.mkdir(parents=True, exist_ok=True)
    (d / "all_posts.json").write_text("{ not json", encoding="utf-8")
    resp = client.get("/accounts/BAD/posts")
    assert resp.status_code == 200
    assert resp.json()["posts"] == []
