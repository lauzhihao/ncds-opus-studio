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

from ncds_opus_factory.server import pipeline_asr_helpers as asr_helpers
from ncds_opus_factory.server import pipeline_media_helpers as media_helpers
from ncds_opus_factory.server import pipeline_runner as pr
from ncds_opus_factory.server import pipeline_rw_helpers as rw_helpers


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

    monkeypatch.setattr(asr_helpers.subprocess, "run", fake_run)

    # 首次:真调 opus,产出 article.md + 指纹 sidecar
    assert asr_helpers._polish_transcript_with_opus(
        transcript_path=transcript, output_path=article, title_hint="标题") is True
    assert article.read_text(encoding="utf-8").startswith("# 整理后")
    assert sha.is_file()
    assert calls["n"] == 1

    # 第二次:同转写+同标题 -> 幂等命中,不再调 opus
    assert asr_helpers._polish_transcript_with_opus(
        transcript_path=transcript, output_path=article, title_hint="标题") is False
    assert calls["n"] == 1

    # title_hint 变 -> 重新整理
    assert asr_helpers._polish_transcript_with_opus(
        transcript_path=transcript, output_path=article, title_hint="新标题") is True
    assert calls["n"] == 2

    # 转写内容变(模拟重转写出不同文本)-> 重新整理
    transcript.write_text("完全不同的另一段转写内容。", encoding="utf-8")
    assert asr_helpers._polish_transcript_with_opus(
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

    monkeypatch.setattr(asr_helpers.subprocess, "run", fake_run)

    assert asr_helpers._polish_transcript_with_opus(
        transcript_path=transcript, output_path=article, title_hint="") is True
    article.unlink()  # 删掉成稿,只留 sidecar
    assert asr_helpers._polish_transcript_with_opus(
        transcript_path=transcript, output_path=article, title_hint="") is True
    assert calls["n"] == 2


def test_asr_stage_label_accepts_ascii_and_legacy_transcribe_progress():
    assert asr_helpers._asr_stage_label("[OK] 转写: /tmp/job/raw/demo.txt") == "语音转写"
    assert asr_helpers._asr_stage_label("\u2705 转写: /tmp/job/raw/demo.txt") == "语音转写"
    assert asr_helpers._asr_stage_label("[DL] 下载中...") == "下载视频"


def test_call_opus_for_rw_delegates_to_common_call_opus(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_call_opus(prompt: str, **kwargs) -> str:
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(rw_helpers, "call_opus", fake_call_opus)

    assert rw_helpers._call_opus_for_rw("user", "system", "model-x") == "ok"
    assert captured["prompt"] == "user"
    assert captured["kwargs"]["system_prompt"] == "system"
    assert captured["kwargs"]["model"] == "model-x"
    assert captured["kwargs"]["timeout_seconds"] == rw_helpers.RW_LLM_TIMEOUT_SEC


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


def test_execute_image_orchestrates_scenes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("paper_card_talk_015", "t", {})
    runner.write_episode(job.job_id, {
        "beats": [{"scene": "ch1"}, {"scene": "s1"}, {"scene": "s2"}],
        "scenes": {
            "ch1": {"prompt": "章节卡"},
            "s1": {"prompt": "场景一", "sketches": [{"prompt": "简笔画1"}]},
            "s2": {"prompt": ""},
        },
        "image": {"size": "1536x1024", "quality": "auto", "sketchStylePrefix": "白底黑剪影"},
    })
    calls: list[str] = []

    def fake_generate(*, scene_id, prompt, size, quality, target, job_id):
        calls.append(scene_id)
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"webp")

    monkeypatch.setattr(media_helpers, "_generate_scene_image", fake_generate)
    out = asyncio.run(runner._execute_image(job.job_id))

    assert (out["ok"], out["skipped"], out["failed"]) == (1, 0, 1)
    assert out["sketch_ok"] == 1
    assert "ch1" not in calls
    assert "s1" in calls and "s1-sk1" in calls
    assert (tmp_path / job.job_id / "03_image" / "s1.webp").is_file()
    assert (tmp_path / job.job_id / "03_image" / "s1-sk1.webp").is_file()


def test_execute_image_idempotent_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("paper_card_talk_015", "t", {})
    img_dir = tmp_path / job.job_id / "03_image"
    img_dir.mkdir(parents=True)
    (img_dir / "s1.webp").write_bytes(b"existing")
    runner.write_episode(job.job_id, {
        "beats": [{"scene": "s1"}],
        "scenes": {"s1": {"prompt": "场景一"}},
        "image": {},
    })
    monkeypatch.setattr(
        media_helpers, "_generate_scene_image",
        lambda **_kwargs: pytest.fail("不应重生已存在的容器图"),
    )

    out = asyncio.run(runner._execute_image(job.job_id))
    assert (out["ok"], out["skipped"], out["failed"]) == (0, 1, 0)


def test_execute_image_all_failed_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("paper_card_talk_015", "t", {})
    runner.write_episode(job.job_id, {
        "beats": [{"scene": "s1"}],
        "scenes": {"s1": {"prompt": "唯一场景"}},
        "image": {},
    })
    monkeypatch.setattr(
        media_helpers, "_generate_scene_image",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="all .* scene image generations failed"):
        asyncio.run(runner._execute_image(job.job_id))


def test_execute_tts_uses_extracted_run_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("paper_card_talk_015", "t", {})
    runner.write_episode(job.job_id, {
        "beats": [
            {"zh": "一", "scene": "s1"},
            {"zh": "二", "scene": "s1"},
            {"zh": "三", "scene": "s2"},
        ],
        "scenes": {},
    })
    tpl = tmp_path / "template" / ".015-draft-assets"
    tpl.mkdir(parents=True)
    (tpl / "tts_gen.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(pr, "_template_dir", lambda _name: tmp_path / "template")

    def fake_tts(*, script, episode_path, audio_dir, on_line, only=None, force=False):
        assert Path(script).name == "tts_gen.py"
        assert Path(audio_dir).name == "04_tts"
        on_line("tts stub")
        ep = json.loads(Path(episode_path).read_text(encoding="utf-8"))
        for b in ep["beats"]:
            b["audioFile"] = f"04_tts/scene-{b['scene']}.mp3"
            b["audioStart"] = 0.0
            b["audioEnd"] = 1.0
        Path(episode_path).write_text(json.dumps(ep, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(media_helpers, "_run_tts_gen_015", fake_tts)

    out = asyncio.run(runner._execute_tts(job.job_id))

    assert out["mode"] == "segmented"
    assert out["scene_count"] == 2
    assert out["audio_count"] == 2
    assert len(out["items"]) == 3
    assert out["items"][0]["audio_relpath"] == "04_tts/scene-s1.mp3"


def test_execute_render_uses_extracted_run_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from ncds_opus_factory.commands import render_015

    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("paper_card_talk_015", "t", {})
    runner.write_episode(job.job_id, {"beats": [], "scenes": {}})
    audio_dir = tmp_path / job.job_id / "04_tts"
    audio_dir.mkdir(parents=True)
    (audio_dir / "scene-s1.mp3").write_bytes(b"mp3")
    pic_dir = tmp_path / job.job_id / "03_image"
    pic_dir.mkdir()
    captured: dict = {}

    def fake_render(**kwargs):
        captured.update(kwargs)
        Path(kwargs["output_path"]).write_bytes(b"mp4")
        return {
            "output_path": kwargs["output_path"],
            "video_size_bytes": 3,
            "workdir": kwargs["workdir"],
        }

    monkeypatch.setattr(render_015, "run", fake_render)

    out = asyncio.run(runner._execute_render(job.job_id))

    assert out["video_relpath"] == "06_render/output.mp4"
    assert out["video_size_bytes"] == 3
    assert captured["episode_path"].endswith("02_rw/episode.json")
    assert captured["audio_dir"].endswith("04_tts")
    assert captured["picture_dir"].endswith("03_image")
    assert captured["cleanup_workdir"] is True


def test_execute_storyboard_fills_scenes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("paper_card_talk_015", "t", {})
    runner.write_episode(job.job_id, {
        "meta": {"title": "T"},
        "image": {},
        "visual": {},
        "beats": [
            {"zh": "一", "en": "", "scene": ""},
            {"zh": "二", "en": "", "scene": ""},
            {"zh": "三", "en": "", "scene": ""},
        ],
        "scenes": {},
    })
    director_json = json.dumps({
        "scenes": {
            "s1": {"prompt": "场景一", "group": "g1", "imageFit": "contain",
                   "motion": {"enter": "fade"}, "overlays": [], "sketches": []},
            "s2": {"prompt": "场景二", "group": "g1", "imageFit": "contain",
                   "motion": {"enter": "fade"}, "overlays": [], "sketches": []},
        },
        "sceneMap": {"1": "s1", "2": "s1", "3": "s2"},
    }, ensure_ascii=False)

    monkeypatch.setattr(rw_helpers, "_call_opus_for_rw", lambda *_args, **_kwargs: director_json)
    out = asyncio.run(runner._execute_storyboard(job.job_id))

    assert out["scenes_count"] == 2
    assert out["groups_count"] == 1
    got = json.loads((tmp_path / job.job_id / "02_rw" / "episode.json").read_text(encoding="utf-8"))
    assert set(got["scenes"]) == {"s1", "s2"}
    assert [b["scene"] for b in got["beats"]] == ["s1", "s1", "s2"]


def test_execute_rw_uses_extracted_run_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("paper_card_talk_015", "t", {})
    state = runner.get_job(job.job_id)
    state.nodes["asr"].status = "done"
    state.nodes["asr"].outputs = {
        "items": [{"index": 1, "title": "标题", "text": "素材正文足够用于改写。"}],
    }
    runner._save(state)

    candidates = [
        {"id": "opus", "label": "改写方案 A", "runner": "fake", "model": "fake-a"},
        {"id": "deepseek", "label": "改写方案 B", "runner": "fake", "model": "fake-b"},
    ]
    monkeypatch.setattr(rw_helpers, "MODEL_CANDIDATES", candidates)

    async def stub_invoke(cand, user_prompt, system_prompt, on_progress, on_status=None):
        assert "素材正文足够用于改写" in user_prompt
        if on_status is not None:
            on_status(cand["id"], "running")
            on_status(cand["id"], "done")
        on_progress(f"stub {cand['id']} done")
        return f"```markdown\n# 改写稿 {cand['id']}\n\n正文。\n```"

    monkeypatch.setattr(rw_helpers, "_invoke_rw_candidate", stub_invoke)
    monkeypatch.setattr(rw_helpers, "_apply_rw_qc", lambda *a, **k: {"qc": {"verdict": "pass"}})

    out = asyncio.run(runner._execute_rw(job.job_id))

    assert out["success_count"] == 2
    assert out["candidate_count"] == 2
    assert [d["model_id"] for d in out["drafts"]] == ["opus", "deepseek"]
    assert all(d["qc"]["verdict"] == "pass" for d in out["drafts"])

    for mid in ("opus", "deepseek"):
        draft_path = tmp_path / job.job_id / "02_rw" / mid / "draft.md"
        text = draft_path.read_text(encoding="utf-8")
        assert "```" not in text
        assert f"改写稿 {mid}" in text

    patched_drafts = runner.get_job(job.job_id).nodes["rw"].outputs["drafts"]
    assert [d["model_id"] for d in patched_drafts] == ["opus", "deepseek"]


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
