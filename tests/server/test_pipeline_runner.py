"""tests：pipeline_runner 的 opus polish 幂等。

_polish_transcript_with_opus 用「转写内容 + title_hint」的 sha256 当缓存键，落 sidecar
<article>.src-sha256；同源第二次命中即跳过、不再调 opus（返回 False）。打桩 subprocess.run
模拟 claude CLI 的 JSON 输出，计数真实调用次数。
"""

from __future__ import annotations

import asyncio
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


def test_asr_stage_label_accepts_ascii_and_legacy_transcribe_progress():
    assert pr._asr_stage_label("[OK] 转写: /tmp/job/raw/demo.txt") == "语音转写"
    assert pr._asr_stage_label("\u2705 转写: /tmp/job/raw/demo.txt") == "语音转写"
    assert pr._asr_stage_label("[DL] 下载中...") == "下载视频"


def test_call_opus_for_rw_delegates_to_common_call_opus(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_call_opus(prompt: str, **kwargs) -> str:
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(pr, "call_opus", fake_call_opus)

    assert pr._call_opus_for_rw("user", "system", "model-x") == "ok"
    assert captured["prompt"] == "user"
    assert captured["kwargs"]["system_prompt"] == "system"
    assert captured["kwargs"]["model"] == "model-x"
    assert captured["kwargs"]["timeout_seconds"] == pr.RW_LLM_TIMEOUT_SEC


def test_rw_model_helpers_assert_known_model_and_mock_short_circuit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    cand = runner._assert_known_model("opus")
    assert cand["id"] == "opus"
    with pytest.raises(KeyError, match="unknown model: missing"):
        runner._assert_known_model("missing")

    async def no_delay() -> None:
        return None

    emitted: list[dict] = []
    monkeypatch.setattr(runner, "_mock_regen_delay", no_delay)
    monkeypatch.setattr(runner, "_emit", lambda _job_id, event: emitted.append(event))

    state = pr.JobState(
        job_id="j1",
        pipeline_id="paper_card_talk_015",
        title="t",
        created_at=1.0,
        updated_at=1.0,
        nodes={"rw": pr.NodeState(name="rw", status="done")},
        mock=True,
    )

    assert asyncio.run(runner._rw_mock_short_circuit(state, "j1", "opus")) is True
    assert emitted[-1]["type"] == "node_status"
    assert emitted[-1]["node"] == "rw"
    with pytest.raises(KeyError, match="unknown model: missing"):
        asyncio.run(runner._rw_mock_short_circuit(state, "j1", "missing"))

    state.mock = False
    assert asyncio.run(runner._rw_mock_short_circuit(state, "j1", "missing")) is False


# --- 鬼谷子选题：job inputs 的 domain 透传到 guiguzi（task-2.5 调用方线程化）------ #
def test_guiguzi_threads_domain_from_job_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """job inputs 带 domain（前端 doCreate 写入）时，analyze/generate 两个 bg 都把它透传给鬼谷子。"""
    import asyncio
    from ncds_opus_factory.commands import guiguzi as gz

    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("paper_card_talk_015", "t", {"domain": "finance"})
    seen: dict[str, object] = {}

    def fake_analyze(items, on_progress=gz._noop, require_comment=True, domain=None, **kw):
        seen["analyze_domain"] = domain
        return {"opus": {"analysis": {"hook_reason": "x"}, "error": None}}

    def fake_generate(items, analysis=None, prompt=None, on_progress=gz._noop,
                      require_comment=True, domain=None, **kw):
        seen["generate_domain"] = domain
        return {"candidates": {}, "topics": [], "out": None, "added": 0, "prompt": ""}

    monkeypatch.setattr(gz, "analyze", fake_analyze)
    monkeypatch.setattr(gz, "generate_topics", fake_generate)

    items = [{"text": "原文", "comment": "评论"}]
    asyncio.run(runner._run_guiguzi_analyze_bg(job.job_id, items))
    asyncio.run(runner._run_guiguzi_generate_bg(
        job.job_id, items, items, {"opus": [], "deepseek": []}, None, None))

    assert seen["analyze_domain"] == "finance"
    assert seen["generate_domain"] == "finance"


def test_guiguzi_no_domain_passes_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """job inputs 无 domain 时透传 None，鬼谷子回退通用 prompt（不预设赛道）。"""
    import asyncio
    from ncds_opus_factory.commands import guiguzi as gz

    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("paper_card_talk_015", "t", {})
    seen: dict[str, object] = {}

    def fake_analyze(items, on_progress=gz._noop, require_comment=True, domain=None, **kw):
        seen["domain"] = domain
        return {"opus": {"analysis": {}, "error": None}}

    monkeypatch.setattr(gz, "analyze", fake_analyze)
    asyncio.run(runner._run_guiguzi_analyze_bg(job.job_id, [{"text": "t", "comment": "c"}]))
    assert seen["domain"] is None
