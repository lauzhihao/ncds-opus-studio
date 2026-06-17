"""下载策略单测：yt-dlp 匿名优先 + TikHub 兜底(全打桩,离线可跑,绝不真跑 yt-dlp/网络)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ncds_opus_factory.common import cancel
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


def _raise_cancelled(*_a, **_k):
    raise cancel.TaskCancelled("cancelled")


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
# fetch_and_download：yt-dlp 优先 -> TikHub 兜底 的编排
# --------------------------------------------------------------------------- #
def test_prefers_ytdlp_skips_tikhub(tmp_path, monkeypatch):
    """yt-dlp 成功 -> 直接返回,绝不调 TikHub(省付费调用)。"""
    out = tmp_path / "v.mp4"
    monkeypatch.setattr(download, "_ytdlp_download", lambda *a, **k: str(out))

    def boom(*a, **k):
        raise AssertionError("yt-dlp 成功不该走 TikHub")

    monkeypatch.setattr(download.tikhub_client, "fetch_video_url", boom)
    monkeypatch.setattr(download.tikhub_client, "download_video", boom)
    assert download.fetch_and_download("123", out) == str(out)


def test_falls_back_to_tikhub_when_ytdlp_none(tmp_path, monkeypatch):
    """yt-dlp 没出片(None) -> 回退 TikHub fetch_video_url + download_video。"""
    out = tmp_path / "v.mp4"
    monkeypatch.setattr(download, "_ytdlp_download", lambda *a, **k: None)
    monkeypatch.setattr(download.tikhub_client, "fetch_video_url", lambda a, token=None: "http://v/x.mp4")

    def fake_dl(url, o, **k):
        Path(o).write_bytes(b"MP4")
        return str(o)

    monkeypatch.setattr(download.tikhub_client, "download_video", fake_dl)
    assert download.fetch_and_download("123", out) == str(out)
    assert out.read_bytes() == b"MP4"


def test_falls_back_on_ytdlp_exception(tmp_path, monkeypatch):
    """yt-dlp 抛普通异常 -> 不冒泡,回退 TikHub。"""
    out = tmp_path / "v.mp4"

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
    monkeypatch.setattr(download, "_ytdlp_download", _raise_cancelled)

    def boom(*a, **k):
        raise AssertionError("取消不该回退 TikHub")

    monkeypatch.setattr(download.tikhub_client, "fetch_video_url", boom)
    with pytest.raises(cancel.TaskCancelled):
        download.fetch_and_download("123", tmp_path / "v.mp4")


def test_raises_when_both_fail(tmp_path, monkeypatch):
    """yt-dlp None + TikHub 也拿不到地址 -> 抛 RuntimeError(由调用方降级)。"""
    monkeypatch.setattr(download, "_ytdlp_download", lambda *a, **k: None)
    monkeypatch.setattr(download.tikhub_client, "fetch_video_url", lambda a, token=None: None)
    with pytest.raises(RuntimeError):
        download.fetch_and_download("123", tmp_path / "v.mp4")
