"""单作品链接解析：Douyin / TikTok / YouTube 平台感知。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from ncds_opus_factory.common import tikhub_client


def test_resolve_video_ref_tiktok_direct_url():
    ref = tikhub_client.resolve_video_ref(
        "https://www.tiktok.com/@kevinpoterfield/video/7650894465690766623"
        "?is_from_webapp=1&sender_device=pc"
    )

    assert ref is not None
    assert ref.platform == "tiktok"
    assert ref.video_id == "7650894465690766623"
    assert ref.url.startswith("https://www.tiktok.com/@kevinpoterfield/video/")


def test_resolve_video_ref_tiktok_short_redirect(monkeypatch):
    class Resp:
        url = "https://www.tiktok.com/@some.user/video/7650894465690766623"

    monkeypatch.setattr(tikhub_client.requests, "get", lambda *a, **k: Resp())

    ref = tikhub_client.resolve_video_ref("https://vm.tiktok.com/ZMshort/")

    assert ref is not None
    assert ref.platform == "tiktok"
    assert ref.video_id == "7650894465690766623"
    assert ref.url == Resp.url


def test_resolve_video_ref_youtube_variants():
    watch = tikhub_client.resolve_video_ref("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s")
    short = tikhub_client.resolve_video_ref("youtu.be/dQw4w9WgXcQ?si=x")
    shorts = tikhub_client.resolve_video_ref("https://youtube.com/shorts/AbCdEf_1234?feature=share")
    user_short = tikhub_client.resolve_video_ref(
        "https://youtube.com/shorts/9fGlP0W09Fk?si=eRb5hoOiV8NN0zhX"
    )

    assert watch is not None and watch.platform == "youtube"
    assert watch.video_id == "dQw4w9WgXcQ"
    assert watch.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert short is not None and short.video_id == "dQw4w9WgXcQ"
    assert shorts is not None and shorts.video_id == "AbCdEf_1234"
    assert user_short is not None and user_short.video_id == "9fGlP0W09Fk"


def test_resolve_video_ref_douyin_pure_id_and_ignores_unknown_host(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("unknown host must not trigger redirect/network parsing")

    monkeypatch.setattr(tikhub_client.requests, "get", boom)

    ref = tikhub_client.resolve_video_ref("7123456789012345678")
    assert ref is not None
    assert ref.platform == "douyin"
    assert ref.video_id == "7123456789012345678"
    assert tikhub_client.resolve_video_ref("https://example.com/video/7650894465690766623") is None


def test_simplify_ytdlp_entry_builds_platform_share_url():
    item = tikhub_client._simplify_ytdlp_entry(
        "tiktok",
        "creator",
        {"id": "7650894465690766623", "title": "TK title", "like_count": 12, "save_count": 8},
    )

    assert item is not None
    assert item["platform"] == "tiktok"
    assert item["aweme_id"] == "7650894465690766623"
    assert item["desc"] == "TK title"
    assert item["digg"] == 12
    assert item["collect"] == 8
    assert item["share_url"] == "https://www.tiktok.com/@creator/video/7650894465690766623"


def test_simplify_ytdlp_entry_does_not_map_view_count_to_digg():
    item = tikhub_client._simplify_ytdlp_entry(
        "tiktok",
        "creator",
        {"id": "7650894465690766623", "title": "TK title", "view_count": 999},
        keep_missing_stats=False,
    )

    assert item is not None
    assert "digg" not in item


def test_video_ref_author_prefers_readable_tiktok_author_over_numeric_id():
    ref = tikhub_client.VideoRef(
        "tiktok",
        "7596952383146443789",
        "https://www.tiktok.com/@freshfinds/video/7596952383146443789",
    )

    author = tikhub_client._video_ref_author(
        ref,
        {"uploader_id": "7596952383146443789", "uploader": "Fresh Finds"},
    )

    assert author == "Fresh Finds"


def test_fetch_video_ref_meta_uses_ytdlp_without_downloading(monkeypatch):
    ref = tikhub_client.VideoRef(
        "tiktok",
        "7650894465690766623",
        "https://www.tiktok.com/@creator/video/7650894465690766623",
    )
    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download=False):
            seen["url"] = url
            seen["download"] = download
            return {
                "id": "7650894465690766623",
                "title": "TK title #tag",
                "like_count": 12,
                "comment_count": 3,
                "share_count": 2,
                "save_count": 8,
                "thumbnail": "http://thumb/cover.jpg",
                "duration": 17,
                "uploader_id": "creator",
                "view_count": 999,
            }

    monkeypatch.setattr(tikhub_client.importlib.util, "find_spec", lambda name: object() if name == "yt_dlp" else None)
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))

    meta = tikhub_client.fetch_video_ref_meta(ref)

    assert seen == {
        "opts": {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "ignoreerrors": False,
            "noplaylist": True,
        },
        "url": ref.url,
        "download": False,
    }
    assert meta["desc"] == "TK title #tag"
    assert meta["hashtags"] == ["tag"]
    assert meta["digg"] == 12
    assert meta["comment"] == 3
    assert meta["share"] == 2
    assert meta["collect"] == 8
    assert meta["cover_url"] == "http://thumb/cover.jpg"
    assert meta["duration"] == 17
    assert meta["metadata_source"] == "yt_dlp"


def test_fetch_tiktok_video_url_uses_share_url_and_extracts_play_addr(monkeypatch):
    seen = {}

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 200,
                "data": {
                    "aweme_detail": {
                        "video": {
                            "cover": {"url_list": ["https://example.com/cover.jpg"]},
                            "download_addr": {"url_list": ["https://example.com/watermark.mp4"]},
                            "play_addr": {
                                "url_list": [
                                    "https:\\/\\/v16-webapp-prime.tiktok.com\\/video\\/tos\\/useast2a\\/video.mp4?mime_type=video_mp4"
                                ]
                            },
                        }
                    }
                },
            }

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["params"] = params
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(tikhub_client, "get_token", lambda token=None: "tok")
    monkeypatch.setattr(tikhub_client.requests, "get", fake_get)

    url = tikhub_client.fetch_tiktok_video_url(
        "7650894465690766623",
        source_url="https://www.tiktok.com/@creator/video/7650894465690766623",
    )

    assert seen["url"] == tikhub_client.TIKTOK_ONE_VIDEO_BY_SHARE_URL
    assert seen["headers"] == {"Authorization": "Bearer tok"}
    assert seen["params"] == {
        "share_url": "https://www.tiktok.com/@creator/video/7650894465690766623"
    }
    assert url == "https://v16-webapp-prime.tiktok.com/video/tos/useast2a/video.mp4?mime_type=video_mp4"


def test_fetch_tiktok_video_url_falls_back_to_aweme_id_endpoint(monkeypatch):
    seen = {}

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "item": {
                        "video": {
                            "playAddr": "https://www.tiktok.com/aweme/v1/play/?video_id=abc&mime_type=video_mp4"
                        }
                    }
                }
            }

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["url"] = url
        seen["params"] = params
        return Resp()

    monkeypatch.setattr(tikhub_client, "get_token", lambda token=None: "tok")
    monkeypatch.setattr(tikhub_client.requests, "get", fake_get)

    url = tikhub_client.fetch_tiktok_video_url("7650894465690766623")

    assert seen["url"] == tikhub_client.TIKTOK_ONE_VIDEO_URL
    assert seen["params"] == {"aweme_id": "7650894465690766623"}
    assert url == "https://www.tiktok.com/aweme/v1/play/?video_id=abc&mime_type=video_mp4"
