"""tests：pipeline_runner 的 opus polish 幂等。

_polish_transcript_with_opus 用「转写内容 + title_hint」的 sha256 当缓存键，落 sidecar
<article>.src-sha256；同源第二次命中即跳过、不再调 opus（返回 False）。打桩 subprocess.run
模拟 claude CLI 的 JSON 输出，计数真实调用次数。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from ncds_opus_core.pipelines import get_pipeline

from ncds_opus_factory.server import pipeline_asr_helpers as asr_helpers
from ncds_opus_factory.server import pipeline_lines_tasks as lines_tasks
from ncds_opus_factory.server import pipeline_media_helpers as media_helpers
from ncds_opus_factory.server import pipeline_runner as pr
from ncds_opus_factory.server import pipeline_rw_helpers as rw_helpers
from ncds_opus_factory.server import pipeline_storyboard_tasks as storyboard_tasks
from ncds_opus_factory.server.engine.recipes import FINAL_PREVIEW
from ncds_opus_factory.server.schemas import TaskMeta


class _FakeProc:
    returncode = 0
    stderr = ""

    def __init__(self, result_text: str):
        self.stdout = json.dumps(
            {"type": "result", "is_error": False, "result": result_text}
        ) + "\n"


def test_core_pipeline_order_matches_engine_recipe():
    """`/jobs` facade 和 engine recipe 必须共享同一条 final_preview 顺序。"""
    pipeline = get_pipeline("final_preview")
    assert pipeline.topological_order() == FINAL_PREVIEW.topological_order()
    assert pipeline.node("image").deps == ("storyboard",)
    assert pipeline.node("tts").deps == ("image",)
    assert pipeline.node("preview").deps == ("tts",)


def test_run_image_allowed_after_storyboard_before_tts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """画面资产应由吴道子在 storyboard 后直接启动，不再等待伯牙 tts。"""
    runner = pr.PipelineRunner(tmp_path)
    job = runner.create_job("final_preview", "t", {})
    state = runner.get_job(job.job_id)
    for node in ("asr", "rw", "lines", "storyboard"):
        state.nodes[node].status = "done"
    state.nodes["image"].status = "idle"
    state.nodes["tts"].status = "idle"
    runner._save(state)

    async def fake_execute(job_id: str, node_name: str) -> None:
        return None

    monkeypatch.setattr(runner, "_execute", fake_execute)
    asyncio.run(runner.run_node(job.job_id, "image"))

    updated = runner.get_job(job.job_id)
    assert updated.nodes["image"].status == "queued"


def test_run_node_active_is_idempotent_and_does_not_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """运行中的节点再次收到 run 请求时，不重置、不二次启动。"""
    runner = pr.PipelineRunner(tmp_path)
    job = runner.create_job("final_preview", "t", {})
    state = runner.get_job(job.job_id)
    for node in ("asr", "rw", "lines", "storyboard"):
        state.nodes[node].status = "done"
    state.nodes["image"].status = "running"
    state.nodes["image"].progress = "[19/33] running"
    state.nodes["tts"].status = "done"
    state.nodes["tts"].outputs = {"keep": True}
    runner._save(state)

    async def fail_execute(_job_id: str, _node_name: str) -> None:
        raise AssertionError("active node must not be restarted")

    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(runner, "_execute", fail_execute)
    monkeypatch.setattr(runner, "_emit", lambda _job_id, event: emitted.append(event))

    asyncio.run(runner.run_node(job.job_id, "image"))

    updated = runner.get_job(job.job_id)
    assert updated.nodes["image"].status == "running"
    assert updated.nodes["image"].progress == "[19/33] running"
    assert updated.nodes["tts"].status == "done"
    assert updated.nodes["tts"].outputs == {"keep": True}
    assert not runner._running_nodes
    assert emitted[-1]["node"] == "image"


def test_run_node_with_task_runner_enqueues_pipeline_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """正式 server 注入 TaskRunner 后，画布节点应入队给 worker，不在 8810 内执行。"""

    class FakeMeta:
        task_id = "pipeline_node_1780000000000abcdef12"
        cmd = "pipeline_node"
        status = "pending"
        error = None

    class FakeStore:
        def __init__(self) -> None:
            self.meta = FakeMeta()

        def get_meta(self, task_id: str) -> FakeMeta | None:
            return self.meta if task_id == self.meta.task_id else None

    class FakeTaskRunner:
        def __init__(self) -> None:
            self.store = FakeStore()
            self.submits: list[tuple[str, dict[str, Any], str | None]] = []

        async def submit(self, cmd: str, params: dict[str, Any], source: str | None = None) -> str:
            self.submits.append((cmd, params, source))
            return self.store.meta.task_id

    runner = pr.PipelineRunner(tmp_path)
    runner.attach_task_runner(FakeTaskRunner())
    job = runner.create_job("final_preview", "t", {})
    state = runner.get_job(job.job_id)
    for node in ("asr", "rw", "lines", "storyboard"):
        state.nodes[node].status = "done"
    runner._save(state)

    async def fail_execute(_job_id: str, _node_name: str) -> None:
        raise AssertionError("server must enqueue instead of executing locally")

    monkeypatch.setattr(runner, "_execute", fail_execute)
    asyncio.run(runner.run_node(job.job_id, "image"))

    task_runner = runner._task_runner
    updated = runner.get_job(job.job_id)
    assert task_runner.submits == [
        ("pipeline_node", {"job_id": job.job_id, "node_name": "image"}, "pipeline"),
    ]
    assert updated.nodes["image"].status == "queued"
    assert updated.nodes["image"].task_id == "pipeline_node_1780000000000abcdef12"
    assert not runner._running_nodes


def test_get_job_reconciles_orphan_running_node(tmp_path: Path):
    runner = pr.PipelineRunner(tmp_path)
    job = runner.create_job("final_preview", "t", {})
    state = runner._load(job.job_id)
    state.nodes["image"].status = "running"
    state.nodes["image"].started_at = time.time() - 60
    state.nodes["image"].progress = "[29/33] S1-13b 前景素材生成中…"
    state.nodes["image"].task_id = "i_orphan_engine"
    runner._save(state)

    updated = runner.get_job(job.job_id)

    assert updated.nodes["image"].status == "failed"
    assert "后台执行已中断" in (updated.nodes["image"].error or "")


def test_get_job_reconciles_active_pipeline_task_over_failed_facade(tmp_path: Path):
    """server 读状态时应以 worker 的 pipeline_node 在途任务为准。"""

    class FakeStore:
        def __init__(self, meta: TaskMeta) -> None:
            self.meta = meta

        def list_tasks(self) -> list[TaskMeta]:
            return [self.meta]

        def get_meta(self, task_id: str) -> TaskMeta | None:
            return self.meta if task_id == self.meta.task_id else None

    class FakeTaskRunner:
        def __init__(self, meta: TaskMeta) -> None:
            self.store = FakeStore(meta)

    runner = pr.PipelineRunner(tmp_path)
    job = runner.create_job("final_preview", "t", {})
    meta = TaskMeta(
        task_id="pipeline_node_1780000000000abcdef12",
        cmd="pipeline_node",
        params={"job_id": job.job_id, "node_name": "image"},
        status="running",
        created_at="2026-06-23T15:00:00",
        started_at="2026-06-23T15:00:01",
        source="pipeline",
    )
    runner.attach_task_runner(FakeTaskRunner(meta))

    state = runner._load(job.job_id)
    state.nodes["image"].status = "failed"
    state.nodes["image"].task_id = "i_old_engine"
    state.nodes["image"].error = "后台执行已中断，请重新执行该节点"
    state.nodes["image"].finished_at = time.time()
    runner._save(state)

    updated = runner.get_job(job.job_id)

    assert updated.nodes["image"].status == "running"
    assert updated.nodes["image"].task_id == "pipeline_node_1780000000000abcdef12"
    assert updated.nodes["image"].error is None
    assert updated.nodes["image"].finished_at is None


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
        pipeline_id="final_preview",
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


def test_execute_lines_writes_episode_with_template_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
    rw_dir = tmp_path / job.job_id / "02_rw"
    rw_dir.mkdir(parents=True)
    (rw_dir / "draft.md").write_text("# 草稿\n\n第一段。第二段。", encoding="utf-8")

    raw = json.dumps({
        "meta": {"title": "测试标题", "subtitle": "", "tags": ["x"]},
        "beats": [
            {"zh": "第一句字幕", "en": "", "chapter": 1},
            {"zh": "第二句字幕", "en": "", "chapter": None},
        ],
    }, ensure_ascii=False)

    def fake_lines_fallback(user_prompt: str, system_prompt: str, on_progress, **_kwargs: Any) -> Any:
        assert "第一段" in user_prompt
        assert "脚本结构化助手" in system_prompt
        on_progress("stub lines")
        return json.loads(raw)

    monkeypatch.setattr(lines_tasks, "structure_lines_json_with_fallback", fake_lines_fallback)

    out = asyncio.run(runner._execute_lines(job.job_id))

    assert out == {"episode_relpath": "02_rw/episode.json", "beats_count": 2}
    episode = json.loads((rw_dir / "episode.json").read_text(encoding="utf-8"))
    assert episode["meta"]["title"] == "测试标题"
    assert [b["zh"] for b in episode["beats"]] == ["第一句字幕", "第二句字幕"]
    assert all(b["scene"] == "" for b in episode["beats"])
    assert episode["scenes"] == {}
    assert "audio" in episode and "playback" in episode


def test_execute_asr_collect_uses_extracted_run_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import ncds_opus_factory.common.tikhub_client as tc

    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {
        "shares": [
            {"url": "https://v.douyin.com/ok"},
            {"url": "https://v.douyin.com/bad"},
            {"url": ""},
        ],
    })
    seen: dict[str, Any] = {"calls": []}

    def fake_resolve(url: str) -> str:
        if "bad" in url:
            raise RuntimeError("bad link")
        return "aweme-ok"

    async def fake_thread_cancellable(fn: Any, flag_path: Any, /, *args: Any, **kwargs: Any) -> Any:
        seen["calls"].append((args, kwargs, flag_path))
        assert kwargs["do_audio"] is False
        assert kwargs["do_frames"] is False
        assert kwargs["meta"] == {"desc": "meta desc"}
        return {
            "aweme_id": args[0],
            "desc": "entry desc",
            "status": {"transcribe": "ok"},
            "text": "清洗稿",
        }

    monkeypatch.setattr(tc, "resolve_aweme_id", fake_resolve)
    monkeypatch.setattr(tc, "fetch_one_video_detail", lambda aweme_id: {"id": aweme_id})
    monkeypatch.setattr(tc, "extract_meta", lambda detail: {"desc": "meta desc"})
    runner._run_in_thread_cancellable = fake_thread_cancellable  # type: ignore[method-assign]

    out = asyncio.run(runner._execute_asr_collect(job.job_id))

    assert out["collect_dir"].endswith("01_collect")
    assert len(seen["calls"]) == 1
    assert [e.get("aweme_id") for e in out["collected"]] == ["aweme-ok", ""]
    assert out["collected"][0]["index"] == 1
    assert out["collected"][0]["url"].endswith("/ok")
    assert "bad link" in out["collected"][1]["error"]

    patched = runner.get_job(job.job_id).nodes["asr"].outputs["collected"]
    assert [e.get("aweme_id") for e in patched] == ["aweme-ok", ""]


def test_select_rw_model_copies_selected_draft(tmp_path: Path):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
    rw_dir = tmp_path / job.job_id / "02_rw"
    model_dir = rw_dir / "opus"
    model_dir.mkdir(parents=True)
    (model_dir / "draft.md").write_text("# 定稿\n\n正文", encoding="utf-8")

    state = runner.get_job(job.job_id)
    state.nodes["rw"].status = "done"
    state.nodes["rw"].outputs = {
        "drafts": [
            {"model_id": "opus", "status": "success"},
            {"model_id": "bad", "status": "failed"},
        ],
        "selected_model_id": None,
    }
    runner._save(state)

    runner.select_rw_model(job.job_id, "opus")

    assert (rw_dir / "draft.md").read_text(encoding="utf-8") == "# 定稿\n\n正文"
    updated = runner.get_job(job.job_id).nodes["rw"].outputs
    assert updated["selected_model_id"] == "opus"
    with pytest.raises(ValueError, match="unknown model or failed model"):
        runner.select_rw_model(job.job_id, "bad")


def test_regen_scene_image_from_preview_updates_image_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
    runner.write_episode(job.job_id, {
        "image": {"size": "1024x1024", "quality": "high", "noTextHint": "no text"},
        "beats": [{"scene": "s1"}],
        "scenes": {"s1": {"prompt": "paper card scene"}},
    })
    state = runner.get_job(job.job_id)
    state.nodes["image"].status = "done"
    state.nodes["image"].outputs = {
        "items": [{"scene_id": "s1", "image_relpath": "03_image/old.webp"}],
    }
    runner._save(state)
    captured: dict[str, Any] = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        Path(kwargs["target"]).write_bytes(b"webp")
        return [Path(kwargs["target"])]

    monkeypatch.setattr(media_helpers, "_generate_scene_image", fake_generate)

    rel = asyncio.run(runner.regen_scene_image_from_preview(job.job_id, "s1"))

    assert rel == "03_image/s1.webp"
    assert captured["scene_id"] == "s1"
    assert captured["prompt"] == "paper card scene no text"
    assert captured["size"] == "1024x1024"
    assert captured["quality"] == "high"
    assert captured["n"] == 4
    updated = runner.get_job(job.job_id).nodes["image"].outputs["items"][0]
    assert updated["image_relpath"] == "03_image/s1.webp"


def test_select_image_variant_copies_candidate_to_main(tmp_path: Path):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
    img_dir = tmp_path / job.job_id / "03_image"
    img_dir.mkdir(parents=True)
    (img_dir / "s1.webp").write_bytes(b"old-main")
    (img_dir / "s1-v1.webp").write_bytes(b"candidate-1")
    (img_dir / "s1-v2.webp").write_bytes(b"candidate-2")
    state = runner.get_job(job.job_id)
    state.nodes["image"].status = "done"
    state.nodes["image"].outputs = {
        "items": [{
            "scene_id": "s1",
            "image_relpath": "03_image/s1.webp",
            "variants": [
                {"index": 1, "image_relpath": "03_image/s1-v1.webp"},
                {"index": 2, "image_relpath": "03_image/s1-v2.webp"},
            ],
        }],
    }
    runner._save(state)

    rel = runner.select_image_variant(job.job_id, "s1", "03_image/s1-v2.webp")

    assert rel == "03_image/s1.webp"
    assert (img_dir / "s1.webp").read_bytes() == b"candidate-2"
    item = runner.get_job(job.job_id).nodes["image"].outputs["items"][0]
    assert item["selected_variant_relpath"] == "03_image/s1-v2.webp"
    assert item["variants"][1]["selected"] is True


def test_generate_scene_image_omits_quality_for_generation_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}

    class Proc:
        returncode = 0
        pid = 99999

        def __init__(self, cmd: list[str], **kwargs: Any):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            out_dir = Path(cmd[cmd.index("--out-dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            from PIL import Image

            count = int(cmd[cmd.index("--n") + 1])
            for i in range(1, count + 1):
                Image.new("RGB", (1, 1), color="white").save(out_dir / f"image_{i:02d}.png")

        def poll(self) -> int:
            return 0

        def communicate(self) -> tuple[str, str]:
            return "", ""

    def fake_popen(cmd: list[str], **kwargs: Any):
        return Proc(cmd, **kwargs)

    monkeypatch.setattr(media_helpers, "GPT_IMAGE_OUTPUT_ROOT", tmp_path / "gpt-image")
    monkeypatch.setattr(media_helpers.subprocess, "Popen", fake_popen)

    target = tmp_path / "scene.webp"
    media_helpers._generate_scene_image(
        scene_id="s1",
        prompt="paper card",
        size="1024x1024",
        quality="auto",
        target=target,
        job_id="job1",
        n=4,
    )

    assert target.is_file()
    for i in range(1, 5):
        assert (tmp_path / f"scene-v{i}.webp").is_file()
    assert "--quality" not in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--n") + 1] == "4"
    assert captured["kwargs"]["start_new_session"] is True
    out_dir = Path(captured["cmd"][captured["cmd"].index("--out-dir") + 1])
    assert out_dir.name.startswith("job-job1-s1-")


def test_generate_scene_image_cancel_kills_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    class Proc:
        returncode = None
        pid = 99999

        def poll(self) -> None:
            return None

        def communicate(self) -> tuple[str, str]:
            return "", ""

    proc = Proc()
    killed = {"n": 0}

    monkeypatch.setattr(media_helpers, "GPT_IMAGE_OUTPUT_ROOT", tmp_path / "gpt-image")
    monkeypatch.setattr(media_helpers.subprocess, "Popen", lambda *_a, **_k: proc)
    monkeypatch.setattr(media_helpers.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(media_helpers, "_terminate_proc_group", lambda _p: killed.__setitem__("n", killed["n"] + 1))

    from ncds_opus_core.common import cancel as _cancel

    _cancel.install(lambda: True)
    try:
        with pytest.raises(_cancel.TaskCancelled):
            media_helpers._generate_scene_image(
                scene_id="s1",
                prompt="paper card",
                size="1024x1024",
                quality="auto",
                target=tmp_path / "scene.webp",
                job_id="job1",
            )
    finally:
        _cancel.uninstall()

    assert killed["n"] == 1


def test_execute_image_orchestrates_scenes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
    runner.write_episode(job.job_id, {
        "beats": [{"scene": "g1"}, {"scene": "g1"}],
        "visual": {
            "stage": {"background": {"prompt": "统一背景", "imageFit": "cover"}},
            "shots": [
                {"beatIndex": 1, "shotId": "b001", "group": "g1", "intent": "画面一",
                 "assets": [{"id": "a1", "prompt": "asset one"}]},
                {"beatIndex": 2, "shotId": "b002", "group": "g1", "intent": "画面二",
                 "assets": [{"id": "a1", "prompt": "asset two"}]},
            ],
        },
        "image": {"size": "1536x1024", "quality": "auto", "sketchStylePrefix": "白底黑剪影"},
    })
    calls: list[str] = []

    def fake_generate(*, scene_id, prompt, size, quality, target, job_id, n=1):
        calls.append(scene_id)
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"webp")
        return [Path(target)]

    monkeypatch.setattr(media_helpers, "_generate_scene_image", fake_generate)
    out = asyncio.run(runner._execute_image(job.job_id))

    assert (out["ok"], out["skipped"], out["failed"]) == (1, 0, 0)
    assert out["asset_ok"] == 2
    assert "background" in calls and "b001-a1" in calls and "b002-a1" in calls
    assert (tmp_path / job.job_id / "03_image" / "background.webp").is_file()
    assert (tmp_path / job.job_id / "03_image" / "b001-a1.webp").is_file()


def test_execute_image_idempotent_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
    img_dir = tmp_path / job.job_id / "03_image"
    img_dir.mkdir(parents=True)
    (img_dir / "background.webp").write_bytes(b"existing")
    runner.write_episode(job.job_id, {
        "beats": [{"scene": "g1"}],
        "visual": {
            "stage": {"background": {"prompt": "统一背景", "imageFit": "cover"}},
            "shots": [{"beatIndex": 1, "shotId": "b001", "group": "g1", "intent": "画面一", "assets": []}],
        },
        "image": {"n": 1},
    })
    monkeypatch.setattr(
        media_helpers, "_generate_scene_image",
        lambda **_kwargs: pytest.fail("不应重生已存在的背景图"),
    )

    out = asyncio.run(runner._execute_image(job.job_id))
    assert (out["ok"], out["skipped"], out["failed"]) == (0, 1, 0)


def test_execute_image_skip_preserves_selected_variant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
    img_dir = tmp_path / job.job_id / "03_image"
    img_dir.mkdir(parents=True)
    (img_dir / "background-v1.webp").write_bytes(b"variant-one")
    (img_dir / "background-v2.webp").write_bytes(b"variant-two")
    (img_dir / "background.webp").write_bytes(b"variant-two")
    runner.write_episode(job.job_id, {
        "beats": [{"scene": "g1"}],
        "visual": {
            "stage": {"background": {"prompt": "统一背景", "imageFit": "cover"}},
            "shots": [{"beatIndex": 1, "shotId": "b001", "group": "g1", "intent": "画面一", "assets": []}],
        },
        "image": {"n": 2},
    })
    monkeypatch.setattr(
        media_helpers, "_generate_scene_image",
        lambda **_kwargs: pytest.fail("候选已存在时不应重生"),
    )

    out = asyncio.run(runner._execute_image(job.job_id))

    assert (out["ok"], out["skipped"], out["failed"]) == (0, 1, 0)
    bg = out["background"]
    assert bg["image_relpath"] == "03_image/background.webp"
    assert bg["selected_variant_relpath"] == "03_image/background-v2.webp"
    assert [v["selected"] for v in bg["variants"]] == [False, True]


def test_execute_image_all_failed_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
    runner.write_episode(job.job_id, {
        "beats": [{"scene": "g1"}],
        "visual": {
            "stage": {"background": {"prompt": "统一背景", "imageFit": "cover"}},
            "shots": [{"beatIndex": 1, "shotId": "b001", "group": "g1", "intent": "画面一", "assets": []}],
        },
        "image": {"retries": 0},
    })
    monkeypatch.setattr(
        media_helpers, "_generate_scene_image",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="画面背景生成失败"):
        asyncio.run(runner._execute_image(job.job_id))


def test_execute_image_retries_transient_timeout_without_leaking_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
    runner.write_episode(job.job_id, {
        "beats": [{"scene": "g1"}],
        "visual": {
            "stage": {"background": {"prompt": "统一背景", "imageFit": "cover"}},
            "shots": [{"beatIndex": 1, "shotId": "b001", "group": "g1", "intent": "画面一", "assets": []}],
        },
        "image": {"retries": 1, "retryBackoffSeconds": 0, "n": 1},
    })
    calls = {"n": 0}
    progress: list[str] = []

    def fake_generate(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(
                'gpt-image gen failed: File "/tmp/ssl.py", line 1\n'
                "TimeoutError: The read operation timed out"
            )
        Path(kwargs["target"]).write_bytes(b"webp")
        return [Path(kwargs["target"])]

    monkeypatch.setattr(media_helpers, "_generate_scene_image", fake_generate)
    monkeypatch.setattr(runner, "_push_progress", lambda _job_id, _node, text: progress.append(text))

    out = asyncio.run(runner._execute_image(job.job_id))

    assert calls["n"] == 2
    assert out["ok"] == 1
    joined = "\n".join(progress)
    assert "重试 1/1" in joined
    assert "图片服务响应超时" in joined
    assert "File " not in joined
    assert "TimeoutError" not in joined


def test_execute_image_failure_progress_is_friendly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
    runner.write_episode(job.job_id, {
        "beats": [{"scene": "g1"}],
        "visual": {
            "stage": {"background": {"prompt": "统一背景", "imageFit": "cover"}},
            "shots": [{"beatIndex": 1, "shotId": "b001", "group": "g1", "intent": "画面一", "assets": []}],
        },
        "image": {"retries": 0},
    })
    progress: list[str] = []

    def fake_generate(**_kwargs):
        raise RuntimeError(
            'gpt-image gen failed: File "/tmp/ssl.py", line 1\n'
            "TimeoutError: The read operation timed out"
        )

    monkeypatch.setattr(media_helpers, "_generate_scene_image", fake_generate)
    monkeypatch.setattr(runner, "_push_progress", lambda _job_id, _node, text: progress.append(text))

    with pytest.raises(RuntimeError, match="画面背景生成失败"):
        asyncio.run(runner._execute_image(job.job_id))

    joined = "\n".join(progress)
    assert "背景图失败：图片服务响应超时，请稍后重试。" in joined
    assert "File " not in joined
    assert "TimeoutError" not in joined


def test_execute_tts_uses_extracted_run_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
    runner.write_episode(job.job_id, {
        "beats": [
            {"zh": "一", "scene": "s1"},
            {"zh": "二", "scene": "s1"},
            {"zh": "三", "scene": "s2"},
        ],
        "scenes": {},
    })
    tpl = tmp_path / "template" / ".final-preview-assets"
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
    from ncds_opus_factory.commands import render_final_preview

    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
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

    monkeypatch.setattr(render_final_preview, "run", fake_render)

    out = asyncio.run(runner._execute_render(job.job_id))

    assert out["video_relpath"] == "06_render/output.mp4"
    assert out["video_size_bytes"] == 3
    assert captured["episode_path"].endswith("02_rw/episode.json")
    assert captured["audio_dir"].endswith("04_tts")
    assert captured["picture_dir"].endswith("03_image")
    assert captured["cleanup_workdir"] is True


def test_execute_storyboard_fills_scenes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
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
        "visual": {
            "stage": {"background": {"prompt": "统一背景", "imageFit": "cover"}},
            "shots": [
                {"beatIndex": 1, "shotId": "b001", "group": "g1", "intent": "画面一",
                 "assets": [{"id": "a1", "prompt": "asset one"}]},
                {"beatIndex": 2, "shotId": "b002", "group": "g1", "intent": "画面二",
                 "assets": [{"id": "a1", "prompt": "asset two"}]},
                {"beatIndex": 3, "shotId": "b003", "group": "g2", "intent": "画面三",
                 "assets": [{"id": "a1", "prompt": "asset three"}]},
            ],
        },
    }, ensure_ascii=False)

    def fake_fallback(_user_prompt: str, _system_prompt: str, _on_progress, *, parse, **_kwargs: Any) -> Any:
        return parse(director_json)

    monkeypatch.setattr(storyboard_tasks, "structure_json_with_model_fallback", fake_fallback)
    out = asyncio.run(runner._execute_storyboard(job.job_id))

    assert out["shots_count"] == 3
    assert out["assets_count"] == 3
    assert out["groups_count"] == 2
    got = json.loads((tmp_path / job.job_id / "02_rw" / "episode.json").read_text(encoding="utf-8"))
    assert got["scenes"] == {}
    assert [s["shotId"] for s in got["visual"]["shots"]] == ["b001", "b002", "b003"]
    assert [b["scene"] for b in got["beats"]] == ["g1", "g1", "g2"]


def test_execute_rw_uses_extracted_run_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {})
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
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(runner, "_emit", lambda _job_id, event: emitted.append(event))

    async def stub_invoke(cand, user_prompt, system_prompt, on_progress, on_status=None):
        assert "素材正文足够用于改写" in user_prompt
        if on_status is not None:
            on_status(cand["id"], "running")
            on_status(cand["id"], "done")
        if cand["id"] == "deepseek":
            await asyncio.sleep(0.01)
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

    rw_states = [e["state"] for e in emitted if e.get("type") == "node_status" and e.get("node") == "rw"]
    assert any((s.get("outputs") or {}).get("model_progress") for s in rw_states)
    draft_patches = [
        (s.get("outputs") or {}).get("drafts")
        for s in rw_states
        if (s.get("outputs") or {}).get("drafts")
    ]
    assert any([d["model_id"] for d in drafts] == ["opus"] for drafts in draft_patches)
    assert any([d["model_id"] for d in drafts] == ["opus", "deepseek"] for drafts in draft_patches)


# --- 鬼谷子选题：job inputs 的 domain 透传到 guiguzi（task-2.5 调用方线程化）------ #
def test_guiguzi_threads_domain_from_job_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """job inputs 带 domain（前端 doCreate 写入）时，analyze/generate 两个 bg 都把它透传给鬼谷子。"""
    import asyncio

    from ncds_opus_factory.commands import guiguzi as gz

    runner = pr.PipelineRunner(video_jobs_dir=tmp_path)
    job = runner.create_job("final_preview", "t", {"domain": "finance"})
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
    job = runner.create_job("final_preview", "t", {})
    seen: dict[str, object] = {}

    def fake_analyze(items, on_progress=gz._noop, require_comment=True, domain=None, **kw):
        seen["domain"] = domain
        return {"opus": {"analysis": {}, "error": None}}

    monkeypatch.setattr(gz, "analyze", fake_analyze)
    asyncio.run(runner._run_guiguzi_analyze_bg(job.job_id, [{"text": "t", "comment": "c"}]))
    assert seen["domain"] is None
