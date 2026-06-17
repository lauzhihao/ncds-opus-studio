"""沈括统一走采集后的回归测试：

- ``collect_one`` 的 ``do_audio/do_frames`` 开关（web 快采趟跳过重活、后台第二趟补）；
- ``_rw_source_text``（下游 rw 从 collected 内嵌清洗稿取数，缺失回退 legacy article 文件）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ncds_opus_factory.commands import shenkuo
from ncds_opus_factory.server.pipeline_runner import _rw_source_text


@pytest.fixture(autouse=True)
def _disable_ytdlp(monkeypatch):
    """禁用 yt-dlp 优先,让下载走 TikHub 兜底,复用现有 tikhub_client 打桩。"""
    monkeypatch.setattr(shenkuo.capabilities.download, "_ytdlp_download", lambda *a, **k: None)


def _works_env(tmp_path, monkeypatch):
    """works 仓库根随 NOF_STATE_DIR -> tmp，离线隔离（同 shenkuo_test._works_env）。"""
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.setattr(shenkuo, "COLLECTED", tmp_path / "figure_collected")


def _fake_dl(url, out, **k):
    Path(out).write_bytes(b"MP4")
    return str(out)


def test_collect_one_skip_audio_frames(tmp_path, monkeypatch):
    """web 快采趟 do_audio/do_frames=False：只下载+转写，跳过 Demucs 音轨分离与抠图重活。"""
    _works_env(tmp_path, monkeypatch)
    author_dir = tmp_path / "author_x"
    author_dir.mkdir()

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_video_url", lambda a, token=None: "http://v/x.mp4")
    monkeypatch.setattr(shenkuo.tikhub_client, "download_video", _fake_dl)
    monkeypatch.setattr(shenkuo.capabilities, "transcribe", lambda v, op: ({"task": "x"}, "转写文案"))
    monkeypatch.setattr(shenkuo.capabilities, "clean_transcript", lambda raw, op=shenkuo._noop: None)

    def boom(*a, **k):
        raise AssertionError("快采不该跑音轨/抠图重活")

    monkeypatch.setattr(shenkuo.capabilities, "separate_audio", boom)
    monkeypatch.setattr(shenkuo.capabilities, "extract_frames", boom)
    monkeypatch.setattr(shenkuo.capabilities, "cutout", boom)

    entry = shenkuo.collect_one(
        "456", author_dir, meta={"desc": "d", "digg": 7}, top_comments=0,
        do_audio=False, do_frames=False,
    )
    assert entry["status"]["download"] == "ok"
    assert entry["status"]["transcribe"] == "ok"
    assert entry["text"] == "转写文案"   # 快采产出文案（喂下游 rw + 抽屉展示）
    assert "audio" not in entry          # branch_audio 未跑
    assert "frames" not in entry         # branch_frames 未跑
    assert "cutouts" not in entry


def test_collect_one_second_pass_fills_audio(tmp_path, monkeypatch):
    """后台第二趟 do_audio/do_frames=True：转写已 cached 跳过（幂等），只补音轨。"""
    _works_env(tmp_path, monkeypatch)
    author_dir = tmp_path / "author_x"
    author_dir.mkdir()
    aid = "789"
    transcribe_calls = {"n": 0}

    def fake_transcribe(v, op):
        transcribe_calls["n"] += 1
        return ({"task": "x"}, "转写文案")

    def fake_sep(v, od, op=shenkuo._noop, check=None):
        od.mkdir(parents=True, exist_ok=True)
        out = {}
        for n in ("original", "vocals", "bgm"):
            p = od / f"{n}.mp3"
            p.write_bytes(b"a")
            out[n] = p
        return out

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_video_url", lambda a, token=None: "http://v/x.mp4")
    monkeypatch.setattr(shenkuo.tikhub_client, "download_video", _fake_dl)
    monkeypatch.setattr(shenkuo.capabilities, "transcribe", fake_transcribe)
    monkeypatch.setattr(shenkuo.capabilities, "clean_transcript", lambda raw, op=shenkuo._noop: None)
    monkeypatch.setattr(shenkuo.capabilities, "separate_audio", fake_sep)
    monkeypatch.setattr(shenkuo.capabilities, "extract_frames", lambda v, od, mf: [])
    monkeypatch.setattr(shenkuo.capabilities, "cutout", lambda frames, od, **k: [])

    # 第一趟快采（不跑音轨/抠图）
    shenkuo.collect_one(aid, author_dir, meta={"desc": "d"}, top_comments=0, do_audio=False, do_frames=False)
    assert transcribe_calls["n"] == 1
    # 第二趟补音轨：转写 cached 不重跑，audio 落位
    entry2 = shenkuo.collect_one(aid, author_dir, meta={}, top_comments=0, do_audio=True, do_frames=True)
    assert transcribe_calls["n"] == 1   # 幂等：转写没重跑
    assert entry2.get("audio") and "vocals" in entry2["audio"]


def test_rw_source_text_prefers_collected_text(tmp_path):
    """rw 取数：优先 collected 内嵌清洗稿 text，标题取 desc 并剥掉内嵌话题。"""
    items = [
        {"index": 1, "desc": "标题A #话题x", "hashtags": ["话题x"], "text": "清洗稿正文A"},
        {"index": 2, "desc": "标题B", "text": ""},  # 空 text 且无文件 → 跳过
    ]
    src = _rw_source_text(items, tmp_path)
    assert "## 来源 1 - 标题A" in src
    assert "#话题x" not in src           # 话题被剥
    assert "清洗稿正文A" in src
    assert "标题B" not in src             # 空 text 跳过


def test_rw_source_text_falls_back_to_article_file(tmp_path):
    """兼容 legacy：collected 无 text 时回退读 article_relpath 文件。"""
    art = tmp_path / "01_asr" / "1" / "article.md"
    art.parent.mkdir(parents=True)
    art.write_text("legacy 文章正文", encoding="utf-8")
    items = [{"index": 1, "title": "旧标题", "article_relpath": "01_asr/1/article.md"}]
    src = _rw_source_text(items, tmp_path)
    assert "legacy 文章正文" in src
    assert "旧标题" in src
