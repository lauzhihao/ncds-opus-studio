"""下载策略单测：DouK sidecar + yt-dlp 匿名 + TikHub 兜底(全打桩,离线可跑)。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ncds_opus_core.common import cancel
from ncds_opus_factory.common.capabilities import download


class _FakePopen:
    """假 subprocess：poll 立即给 rc(可顺带造产物);never_finish=True 模拟卡住(测取消)。"""

    def __init__(self, rc: int = 0, produce=None, never_finish: bool = False):
        self.rc = rc
        self.produce = produce
        self.never_finish = never_finish
        self.terminated = False
        self.killed = False

    def poll(self):
        if self.never_finish:
            return None
        if self.produce:
            self.produce()
            self.produce = None
        return self.rc

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.rc


class _FakeResponse:
    def __init__(self, status_code: int = 200, data=None, chunks=None):
        self.status_code = status_code
        self._data = data or {}
        self._chunks = chunks or []

    def json(self):
        return self._data

    def iter_content(self, chunk_size=1024 * 1024):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _raise_cancelled(*_a, **_k):
    raise cancel.TaskCancelled("cancelled")


# --------------------------------------------------------------------------- #
# _douk_http_download：独立 DouK sidecar HTTP client
# --------------------------------------------------------------------------- #
def test_douk_http_download_success(tmp_path, monkeypatch):
    """配置 NOF_DOUK_ENDPOINT 后，优先调用 sidecar 并把 artifact 拉回本地。"""
    out = tmp_path / "video.mp4"
    body = b"MP4"
    monkeypatch.setenv("NOF_DOUK_ENDPOINT", "http://douk.local")
    monkeypatch.setattr(
        download.requests,
        "post",
        lambda *a, **k: _FakeResponse(
            data={
                "ok": True,
                "file_url": "/v1/artifacts/job/video.mp4",
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        ),
    )
    monkeypatch.setattr(download.requests, "get", lambda *a, **k: _FakeResponse(chunks=[body]))

    assert download._douk_http_download("123", out) == str(out)
    assert out.read_bytes() == body
    assert not list(tmp_path.glob("*.douk.part"))


def test_prefers_douk_skips_ytdlp_and_tikhub(tmp_path, monkeypatch):
    """DouK sidecar 成功 -> 直接返回，不再调 yt-dlp/TikHub。"""
    out = tmp_path / "video.mp4"
    body = b"MP4"
    monkeypatch.setenv("NOF_DOUK_ENDPOINT", "http://douk.local")
    monkeypatch.setattr(
        download.requests,
        "post",
        lambda *a, **k: _FakeResponse(
            data={
                "ok": True,
                "file_url": "http://douk.local/v1/artifacts/job/video.mp4",
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        ),
    )
    monkeypatch.setattr(download.requests, "get", lambda *a, **k: _FakeResponse(chunks=[body]))

    def boom(*a, **k):
        raise AssertionError("DouK 成功不该走后续下载器")

    monkeypatch.setattr(download, "_ytdlp_download", boom)
    monkeypatch.setattr(download.tikhub_client, "fetch_video_url", boom)
    monkeypatch.setattr(download.tikhub_client, "download_video", boom)
    assert download.fetch_and_download("123", out) == str(out)
    assert out.read_bytes() == body


def test_douk_failure_falls_back_to_ytdlp(tmp_path, monkeypatch):
    """DouK sidecar 失败(None) -> 继续走 yt-dlp。"""
    out = tmp_path / "video.mp4"
    monkeypatch.setattr(download, "_douk_http_download", lambda *a, **k: None)
    monkeypatch.setattr(download, "_ytdlp_download", lambda *a, **k: str(out))
    assert download.fetch_and_download("123", out) == str(out)


def test_douk_passes_platform_source_url_and_deployment_options(tmp_path, monkeypatch):
    """TK 请求会把平台、原始链接、代理、token、device_id 传给 sidecar。"""
    out = tmp_path / "video.mp4"
    body = b"MP4"
    seen = {}
    monkeypatch.setenv("NOF_DOUK_ENDPOINT", "http://douk.local")
    monkeypatch.setenv("NOF_DOUK_TIKTOK_PROXY", "http://127.0.0.1:10808")
    monkeypatch.setenv("NOF_DOUK_TIKTOK_DEVICE_ID", "device-1")
    monkeypatch.setenv("NOF_DOUK_TOKEN", "secret")

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["post_url"] = url
        seen["payload"] = json
        seen["headers"] = headers
        seen["timeout"] = timeout
        return _FakeResponse(
            data={
                "ok": True,
                "file_url": "/v1/artifacts/job/video.mp4",
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )

    def fake_get(url, headers=None, stream=False, timeout=None):
        seen["get_url"] = url
        seen["get_headers"] = headers
        seen["stream"] = stream
        return _FakeResponse(chunks=[body])

    monkeypatch.setattr(download.requests, "post", fake_post)
    monkeypatch.setattr(download.requests, "get", fake_get)

    assert download.fetch_and_download(
        "7650894465690766623",
        out,
        platform="tiktok",
        source_url="https://www.tiktok.com/@u/video/7650894465690766623",
    ) == str(out)
    assert seen["post_url"] == "http://douk.local/v1/download"
    assert seen["payload"]["platform"] == "tiktok"
    assert seen["payload"]["video_id"] == "7650894465690766623"
    assert seen["payload"]["source_url"] == "https://www.tiktok.com/@u/video/7650894465690766623"
    assert seen["payload"]["proxy"] == "http://127.0.0.1:10808"
    assert seen["payload"]["device_id"] == "device-1"
    assert seen["headers"]["Authorization"] == "Bearer secret"
    assert seen["get_url"] == "http://douk.local/v1/artifacts/job/video.mp4"
    assert seen["get_headers"]["Authorization"] == "Bearer secret"
    assert seen["stream"] is True


# --------------------------------------------------------------------------- #
# _ytdlp_cmd：调用命令前缀解析(关键：.venv/bin 不在 PATH 时回退 python -m yt_dlp)
# --------------------------------------------------------------------------- #
def test_ytdlp_cmd_prefers_env(monkeypatch):
    monkeypatch.setenv("NOF_YT_DLP", "/opt/yt-dlp")
    assert download._ytdlp_cmd() == ["/opt/yt-dlp"]


def test_ytdlp_cmd_falls_back_to_module(monkeypatch):
    """PATH 无 yt-dlp 二进制 -> 回退当前解释器 python -m yt_dlp(venv 装了包但不在 PATH 的常态)。"""
    monkeypatch.delenv("NOF_YT_DLP", raising=False)
    monkeypatch.setattr(download.shutil, "which", lambda _: None)
    monkeypatch.setattr(download.importlib.util, "find_spec", lambda _: object())
    assert download._ytdlp_cmd() == [download.sys.executable, "-m", "yt_dlp"]


def test_ytdlp_cmd_none_when_unavailable(monkeypatch):
    monkeypatch.delenv("NOF_YT_DLP", raising=False)
    monkeypatch.setattr(download.shutil, "which", lambda _: None)
    monkeypatch.setattr(download.importlib.util, "find_spec", lambda _: None)
    assert download._ytdlp_cmd() is None


# --------------------------------------------------------------------------- #
# _ytdlp_download：底层 yt-dlp 子进程封装
# --------------------------------------------------------------------------- #
def test_ytdlp_download_success(tmp_path, monkeypatch):
    """yt-dlp 出片：临时产物 glob 命中 -> 原子替换到 out,残片清理,返回路径。"""
    out = tmp_path / "video.mp4"
    monkeypatch.setattr(download, "_ytdlp_cmd", lambda: ["/usr/bin/yt-dlp"])
    monkeypatch.setattr(
        download.subprocess, "Popen",
        lambda *a, **k: _FakePopen(rc=0, produce=lambda: (tmp_path / "video.ytdl.mp4").write_bytes(b"MP4")),
    )
    path = download._ytdlp_download("123", out)
    assert path == str(out)
    assert out.read_bytes() == b"MP4"
    assert not list(tmp_path.glob("*.ytdl.*"))  # 临时残片已清


def test_ytdlp_download_failure_returns_none(tmp_path, monkeypatch):
    """yt-dlp rc!=0 -> 返回 None(交给 TikHub 兜底),不留半成品。"""
    out = tmp_path / "video.mp4"
    monkeypatch.setattr(download, "_ytdlp_cmd", lambda: ["/usr/bin/yt-dlp"])
    monkeypatch.setattr(download.subprocess, "Popen", lambda *a, **k: _FakePopen(rc=1))
    assert download._ytdlp_download("123", out) is None
    assert not out.exists()


def test_ytdlp_download_no_binary_returns_none(tmp_path, monkeypatch):
    """没装 yt-dlp -> 直接 None,不尝试跑子进程。"""
    monkeypatch.setattr(download, "_ytdlp_cmd", lambda: None)
    assert download._ytdlp_download("123", tmp_path / "v.mp4") is None


def test_ytdlp_download_cancel(tmp_path, monkeypatch):
    """取消标记触发 -> SIGTERM 子进程并抛 TaskCancelled。"""
    fp = _FakePopen(never_finish=True)
    monkeypatch.setattr(download, "_ytdlp_cmd", lambda: ["/usr/bin/yt-dlp"])
    monkeypatch.setattr(download.subprocess, "Popen", lambda *a, **k: fp)
    monkeypatch.setattr(download.time, "sleep", lambda *_: None)
    with pytest.raises(cancel.TaskCancelled):
        download._ytdlp_download("123", tmp_path / "v.mp4", check=lambda: True)
    assert fp.terminated


# --------------------------------------------------------------------------- #
# fetch_and_download：DouK sidecar -> yt-dlp -> TikHub 兜底 的编排
# --------------------------------------------------------------------------- #
def test_prefers_ytdlp_skips_tikhub(tmp_path, monkeypatch):
    """yt-dlp 成功 -> 直接返回,绝不调 TikHub(省付费调用)。"""
    out = tmp_path / "v.mp4"
    monkeypatch.setattr(download, "_douk_http_download", lambda *a, **k: None)
    monkeypatch.setattr(download, "_ytdlp_download", lambda *a, **k: str(out))

    def boom(*a, **k):
        raise AssertionError("yt-dlp 成功不该走 TikHub")

    monkeypatch.setattr(download.tikhub_client, "fetch_video_url", boom)
    monkeypatch.setattr(download.tikhub_client, "download_video", boom)
    assert download.fetch_and_download("123", out) == str(out)


def test_fetch_and_download_passes_platform_and_source_url_to_ytdlp(tmp_path, monkeypatch):
    """TK/油管等外部平台要把原始链接传给 yt-dlp。"""
    out = tmp_path / "v.mp4"
    seen = {}
    monkeypatch.setattr(download, "_douk_http_download", lambda *a, **k: None)

    def fake_ytdlp(aweme_id, output, on_progress=download.noop, check=lambda: False, **kwargs):
        seen["aweme_id"] = aweme_id
        seen["output"] = output
        seen.update(kwargs)
        return str(out)

    monkeypatch.setattr(download, "_ytdlp_download", fake_ytdlp)

    assert download.fetch_and_download(
        "7650894465690766623",
        out,
        platform="tiktok",
        source_url="https://www.tiktok.com/@u/video/7650894465690766623",
    ) == str(out)
    assert seen["platform"] == "tiktok"
    assert seen["source_url"] == "https://www.tiktok.com/@u/video/7650894465690766623"


def test_falls_back_to_tikhub_when_ytdlp_none(tmp_path, monkeypatch):
    """yt-dlp 没出片(None) -> 回退 TikHub fetch_video_url + download_video。"""
    out = tmp_path / "v.mp4"
    monkeypatch.setattr(download, "_douk_http_download", lambda *a, **k: None)
    monkeypatch.setattr(download, "_ytdlp_download", lambda *a, **k: None)
    monkeypatch.setattr(download.tikhub_client, "fetch_video_url", lambda a, token=None: "http://v/x.mp4")

    def fake_dl(url, o, **k):
        Path(o).write_bytes(b"MP4")
        return str(o)

    monkeypatch.setattr(download.tikhub_client, "download_video", fake_dl)
    assert download.fetch_and_download("123", out) == str(out)
    assert out.read_bytes() == b"MP4"


def test_tiktok_falls_back_to_tikhub_when_douk_and_ytdlp_none(tmp_path, monkeypatch):
    """TK 在 DouK/yt-dlp 都没出片时，走 TikHub App V3 付费兜底。"""
    out = tmp_path / "v.mp4"
    seen = {}
    monkeypatch.setattr(download, "_douk_http_download", lambda *a, **k: None)
    monkeypatch.setattr(download, "_ytdlp_download", lambda *a, **k: None)

    def fake_fetch(video_id, source_url=None, token=None):
        seen["video_id"] = video_id
        seen["source_url"] = source_url
        seen["token"] = token
        return "https://v16-webapp-prime.tiktok.com/video/tos/x.mp4"

    def fake_dl(url, o, **k):
        seen["download_url"] = url
        Path(o).write_bytes(b"TKMP4")
        return str(o)

    monkeypatch.setattr(download.tikhub_client, "fetch_tiktok_video_url", fake_fetch)
    monkeypatch.setattr(download.tikhub_client, "download_video", fake_dl)

    assert download.fetch_and_download(
        "7650894465690766623",
        out,
        token="tok",
        platform="tiktok",
        source_url="https://www.tiktok.com/@creator/video/7650894465690766623",
    ) == str(out)
    assert out.read_bytes() == b"TKMP4"
    assert seen == {
        "video_id": "7650894465690766623",
        "source_url": "https://www.tiktok.com/@creator/video/7650894465690766623",
        "token": "tok",
        "download_url": "https://v16-webapp-prime.tiktok.com/video/tos/x.mp4",
    }


def test_non_douyin_does_not_fall_back_to_tikhub(tmp_path, monkeypatch):
    """非抖音作品 yt-dlp 失败后直接报错，不调用抖音 TikHub 兜底。"""
    monkeypatch.setattr(download, "_douk_http_download", lambda *a, **k: None)
    monkeypatch.setattr(download, "_ytdlp_download", lambda *a, **k: None)

    def boom(*a, **k):
        raise AssertionError("非抖音不该走 TikHub")

    monkeypatch.setattr(download.tikhub_client, "fetch_video_url", boom)
    monkeypatch.setattr(download.tikhub_client, "download_video", boom)

    with pytest.raises(RuntimeError, match="未配置该平台兜底"):
        download.fetch_and_download("dQw4w9WgXcQ", tmp_path / "v.mp4", platform="youtube")


def test_falls_back_on_ytdlp_exception(tmp_path, monkeypatch):
    """yt-dlp 抛普通异常 -> 不冒泡,回退 TikHub。"""
    out = tmp_path / "v.mp4"
    monkeypatch.setattr(download, "_douk_http_download", lambda *a, **k: None)

    def boom_ytdlp(*a, **k):
        raise RuntimeError("yt-dlp 崩了")

    monkeypatch.setattr(download, "_ytdlp_download", boom_ytdlp)
    monkeypatch.setattr(download.tikhub_client, "fetch_video_url", lambda a, token=None: "http://v/x.mp4")

    def fake_dl(url, o, **k):
        Path(o).write_bytes(b"MP4")
        return str(o)

    monkeypatch.setattr(download.tikhub_client, "download_video", fake_dl)
    assert download.fetch_and_download("123", out) == str(out)


def test_cancel_not_caught_no_fallback(tmp_path, monkeypatch):
    """yt-dlp 抛 TaskCancelled -> 冒泡中止,不回退 TikHub(取消是用户意图,不是失败)。"""
    monkeypatch.setattr(download, "_douk_http_download", lambda *a, **k: None)
    monkeypatch.setattr(download, "_ytdlp_download", _raise_cancelled)

    def boom(*a, **k):
        raise AssertionError("取消不该回退 TikHub")

    monkeypatch.setattr(download.tikhub_client, "fetch_video_url", boom)
    with pytest.raises(cancel.TaskCancelled):
        download.fetch_and_download("123", tmp_path / "v.mp4")


def test_raises_when_both_fail(tmp_path, monkeypatch):
    """yt-dlp None + TikHub 也拿不到地址 -> 抛 RuntimeError(由调用方降级)。"""
    monkeypatch.setattr(download, "_douk_http_download", lambda *a, **k: None)
    monkeypatch.setattr(download, "_ytdlp_download", lambda *a, **k: None)
    monkeypatch.setattr(download.tikhub_client, "fetch_video_url", lambda a, token=None: None)
    with pytest.raises(RuntimeError):
        download.fetch_and_download("123", tmp_path / "v.mp4")
