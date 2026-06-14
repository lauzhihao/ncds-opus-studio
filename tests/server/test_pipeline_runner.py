"""tests：pipeline_runner 的 opus polish 幂等。

_polish_transcript_with_opus 用「转写内容 + title_hint」的 sha256 当缓存键，落 sidecar
<article>.src-sha256；同源第二次命中即跳过、不再调 opus（返回 False）。打桩 subprocess.run
模拟 claude CLI 的 JSON 输出，计数真实调用次数。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ncds_opus_factory.server import pipeline_runner as pr


class _FakeProc:
    returncode = 0
    stderr = ""

    def __init__(self, result_text: str):
        self.stdout = json.dumps(
            {"type": "result", "is_error": False, "result": result_text}
        ) + "\n"


def test_polish_transcript_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    transcript = tmp_path / "t.txt"
    transcript.write_text("这是一段语音转写得到的原稿内容,足够长。", encoding="utf-8")
    article = tmp_path / "article.md"
    sha = tmp_path / "article.md.src-sha256"

    calls = {"n": 0}

    def fake_run(args, **kw):
        calls["n"] += 1
        return _FakeProc("# 整理后\n正文")

    monkeypatch.setattr(pr.subprocess, "run", fake_run)

    # 首次:真调 opus,产出 article.md + 指纹 sidecar
    assert pr._polish_transcript_with_opus(
        transcript_path=transcript, output_path=article, title_hint="标题") is True
    assert article.read_text(encoding="utf-8").startswith("# 整理后")
    assert sha.is_file()
    assert calls["n"] == 1

    # 第二次:同转写+同标题 -> 幂等命中,不再调 opus
    assert pr._polish_transcript_with_opus(
        transcript_path=transcript, output_path=article, title_hint="标题") is False
    assert calls["n"] == 1

    # title_hint 变 -> 重新整理
    assert pr._polish_transcript_with_opus(
        transcript_path=transcript, output_path=article, title_hint="新标题") is True
    assert calls["n"] == 2

    # 转写内容变(模拟重转写出不同文本)-> 重新整理
    transcript.write_text("完全不同的另一段转写内容。", encoding="utf-8")
    assert pr._polish_transcript_with_opus(
        transcript_path=transcript, output_path=article, title_hint="新标题") is True
    assert calls["n"] == 3


def test_polish_transcript_repolishes_when_article_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """指纹在但 article.md 被删 -> 不能误命中,要重新整理。"""
    transcript = tmp_path / "t.txt"
    transcript.write_text("一段原稿。", encoding="utf-8")
    article = tmp_path / "article.md"

    calls = {"n": 0}

    def fake_run(args, **kw):
        calls["n"] += 1
        return _FakeProc("正文")

    monkeypatch.setattr(pr.subprocess, "run", fake_run)

    assert pr._polish_transcript_with_opus(
        transcript_path=transcript, output_path=article, title_hint="") is True
    article.unlink()  # 删掉成稿,只留 sidecar
    assert pr._polish_transcript_with_opus(
        transcript_path=transcript, output_path=article, title_hint="") is True
    assert calls["n"] == 2
