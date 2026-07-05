"""tests：单作品解析端点 POST /works/resolve（临时任务"智能解析"）。

解析出 platform+aweme_id → 查本地缓存（查过不打 TikHub）→ 没查过用 key 锁打 TikHub。
mock fetch_one_video_detail，断言：缓存命中不重复打、并发同 key single-flight、解析失败 422。
text 传纯数字 aweme_id，避免 resolve_aweme_id 走网络重定向（纯数字直接返回）。
"""

from __future__ import annotations

import importlib
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 最小可用 aweme_detail：能喂真实 extract_work_card 跑通全部字段
FAKE_DETAIL = {
    "desc": "测试作品标题",
    "statistics": {"digg_count": 100, "comment_count": 20, "share_count": 5, "collect_count": 8},
    "text_extra": [{"hashtag_name": "话题1"}, {"hashtag_name": "话题2"}, {"other": "无标签项应跳过"}],
    "video": {"cover_original_scale": {"url_list": ["http://c/cover.jpg"]}},
    "author": {
        "sec_uid": "MS4wAUTHOR",
        "nickname": "测试作者",
        "unique_id": "test_author",
        "avatar_168x168": {"url_list": ["http://a/avatar.jpg"]},
    },
}

AID = "7123456789012345678"  # 纯数字 -> resolve_aweme_id 直接返回，不走网络


@pytest.fixture()
def client_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.setenv("NOF_ARTIFACTS_ROOT", str(tmp_path))
    monkeypatch.setenv("NOF_MOCK_AGENTS", "all")

    from ncds_opus_factory.server import artifacts as art_mod
    importlib.reload(art_mod)
    from ncds_opus_factory.server import state as state_mod
    importlib.reload(state_mod)
    # works 缓存走 works_repo（运行时按 NOF_STATE_DIR 算根，无 import 期状态，reload 仅为干净）
    from ncds_opus_factory.server.routes import works as works_mod
    importlib.reload(works_mod)
    from ncds_opus_factory.server import app as app_mod
    importlib.reload(app_mod)

    return TestClient(app_mod.app), works_mod


def test_extract_work_card_fields():
    """extract_work_card：标题/话题/四项数据/封面 + 作者档案（sec_uid/avatar/...）。"""
    from ncds_opus_factory.common import tikhub_client

    card = tikhub_client.extract_work_card(FAKE_DETAIL)
    assert card["title"] == "测试作品标题"
    assert card["hashtags"] == ["话题1", "话题2"]  # 无 hashtag_name 的项被跳过
    assert card["digg"] == 100 and card["comment"] == 20
    assert card["share"] == 5 and card["collect"] == 8
    assert card["cover_url"] == "http://c/cover.jpg"
    a = card["author"]
    assert a["platform"] == "douyin"
    assert a["sec_uid"] == "MS4wAUTHOR"
    assert a["nickname"] == "测试作者"
    assert a["unique_id"] == "test_author"
    assert a["avatar"] == "http://a/avatar.jpg"


def test_extract_work_card_strips_title_and_fills_missing_tags():
    """title 剥离 #话题；text_extra 漏掉的话题用 desc 正则补回（复刻"#教育"被漏的 case）。"""
    from ncds_opus_factory.common import tikhub_client

    detail = {
        "desc": "为什么爸爸打孩子 #原生家庭 #父母 #孩子 #成长 #教育",
        "statistics": {},
        # text_extra 故意只给前 4 个，模拟 TikHub 漏掉末尾 #教育
        "text_extra": [
            {"hashtag_name": "原生家庭"},
            {"hashtag_name": "父母"},
            {"hashtag_name": "孩子"},
            {"hashtag_name": "成长"},
        ],
        "video": {},
        "author": {},
    }
    card = tikhub_client.extract_work_card(detail)
    assert card["title"] == "为什么爸爸打孩子"  # 话题被剥离，不再混进标题
    assert card["hashtags"] == ["原生家庭", "父母", "孩子", "成长", "教育"]  # #教育 从 desc 补回


def test_resolve_work_422_on_garbage(client_env):
    """解析不出 aweme_id -> 422（不 500）。"""
    client, _ = client_env
    resp = client.post("/works/resolve", json={"text": "这不是链接"})
    assert resp.status_code == 422


def test_resolve_work_caches_and_skips_tikhub(client_env, monkeypatch):
    """首解析打一次 TikHub 并落盘；再解析命中缓存（cached=True），不再打。"""
    client, works_mod = client_env
    calls = {"n": 0}

    def fake(aweme_id, token=None):
        calls["n"] += 1
        return FAKE_DETAIL

    monkeypatch.setattr(works_mod.tikhub_client, "fetch_one_video_detail", fake)

    r1 = client.post("/works/resolve", json={"text": AID})
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["aweme_id"] == AID
    assert b1["platform"] == "douyin"
    assert b1["share_url"] == f"https://www.douyin.com/video/{AID}"
    assert b1["title"] == "测试作品标题"
    assert b1["author"]["sec_uid"] == "MS4wAUTHOR"
    assert b1["cached"] is False
    assert calls["n"] == 1
    # 作品卡落作品仓库 manifest 的 card 分区(与沈括 products 分区共存)
    from ncds_opus_factory.common import works_repo
    assert works_repo.manifest_path("douyin", AID).exists()
    assert works_repo.load_card("douyin", AID)["title"] == "测试作品标题"

    r2 = client.post("/works/resolve", json={"text": AID})
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["cached"] is True
    assert b2["title"] == "测试作品标题"
    assert calls["n"] == 1  # 命中缓存，未再打 TikHub


def test_resolve_work_returns_domain_field(client_env, monkeypatch):
    """resolve 响应带 domain 字段：新作品为 None；PATCH 写入后缓存命中时带回已存 domain。"""
    client, works_mod = client_env

    def fake(aweme_id, token=None):
        return FAKE_DETAIL

    monkeypatch.setattr(works_mod.tikhub_client, "fetch_one_video_detail", fake)

    # 首次解析：domain=None（作品刚落盘，manifest 里还无 domain）
    r = client.post("/works/resolve", json={"text": AID})
    assert r.status_code == 200
    assert r.json()["domain"] is None

    # 写入 domain
    pr = client.patch(f"/works/douyin/{AID}/domain", json={"domain": "emotion"})
    assert pr.status_code == 200
    assert pr.json() == {"ok": True, "platform": "douyin", "aweme_id": AID, "domain": "emotion"}

    # 再次 resolve（命中缓存）：domain 随 manifest 带回
    r2 = client.post("/works/resolve", json={"text": AID})
    assert r2.status_code == 200
    assert r2.json()["cached"] is True
    assert r2.json()["domain"] == "emotion"


def test_resolve_work_tiktok_metadata_card_without_douyin_tikhub(client_env, monkeypatch):
    """TikTok 单作品链接：不误走抖音详情接口，改用非抖音 metadata 填作品卡。"""
    client, works_mod = client_env

    monkeypatch.setattr(
        works_mod.tikhub_client, "fetch_one_video_detail",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("TikTok 不该打抖音详情接口")),
    )
    monkeypatch.setattr(
        works_mod.tikhub_client, "fetch_video_ref_meta",
        lambda _ref: {
            "desc": "TK title #tag",
            "author": "kevinpoterfield",
            "hashtags": ["tag"],
            "digg": 12,
            "comment": 3,
            "share": 2,
            "collect": 8,
            "cover_url": "http://tk/cover.jpg",
            "duration": 17,
            "metadata_source": "yt_dlp",
        },
    )

    url = "https://www.tiktok.com/@kevinpoterfield/video/7650894465690766623?is_from_webapp=1"
    r = client.post("/works/resolve", json={"text": url})

    assert r.status_code == 200
    body = r.json()
    assert body["platform"] == "tiktok"
    assert body["aweme_id"] == "7650894465690766623"
    assert body["share_url"].startswith("https://www.tiktok.com/@kevinpoterfield/video/")
    assert body["title"] == "TK title"
    assert body["hashtags"] == ["tag"]
    assert body["digg"] == 12
    assert body["comment"] == 3
    assert body["share"] == 2
    assert body["collect"] == 8
    assert body["cover_url"] == "http://tk/cover.jpg"
    assert body["author"]["platform"] == "tiktok"
    assert body["author"]["unique_id"] == "kevinpoterfield"
    assert body["cached"] is False

    from ncds_opus_factory.common import works_repo
    assert works_repo.load_card("tiktok", "7650894465690766623")["platform"] == "tiktok"


def test_resolve_work_refreshes_old_tiktok_lightweight_cache(client_env, monkeypatch):
    """旧缓存 TK 轻量卡没有 metadata_source 时，resolve 会补一次 metadata 并覆盖 card。"""
    client, works_mod = client_env
    from ncds_opus_factory.common import works_repo

    aid = "7650894465690766623"
    old = {
        "platform": "tiktok",
        "aweme_id": aid,
        "share_url": f"https://www.tiktok.com/@kevinpoterfield/video/{aid}",
        "title": f"TikTok 作品 {aid}",
        "hashtags": [],
        "digg": 0,
        "comment": 0,
        "share": 0,
        "collect": 0,
        "cover_url": "",
        "author": {
            "platform": "tiktok",
            "sec_uid": "",
            "nickname": "kevinpoterfield",
            "unique_id": "kevinpoterfield",
            "avatar": "",
        },
    }
    works_repo.save_card("tiktok", aid, old)
    monkeypatch.setattr(
        works_mod.tikhub_client, "fetch_video_ref_meta",
        lambda _ref: {
            "desc": "Fresh TK",
            "author": "kevinpoterfield",
            "hashtags": [],
            "digg": 9,
            "cover_url": "http://tk/new.jpg",
            "metadata_source": "yt_dlp",
        },
    )

    r = client.post("/works/resolve", json={"text": old["share_url"]})

    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is True
    assert body["title"] == "Fresh TK"
    assert body["cover_url"] == "http://tk/new.jpg"
    assert works_repo.load_card("tiktok", aid)["metadata_source"] == "yt_dlp"


def test_resolve_work_refreshes_tiktok_numeric_author_cache(client_env, monkeypatch):
    """TikTok 旧缓存若把纯数字内部 id 当作者名，resolve 会补一次 metadata。"""
    client, works_mod = client_env
    from ncds_opus_factory.common import works_repo

    aid = "7596952383146443789"
    old = {
        "platform": "tiktok",
        "aweme_id": aid,
        "share_url": f"https://www.tiktok.com/@freshfinds/video/{aid}",
        "title": "Old TK",
        "hashtags": [],
        "cover_url": "http://tk/old.jpg",
        "author": {
            "platform": "tiktok",
            "sec_uid": "",
            "nickname": aid,
            "unique_id": aid,
            "avatar": "",
        },
        "metadata_source": "yt_dlp",
    }
    works_repo.save_card("tiktok", aid, old)
    monkeypatch.setattr(
        works_mod.tikhub_client, "fetch_video_ref_meta",
        lambda _ref: {
            "desc": "Fresh TK",
            "author": "Fresh Finds",
            "hashtags": [],
            "cover_url": "http://tk/new.jpg",
            "metadata_source": "yt_dlp",
        },
    )

    r = client.post("/works/resolve", json={"text": old["share_url"]})

    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is True
    assert body["author"]["nickname"] == "Fresh Finds"
    assert works_repo.load_card("tiktok", aid)["author"]["nickname"] == "Fresh Finds"


def test_resolve_work_youtube_lightweight_card_without_tikhub(client_env, monkeypatch):
    """YouTube 单视频链接：识别并缓存，等待沈括采集阶段用 yt-dlp 下载听写。"""
    client, works_mod = client_env

    monkeypatch.setattr(
        works_mod.tikhub_client, "fetch_one_video_detail",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("YouTube 不该打抖音详情接口")),
    )
    monkeypatch.setattr(
        works_mod.tikhub_client, "fetch_video_ref_meta",
        lambda _ref: {
            "desc": "YT title",
            "author": "yt_author",
            "hashtags": [],
            "cover_url": "http://yt/cover.jpg",
            "metadata_source": "yt_dlp",
        },
    )

    r = client.post("/works/resolve", json={"text": "https://youtu.be/dQw4w9WgXcQ?si=x"})

    assert r.status_code == 200
    body = r.json()
    assert body["platform"] == "youtube"
    assert body["aweme_id"] == "dQw4w9WgXcQ"
    assert body["share_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert body["title"] == "YT title"
    assert body["cover_url"] == "http://yt/cover.jpg"
    assert body["author"]["platform"] == "youtube"
    assert body["cached"] is False


def test_resolve_work_youtube_shorts_falls_back_when_metadata_blocked(client_env, monkeypatch):
    """YouTube Shorts URL：yt-dlp metadata 被反爬拦住时也返回轻量卡片，不让解析失败。"""
    client, works_mod = client_env

    monkeypatch.setattr(
        works_mod.tikhub_client, "fetch_one_video_detail",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("YouTube 不该打抖音详情接口")),
    )
    monkeypatch.setattr(
        works_mod.tikhub_client, "fetch_video_ref_meta",
        lambda _ref: (_ for _ in ()).throw(RuntimeError("Sign in to confirm you’re not a bot")),
    )

    r = client.post(
        "/works/resolve",
        json={"text": "https://youtube.com/shorts/9fGlP0W09Fk?si=eRb5hoOiV8NN0zhX"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["platform"] == "youtube"
    assert body["aweme_id"] == "9fGlP0W09Fk"
    assert body["share_url"] == "https://www.youtube.com/watch?v=9fGlP0W09Fk"
    assert body["title"] == "YouTube 作品 9fGlP0W09Fk"
    assert body["cached"] is False


def test_set_work_domain_unknown_key_422(client_env):
    """PATCH domain：传未知 key 返回 422，不落盘。"""
    client, _ = client_env
    r = client.patch(f"/works/douyin/{AID}/domain", json={"domain": "sports"})
    assert r.status_code == 422
    from ncds_opus_factory.common import works_repo
    # manifest 可能不存在（新作品）或不含 domain（已有作品但本 key 未写入）
    assert works_repo.load_domain("douyin", AID) is None


def test_set_work_domain_known_keys(client_env):
    """PATCH domain：已注册的合法 key 均可写入。"""
    client, _ = client_env
    for key in ("finance", "emotion", "film", "comedy"):
        r = client.patch(f"/works/douyin/{AID}/domain", json={"domain": key})
        assert r.status_code == 200, f"key={key} 应 200, 实际 {r.status_code}: {r.text}"
        from ncds_opus_factory.common import works_repo
        assert works_repo.load_domain("douyin", AID) == key


def test_resolve_work_single_flight(client_env, monkeypatch):
    """并发解析同一作品（同 key）只打一次 TikHub（per-key 锁 + 锁内 double-check 缓存）。"""
    client, works_mod = client_env
    calls = {"n": 0}
    clock = threading.Lock()

    def fake(aweme_id, token=None):
        with clock:
            calls["n"] += 1
        time.sleep(0.15)  # 放大窗口，让其它线程在锁外等待
        return FAKE_DETAIL

    monkeypatch.setattr(works_mod.tikhub_client, "fetch_one_video_detail", fake)

    results: list[int] = []
    rlock = threading.Lock()

    def worker():
        resp = client.post("/works/resolve", json={"text": AID})
        with rlock:
            results.append(resp.status_code)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [200] * 6
    assert calls["n"] == 1  # single-flight：6 个并发只打一次
