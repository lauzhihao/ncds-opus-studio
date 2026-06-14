"""E1-b2 slice-1：015 step-performer + 引擎驱动真实 015 链端到端验证（hermetic）。

- lines/storyboard：真实复用 PipelineRunner 的 opus 结构化算法（opus 调用注入桩）。
- e2e：引擎按真实 015 拓扑（含 content_edit 闸门）驱动 lines/storyboard 真实 performer
  + 重步骤桩（asr/rw/tts/image/render），经共享 02_rw/episode.json 耦合，端到端出 mp4。
  不依赖 015 样例素材 / 真 opus / node / ffmpeg。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from ncds_opus_factory.server.engine import pipeline_performers_015 as perf
from ncds_opus_factory.server.engine.instance_runner import InstanceRunner
from ncds_opus_factory.server.engine.instance_store import InstanceStore
from ncds_opus_factory.server.engine.types import Recipe, RecipeStep


def _noop(_t: str) -> None:
    return None


# opus 桩：按 system_prompt 区分 lines（脚本结构化）vs storyboard（director）。
_LINES_JSON = json.dumps({
    "meta": {"title": "测试标题", "subtitle": "", "tags": ["t1"]},
    "beats": [
        {"zh": "第一句字幕", "en": "", "chapter": 1},
        {"zh": "第二句字幕", "en": "", "chapter": None},
        {"zh": "第三句字幕", "en": "", "chapter": None},
    ],
}, ensure_ascii=False)

_DIRECTOR_JSON = json.dumps({
    "scenes": {
        "s1": {"prompt": "场景一容器图", "group": "g1", "imageFit": "contain",
               "motion": {"enter": "fade"}, "overlays": [], "sketches": []},
        "s2": {"prompt": "场景二容器图", "group": "g1", "imageFit": "contain",
               "motion": {"enter": "fade"}, "overlays": [], "sketches": []},
    },
    "sceneMap": {"1": "s1", "2": "s1", "3": "s2"},
}, ensure_ascii=False)


def _fake_opus(user_prompt: str, system_prompt: str, model_id: str = "claude-opus-4-7") -> str:
    return _LINES_JSON if "脚本结构化" in system_prompt else _DIRECTOR_JSON


# --------------------------------------------------------------------------- #
# B1) lines performer：真实算法 + opus 桩 → 写出 beats 的 episode.json
# --------------------------------------------------------------------------- #
def test_run_lines_step_structures_beats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(perf, "_opus_structure", _fake_opus)
    jd = tmp_path / "job"
    (jd / "02_rw").mkdir(parents=True)
    (jd / "02_rw" / "draft.md").write_text("# 草稿\n\n正文若干。", encoding="utf-8")

    out = perf.run_lines_step(_noop, job_dir=str(jd))
    assert out["beats_count"] == 3
    ep = json.loads((jd / "02_rw" / "episode.json").read_text(encoding="utf-8"))
    assert [b["zh"] for b in ep["beats"]] == ["第一句字幕", "第二句字幕", "第三句字幕"]
    assert ep["meta"]["title"] == "测试标题"
    assert ep["scenes"] == {}                       # scenes 留给 storyboard
    assert all(b["scene"] == "" for b in ep["beats"])
    assert "audio" in ep and "playback" in ep       # 模板渲染配置被保留


def test_run_lines_step_missing_draft_raises(tmp_path: Path):
    with pytest.raises(ValueError, match="draft.md"):
        perf.run_lines_step(_noop, job_dir=str(tmp_path / "job"))


# 缺逗号 → json.loads 抛 "Expecting ',' delimiter"（复刻线上 opus 偶发的非法 JSON）
_BAD_LINES_JSON = '{"meta": {"title": "x"}, "beats": [{"zh": "第一句" "en": ""}]}'


def test_run_lines_step_retries_bad_json_then_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """opus 第一次出非法 JSON，带纠正提示重试后出合法 JSON → lines 成功。"""
    calls = {"n": 0}

    def flaky_opus(user_prompt: str, system_prompt: str, model_id: str = "claude-opus-4-7") -> str:
        calls["n"] += 1
        return _BAD_LINES_JSON if calls["n"] == 1 else _LINES_JSON

    monkeypatch.setattr(perf, "_opus_structure", flaky_opus)
    jd = tmp_path / "job"
    (jd / "02_rw").mkdir(parents=True)
    (jd / "02_rw" / "draft.md").write_text("# 草稿\n\n正文。", encoding="utf-8")

    out = perf.run_lines_step(_noop, job_dir=str(jd))
    assert calls["n"] == 2                       # 确实重试了一次
    assert out["beats_count"] == 3


def test_run_lines_step_raises_after_max_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """opus 始终出非法 JSON → 重试耗尽抛 RuntimeError（带 tail 便于排查）。"""
    monkeypatch.setattr(
        perf, "_opus_structure",
        lambda u, s, m="claude-opus-4-7": _BAD_LINES_JSON,
    )
    jd = tmp_path / "job"
    (jd / "02_rw").mkdir(parents=True)
    (jd / "02_rw" / "draft.md").write_text("# 草稿\n\n正文。", encoding="utf-8")

    with pytest.raises(RuntimeError, match="重试 3 次仍失败"):
        perf.run_lines_step(_noop, job_dir=str(jd))


# --------------------------------------------------------------------------- #
# B2) storyboard performer：真实算法 + director 桩 → 回填 scene + 写 scenes{}
# --------------------------------------------------------------------------- #
def test_run_storyboard_step_fills_scenes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(perf, "_opus_structure", _fake_opus)
    jd = tmp_path / "job"
    (jd / "02_rw").mkdir(parents=True)
    ep = {
        "meta": {"title": "T"}, "image": {}, "visual": {},
        "beats": [{"zh": "一", "en": "", "scene": ""},
                  {"zh": "二", "en": "", "scene": ""},
                  {"zh": "三", "en": "", "scene": ""}],
        "scenes": {},
    }
    (jd / "02_rw" / "episode.json").write_text(json.dumps(ep, ensure_ascii=False), encoding="utf-8")

    out = perf.run_storyboard_step(_noop, job_dir=str(jd))
    assert out["scenes_count"] == 2
    assert out["groups_count"] == 1          # s1/s2 同 group=g1 → 1 段（与 web 契约对齐）
    got = json.loads((jd / "02_rw" / "episode.json").read_text(encoding="utf-8"))
    assert set(got["scenes"]) == {"s1", "s2"}
    assert [b["scene"] for b in got["beats"]] == ["s1", "s1", "s2"]   # sceneMap 回填


# 缺逗号 + 缺 sceneMap → parse_director_output 抛 → 触发重试
_BAD_DIRECTOR_JSON = '{"scenes": {"s1": {"prompt": "x" "group": "g1"}}}'


def test_run_storyboard_step_retries_bad_json_then_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """director opus 第一次出非法 JSON，带纠正提示重试后出合法分镜 → storyboard 成功。"""
    calls = {"n": 0}

    def flaky_opus(user_prompt: str, system_prompt: str, model_id: str = "claude-opus-4-7") -> str:
        calls["n"] += 1
        return _BAD_DIRECTOR_JSON if calls["n"] == 1 else _DIRECTOR_JSON

    monkeypatch.setattr(perf, "_opus_structure", flaky_opus)
    jd = tmp_path / "job"
    (jd / "02_rw").mkdir(parents=True)
    ep = {
        "meta": {"title": "T"}, "image": {}, "visual": {},
        "beats": [{"zh": "一", "en": "", "scene": ""},
                  {"zh": "二", "en": "", "scene": ""},
                  {"zh": "三", "en": "", "scene": ""}],
        "scenes": {},
    }
    (jd / "02_rw" / "episode.json").write_text(json.dumps(ep, ensure_ascii=False), encoding="utf-8")

    out = perf.run_storyboard_step(_noop, job_dir=str(jd))
    assert calls["n"] == 2                       # 确实重试了一次
    assert out["scenes_count"] == 2


# --------------------------------------------------------------------------- #
# A) 引擎驱动真实 015 拓扑（含 content_edit 闸门）→ 共享 episode 耦合 → 端到端出 mp4
# --------------------------------------------------------------------------- #
def _asr_stub(on_progress, **p):
    on_progress("asr stub")
    return {"items": [{"index": 0, "article_relpath": "01_asr/a.md"}]}


def _rw_stub(on_progress, **p):
    jd = Path(p["job_dir"]); (jd / "02_rw").mkdir(parents=True, exist_ok=True)
    (jd / "02_rw" / "draft.md").write_text("# 草稿\n\n测试草稿正文。", encoding="utf-8")
    return {"drafts": [{"model_id": "opus", "status": "done"}], "selected_model_id": "opus"}


def _tts_stub(on_progress, **p):
    # 读 storyboard 后的 episode，给每个 beat 标 audioFile —— 证明 storyboard 之后共享 episode 仍流通
    ep_path = Path(p["job_dir"]) / "02_rw" / "episode.json"
    ep = json.loads(ep_path.read_text(encoding="utf-8"))
    for b in ep.get("beats", []):
        b["audioFile"] = f"04_tts/scene-{b.get('scene') or 's1'}.mp3"
    ep_path.write_text(json.dumps(ep, ensure_ascii=False), encoding="utf-8")
    return {"items": [], "scene_count": len(ep.get("scenes", {}))}


def _image_stub(on_progress, **p):
    return {"pictures_count": 0, "ok": 0}


def _render_stub(on_progress, **p):
    jd = Path(p["job_dir"])
    assert (jd / "02_rw" / "episode.json").is_file(), "render 前 episode 必须就位"
    out = jd / "06_render"; out.mkdir(parents=True, exist_ok=True)
    (out / "output.mp4").write_bytes(b"\x00stub-mp4")
    return {"video_relpath": "06_render/output.mp4", "output_path": str(out / "output.mp4")}


_RECIPE_015E2E = Recipe(
    recipe_id="rt015", name="015 perf e2e", template_renderer="paper_card_talk_015",
    steps=[
        RecipeStep(step_id="input", kind="input"),
        RecipeStep(step_id="asr", cmd="asr_stub", deps=["input"]),
        RecipeStep(step_id="rw", cmd="rw_stub", deps=["asr"], intervention="content_edit"),
        RecipeStep(step_id="lines", cmd="lines", deps=["rw"], intervention="content_edit"),
        RecipeStep(step_id="storyboard", cmd="storyboard", deps=["lines"], intervention="content_edit"),
        RecipeStep(step_id="tts", cmd="tts_stub", deps=["storyboard"], expensive=True),
        RecipeStep(step_id="image", cmd="image_stub", deps=["tts"], expensive=True),
        RecipeStep(step_id="preview", deps=["image"], intervention="content_edit"),  # 无 performer 的人工闸
        RecipeStep(step_id="render", cmd="render_stub", deps=["preview"], expensive=True),
        RecipeStep(step_id="download", kind="output", deps=["render"]),
    ],
)


def test_engine_drives_real_015_chain_to_mp4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(perf, "_opus_structure", _fake_opus)
    registry = {
        "asr_stub": _asr_stub, "rw_stub": _rw_stub,
        "lines": perf.run_lines_step, "storyboard": perf.run_storyboard_step,
        "tts_stub": _tts_stub, "image_stub": _image_stub, "render_stub": _render_stub,
    }
    job_dir = tmp_path / "video-jobs" / "j1"
    store = InstanceStore(tmp_path / "instances")
    runner = InstanceRunner(store, registry=registry, recipes={"rt015": _RECIPE_015E2E})
    iid = runner.create_instance("rt015").meta.instance_id

    gated_observed: list[str] = []

    async def _drive():
        for sid in _RECIPE_015E2E.topological_order():
            st = await runner.run_step(iid, sid, {"job_dir": str(job_dir)})
            if st.status == "awaiting_review":                 # content_edit 闸：人/driver 放行
                gated_observed.append(sid)
                st = await runner.approve_step(iid, sid, "approved")
            assert st.status == "done", f"{sid} 终态非 done: {st.status} ({st.error})"
        return await runner.finalize_instance(iid)

    meta = asyncio.run(_drive())
    assert meta.status == "completed"

    # 闸门是 load-bearing 断言：真正 fire 的 content_edit 步必须正好 == recipe 声明的那些
    # （删任一 intervention 就会让此断言变红，而非被 if 静默容忍）
    expected_gates = {s.step_id for s in _RECIPE_015E2E.steps if s.intervention}
    assert set(gated_observed) == expected_gates == {"rw", "lines", "storyboard", "preview"}

    # 产物链：lines 写 beats → storyboard 回填 scenes → tts 标 audioFile → render 出 mp4
    ep = json.loads((job_dir / "02_rw" / "episode.json").read_text(encoding="utf-8"))
    assert len(ep["beats"]) == 3
    assert all(b["scene"] for b in ep["beats"])        # storyboard 回填了 scene
    assert set(ep["scenes"]) == {"s1", "s2"}
    assert all(b.get("audioFile") for b in ep["beats"])  # tts 之后共享 episode 仍流通
    assert (job_dir / "06_render" / "output.mp4").is_file()   # 端到端出 mp4


# --------------------------------------------------------------------------- #
# C) 重步骤 performer（tts/image/render）：真实编排 + 外部 seam 注桩
# --------------------------------------------------------------------------- #
def _seed_episode(jd: Path, ep: dict) -> None:
    (jd / "02_rw").mkdir(parents=True, exist_ok=True)
    (jd / "02_rw" / "episode.json").write_text(json.dumps(ep, ensure_ascii=False), encoding="utf-8")


def test_run_tts_step_rebuilds_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    jd = tmp_path / "job"
    _seed_episode(jd, {"beats": [{"zh": "一", "scene": "s1"}, {"zh": "二", "scene": "s1"},
                                 {"zh": "三", "scene": "s2"}], "scenes": {}})

    def _fake_tts(*, script, episode_path, audio_dir, on_line, only=None, force=False):
        on_line("tts stub")
        e = json.loads(Path(episode_path).read_text(encoding="utf-8"))
        for b in e["beats"]:                       # 模拟 tts_gen 写回 scene mp3 + 时间戳
            b["audioFile"] = f"04_tts/scene-{b['scene']}.mp3"
            b["audioStart"] = 0.0
            b["audioEnd"] = 1.0
        Path(episode_path).write_text(json.dumps(e, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(perf, "_run_tts_gen", _fake_tts)
    out = perf.run_tts_step(_noop, job_dir=str(jd))
    assert out["mode"] == "segmented"
    assert out["scene_count"] == 2                 # s1 / s2
    assert len(out["items"]) == 3
    assert out["items"][0]["audio_relpath"] == "04_tts/scene-s1.mp3"


def test_run_image_step_orchestrates_scenes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    jd = tmp_path / "job"
    _seed_episode(jd, {
        "beats": [{"scene": "ch1"}, {"scene": "s1"}, {"scene": "s2"}],
        "scenes": {
            "ch1": {"prompt": "章节卡"},                                  # ch* → 跳过出图
            "s1": {"prompt": "场景一", "sketches": [{"prompt": "简笔画1"}]},
            "s2": {"prompt": ""},                                         # 空 prompt → fail
        },
        "image": {"size": "1536x1024", "quality": "auto", "sketchStylePrefix": "白底黑剪影"},
    })
    calls: list[str] = []

    def _fake_gen(*, scene_id, prompt, size, quality, target, job_id):
        calls.append(scene_id)
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"webp")

    monkeypatch.setattr(perf, "_gen_scene_image", _fake_gen)
    out = perf.run_image_step(_noop, job_dir=str(jd))
    assert (out["ok"], out["skipped"], out["failed"]) == (1, 0, 1)   # s1 ok / s2 空→fail
    assert out["sketch_ok"] == 1
    assert "ch1" not in calls                       # 章节卡不出图
    assert "s1" in calls and "s1-sk1" in calls      # 容器图 + 简笔画
    assert (jd / "03_image" / "s1.webp").is_file()
    assert (jd / "03_image" / "s1-sk1.webp").is_file()


def test_run_image_step_idempotent_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    jd = tmp_path / "job"
    (jd / "03_image").mkdir(parents=True)
    (jd / "03_image" / "s1.webp").write_bytes(b"existing")     # 预先存在 → 跳过、不重生
    _seed_episode(jd, {"beats": [{"scene": "s1"}], "scenes": {"s1": {"prompt": "场景一"}}, "image": {}})
    monkeypatch.setattr(perf, "_gen_scene_image",
                        lambda **k: pytest.fail("不应重生已存在的容器图"))
    out = perf.run_image_step(_noop, job_dir=str(jd))
    assert (out["ok"], out["skipped"], out["failed"]) == (0, 1, 0)


def test_run_render_step_invokes_render_015(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    jd = tmp_path / "job"
    _seed_episode(jd, {})
    (jd / "04_tts").mkdir(parents=True)
    (jd / "04_tts" / "scene-s1.mp3").write_bytes(b"mp3")
    captured: dict = {}

    def _fake_render(**kw):
        captured.update(kw)
        Path(kw["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kw["output_path"]).write_bytes(b"mp4")
        return {"output_path": kw["output_path"], "video_size_bytes": 3, "workdir": kw["workdir"]}

    monkeypatch.setattr(perf, "_render_run", _fake_render)
    out = perf.run_render_step(_noop, job_dir=str(jd))
    assert out["video_relpath"] == "06_render/output.mp4"
    assert out["video_size_bytes"] == 3
    assert captured["episode_path"].endswith("02_rw/episode.json")
    assert captured["audio_dir"].endswith("04_tts")
    assert captured["picture_dir"] is None          # 03_image 不存在 → None（picture_dir 可选）


def test_run_render_step_missing_audio_raises(tmp_path: Path):
    jd = tmp_path / "job"
    _seed_episode(jd, {})
    with pytest.raises(ValueError, match="04_tts"):
        perf.run_render_step(_noop, job_dir=str(jd))


def test_run_render_step_forwards_existing_picture_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 03_image 存在 → picture_dir 原样转发（钉死 .is_dir()→forward，防漂移成 .is_file()）
    jd = tmp_path / "job"
    _seed_episode(jd, {})
    (jd / "04_tts").mkdir(parents=True)
    (jd / "04_tts" / "scene-s1.mp3").write_bytes(b"mp3")
    (jd / "03_image").mkdir(parents=True)
    captured: dict = {}

    def _fake_render(**kw):
        captured.update(kw)
        Path(kw["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kw["output_path"]).write_bytes(b"mp4")
        return {"output_path": kw["output_path"], "video_size_bytes": 1, "workdir": kw["workdir"]}

    monkeypatch.setattr(perf, "_render_run", _fake_render)
    perf.run_render_step(_noop, job_dir=str(jd))
    assert captured["picture_dir"] is not None and captured["picture_dir"].endswith("03_image")


# 覆盖 image 的两条易漂移分支：单 scene 生成异常被捕获（部分成功）+ 全失败兜底 raise
def test_run_image_step_captures_generation_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    jd = tmp_path / "job"
    _seed_episode(jd, {
        "beats": [{"scene": "s1"}, {"scene": "s2"}],
        "scenes": {"s1": {"prompt": "好场景"}, "s2": {"prompt": "坏场景"}},
        "image": {},
    })

    def _gen(*, scene_id, prompt, size, quality, target, job_id):
        if scene_id == "s2":
            raise RuntimeError("gpt-image boom")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"webp")

    monkeypatch.setattr(perf, "_gen_scene_image", _gen)
    out = perf.run_image_step(_noop, job_dir=str(jd))
    assert (out["ok"], out["failed"]) == (1, 1)          # s1 成、s2 异常被捕获，run 仍返回
    s2 = next(it for it in out["items"] if it["scene_id"] == "s2")
    assert s2["image_relpath"] is None and "boom" in s2["error"]


def test_run_image_step_all_failed_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    jd = tmp_path / "job"
    _seed_episode(jd, {"beats": [{"scene": "s1"}], "scenes": {"s1": {"prompt": "唯一场景"}}, "image": {}})
    monkeypatch.setattr(perf, "_gen_scene_image",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="all .* scene image generations failed"):
        perf.run_image_step(_noop, job_dir=str(jd))


# --------------------------------------------------------------------------- #
# D) run_asr_step：hermetic，stub _run_video_pipeline_fn + _polish_transcript
# --------------------------------------------------------------------------- #

def _make_asr_seams(tmp_path: Path):
    """返回 (stub_pipeline_fn, stub_polish_fn)，会自动在 item_dir 写 result.json + transcript。

    stub_pipeline_fn 的行为：
      - 在 output_dir/deliverables/result.json 写 rawTranscriptPath 指向真实 transcript 文件。
      - 对 on_line 推一行伪进度（测试 _asr_stage_label 走通）。
    stub_polish_fn 的行为：
      - 把 output_path 写成 "# 整理文章\n\n来源：{transcript_path}"。
    """
    def stub_pipeline(*, pipeline_script, url, output_dir, on_line):
        output_dir = Path(output_dir)
        deliverables = output_dir / "deliverables"
        deliverables.mkdir(parents=True, exist_ok=True)
        raw_dir = output_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        # 写伪 transcript
        transcript_file = deliverables / "transcript.txt"
        transcript_file.write_text(f"转写稿 url={url}", encoding="utf-8")
        result = {"rawTranscriptPath": str(transcript_file)}
        (deliverables / "result.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
        # 推一行"下载"进度，覆盖 _asr_stage_label 的"下载视频"分支
        on_line("download video starting")

    def stub_polish(*, transcript_path, output_path, title_hint=""):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            f"# 整理文章\n\n来源：{transcript_path}", encoding="utf-8"
        )

    return stub_pipeline, stub_polish


def test_run_asr_step_normal_two_urls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """正常 2 条 URL → items 齐，article_relpath 指向 article.md（polish 成功路径）。"""
    jd = tmp_path / "video-jobs" / "job1"
    stub_pipeline, stub_polish = _make_asr_seams(tmp_path)
    monkeypatch.setattr(perf, "_run_video_pipeline_fn", stub_pipeline)
    monkeypatch.setattr(perf, "_polish_transcript", stub_polish)
    # NOF_VIDEO_PIPELINE_SCRIPT 让 performer 不去找真实 video_pipeline.py
    monkeypatch.setenv("NOF_VIDEO_PIPELINE_SCRIPT", __file__)  # 任意已存在的文件

    urls = ["https://example.com/a", "https://example.com/b"]
    out = perf.run_asr_step(_noop, job_dir=str(jd), urls=urls)

    assert len(out["items"]) == 2
    assert out["asr_dir"].endswith("01_asr")

    for item in out["items"]:
        assert item["error"] is None
        # article_relpath 应指向 polish 写出的 article.md
        assert item["article_relpath"].endswith("article.md")
        # transcript_relpath 指向 deliverables/transcript.txt（相对 job_dir）
        assert "transcript.txt" in item["transcript_relpath"]
        # 对应文件真实存在
        assert (jd / item["article_relpath"]).is_file()
        assert (jd / item["transcript_relpath"]).is_file()


def test_run_asr_step_download_cache_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """下载缓存命中：预先在 _downloads/<md5>/ 放 mp4 → raw/ 里出现 symlink，
    video_pipeline stub 仍被调（fast-path 由 video_pipeline 自己判断，performer 不跳过调用）。"""
    import hashlib as _hashlib

    jd = tmp_path / "video-jobs" / "job1"
    url = "https://example.com/cached"
    url_md5 = _hashlib.md5(url.encode()).hexdigest()
    # 预先在 _downloads/<md5>/ 放一个假 mp4
    cache_dir = tmp_path / "video-jobs" / "_downloads" / url_md5
    cache_dir.mkdir(parents=True)
    (cache_dir / "video.mp4").write_bytes(b"fake-mp4-content")

    pipeline_called = []

    def stub_pipeline(*, pipeline_script, url, output_dir, on_line):
        pipeline_called.append(url)
        output_dir = Path(output_dir)
        deliverables = output_dir / "deliverables"
        deliverables.mkdir(parents=True, exist_ok=True)
        t = deliverables / "transcript.txt"
        t.write_text("t", encoding="utf-8")
        (deliverables / "result.json").write_text(
            json.dumps({"rawTranscriptPath": str(t)}), encoding="utf-8"
        )

    stub_pipeline2, stub_polish = _make_asr_seams(tmp_path)
    monkeypatch.setattr(perf, "_run_video_pipeline_fn", stub_pipeline)
    monkeypatch.setattr(perf, "_polish_transcript", stub_polish)
    monkeypatch.setenv("NOF_VIDEO_PIPELINE_SCRIPT", __file__)

    out = perf.run_asr_step(_noop, job_dir=str(jd), urls=[url])

    # video_pipeline stub 必须被调用（performer 不跳过，只是提前 symlink）
    assert pipeline_called == [url]
    # raw/ 里应该有对 cache 的 symlink（performer 在调 pipeline 前就 link 进去了）
    raw_dir = jd / "01_asr" / "1" / "raw"
    symlinks = list(raw_dir.glob("*.mp4"))
    assert len(symlinks) == 1
    assert symlinks[0].is_symlink()
    # item 成功
    assert out["items"][0]["error"] is None


def test_run_asr_step_url_stamp_changes_clears_old(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """URL stamp 变更：item_dir 里已有旧 stamp → 清掉旧产物，重新处理。"""
    import hashlib as _hashlib

    jd = tmp_path / "video-jobs" / "job1"
    stub_pipeline, stub_polish = _make_asr_seams(tmp_path)
    monkeypatch.setattr(perf, "_run_video_pipeline_fn", stub_pipeline)
    monkeypatch.setattr(perf, "_polish_transcript", stub_polish)
    monkeypatch.setenv("NOF_VIDEO_PIPELINE_SCRIPT", __file__)

    url = "https://example.com/new-url"
    # 预先写 item_dir，放一个旧 stamp（不同 URL 的 md5 短码）
    item_dir = jd / "01_asr" / "1"
    item_dir.mkdir(parents=True)
    stale_marker = item_dir / "stale-marker.txt"
    stale_marker.write_text("should-be-deleted", encoding="utf-8")
    (item_dir / ".url-stamp").write_text("000000000000", encoding="utf-8")  # 与新 URL 不同

    progress_msgs: list[str] = []
    out = perf.run_asr_step(progress_msgs.append, job_dir=str(jd), urls=[url])

    # 旧产物（stale-marker.txt）应被删掉（shutil.rmtree + mkdir）
    assert not stale_marker.is_file()
    # 仍正常完成
    assert out["items"][0]["error"] is None
    # 进度消息中应有"URL 已变更"
    assert any("URL 已变更" in m for m in progress_msgs)


def test_run_asr_step_single_item_failure_captured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """单条 URL 失败（pipeline 抛异常）→ item.error 被捕获，其余成功仍返回。"""
    jd = tmp_path / "video-jobs" / "job1"
    _, stub_polish = _make_asr_seams(tmp_path)
    call_count = [0]

    def stub_pipeline(*, pipeline_script, url, output_dir, on_line):
        call_count[0] += 1
        output_dir = Path(output_dir)
        if "bad" in url:
            raise RuntimeError("bad url boom")
        deliverables = output_dir / "deliverables"
        deliverables.mkdir(parents=True, exist_ok=True)
        t = deliverables / "transcript.txt"
        t.write_text("ok transcript", encoding="utf-8")
        (deliverables / "result.json").write_text(
            json.dumps({"rawTranscriptPath": str(t)}), encoding="utf-8"
        )

    monkeypatch.setattr(perf, "_run_video_pipeline_fn", stub_pipeline)
    monkeypatch.setattr(perf, "_polish_transcript", stub_polish)
    monkeypatch.setenv("NOF_VIDEO_PIPELINE_SCRIPT", __file__)

    urls = ["https://example.com/ok", "https://example.com/bad"]
    out = perf.run_asr_step(_noop, job_dir=str(jd), urls=urls)

    # 两条都处理（call_count 仅 bad 那条 pipeline 失败但 ok 那条成功）
    ok_items = [it for it in out["items"] if not it["error"]]
    fail_items = [it for it in out["items"] if it["error"]]
    assert len(ok_items) == 1 and ok_items[0]["url"] == "https://example.com/ok"
    assert len(fail_items) == 1 and "boom" in fail_items[0]["error"]
    # 全量函数仍返回（没有 raise），因为有 1 条成功
    assert out["asr_dir"].endswith("01_asr")


def test_run_asr_step_all_failed_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """全部 URL 失败 → raise RuntimeError（包含失败数）。"""
    jd = tmp_path / "video-jobs" / "job1"
    monkeypatch.setattr(
        perf, "_run_video_pipeline_fn",
        lambda *, pipeline_script, url, output_dir, on_line: (_ for _ in ()).throw(RuntimeError("always fail"))
    )
    monkeypatch.setenv("NOF_VIDEO_PIPELINE_SCRIPT", __file__)

    with pytest.raises(RuntimeError, match="全部"):
        perf.run_asr_step(_noop, job_dir=str(jd), urls=["https://example.com/x"])


def test_run_asr_step_polish_failure_falls_back_to_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """opus polish 失败 → fallback：article_relpath 指向 transcript（非 article.md），item 仍成功。"""
    jd = tmp_path / "video-jobs" / "job1"
    stub_pipeline, _ = _make_asr_seams(tmp_path)

    def boom_polish(*, transcript_path, output_path, title_hint=""):
        raise RuntimeError("opus down")

    monkeypatch.setattr(perf, "_run_video_pipeline_fn", stub_pipeline)
    monkeypatch.setattr(perf, "_polish_transcript", boom_polish)
    monkeypatch.setenv("NOF_VIDEO_PIPELINE_SCRIPT", __file__)

    progress: list[str] = []
    out = perf.run_asr_step(progress.append, job_dir=str(jd), urls=["https://example.com/a"])
    item = out["items"][0]
    assert item["error"] is None                                   # fallback 仍算成功
    assert item["article_relpath"].endswith("transcript.txt")      # 指向 transcript 而非 article.md
    assert not (jd / "01_asr" / "1" / "article.md").is_file()      # article.md 没写出
    assert any("fallback" in m for m in progress)


def test_run_asr_step_cache_miss_populates_downloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """缓存 MISS：pipeline 在 raw/ 产出真 mp4 → 迁移到 _downloads/<md5>/ + 原位留 symlink（喂下个 job）。"""
    import hashlib as _hashlib

    jd = tmp_path / "video-jobs" / "job1"
    url = "https://example.com/fresh"
    url_md5 = _hashlib.md5(url.encode()).hexdigest()
    _, stub_polish = _make_asr_seams(tmp_path)

    def stub_pipeline(*, pipeline_script, url, output_dir, on_line):
        out = Path(output_dir)
        deliverables = out / "deliverables"
        deliverables.mkdir(parents=True, exist_ok=True)
        t = deliverables / "transcript.txt"
        t.write_text("t", encoding="utf-8")
        (deliverables / "result.json").write_text(
            json.dumps({"rawTranscriptPath": str(t)}), encoding="utf-8"
        )
        raw = out / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "video.mp4").write_bytes(b"fresh-mp4")     # 真 mp4（非 symlink）

    monkeypatch.setattr(perf, "_run_video_pipeline_fn", stub_pipeline)
    monkeypatch.setattr(perf, "_polish_transcript", stub_polish)
    monkeypatch.setenv("NOF_VIDEO_PIPELINE_SCRIPT", __file__)

    out = perf.run_asr_step(_noop, job_dir=str(jd), urls=[url])
    assert out["items"][0]["error"] is None
    cached = tmp_path / "video-jobs" / "_downloads" / url_md5 / "video.mp4"
    assert cached.is_file() and not cached.is_symlink() and cached.read_bytes() == b"fresh-mp4"
    raw_link = jd / "01_asr" / "1" / "raw" / "video.mp4"
    assert raw_link.is_symlink() and raw_link.resolve() == cached.resolve()


# --------------------------------------------------------------------------- #
# E) run_rw_step：hermetic，stub _invoke_rw（async）
# --------------------------------------------------------------------------- #

def _seed_asr_items(jd: Path, items_data: list[dict]) -> list[dict[str, Any]]:
    """在 job_dir 下写 article 文件，返回 asr_items 列表（含 article_relpath）。"""
    asr_dir = jd / "01_asr"
    items = []
    for i, d in enumerate(items_data, start=1):
        item_dir = asr_dir / str(i)
        item_dir.mkdir(parents=True, exist_ok=True)
        article = item_dir / "article.md"
        article.write_text(d.get("content", f"文章内容 {i}"), encoding="utf-8")
        items.append({
            "index": i,
            "url": d.get("url", f"https://example.com/{i}"),
            "title": d.get("title", f"标题{i}"),
            "author": "",
            "transcript_relpath": f"01_asr/{i}/transcript.txt",
            "article_relpath": f"01_asr/{i}/article.md",
            "error": None,
        })
    return items


def test_run_rw_step_partial_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """部分成功：opus 成功、gpt5/_ModelUnavailable、gemini_local/_ModelUnavailable、deepseek 普通异常。

    验证：drafts 状态对、draft.md 写盘、success_count 准确。
    """
    jd = tmp_path / "job1"
    asr_items = _seed_asr_items(jd, [{"content": "测试素材文章。"}])

    # stub_invoke 按模型 id 返回不同结果
    async def stub_invoke(cand, user_prompt, system_prompt, on_progress, on_status=None):
        mid = cand["id"]
        if mid == "opus":
            return "# 改写稿 A\n\n正文内容。"
        if mid == "gpt5":
            from ncds_opus_factory.server.pipeline_runner import _ModelUnavailable as MU
            raise MU("本机未安装 scodex")
        if mid == "gemini_local":
            from ncds_opus_factory.server.pipeline_runner import _ModelUnavailable as MU
            raise MU("~/.gemini/g.sh 未安装")
        # deepseek → 普通异常（非 _ModelUnavailable）
        raise RuntimeError("deepseek API timeout")

    monkeypatch.setattr(perf, "_invoke_rw", stub_invoke)

    out = perf.run_rw_step(_noop, job_dir=str(jd), asr_items=asr_items)

    assert out["success_count"] == 1
    assert out["candidate_count"] == 4
    assert out["profile"] == "freestyle"        # DEFAULT_RW_PROFILE 的具体值，非同义反复

    drafts = {d["model_id"]: d for d in out["drafts"]}
    # opus 成功，draft.md 写盘
    assert drafts["opus"]["status"] == "success"
    assert drafts["opus"]["draft_relpath"] == "02_rw/opus/draft.md"
    assert (jd / "02_rw" / "opus" / "draft.md").is_file()
    assert "改写稿 A" in (jd / "02_rw" / "opus" / "draft.md").read_text(encoding="utf-8")

    # gpt5 / gemini_local → failed（_ModelUnavailable 包成 failed，不算普通 failed）
    assert drafts["gpt5"]["status"] == "failed"
    assert "不可用" in drafts["gpt5"]["reason"]
    assert drafts["gemini_local"]["status"] == "failed"

    # deepseek 普通异常 → failed
    assert drafts["deepseek"]["status"] == "failed"
    assert "timeout" in drafts["deepseek"]["reason"]


def test_run_rw_step_all_failed_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """全部模型失败 → raise RuntimeError 包含失败详情。"""
    jd = tmp_path / "job2"
    asr_items = _seed_asr_items(jd, [{"content": "素材。"}])

    async def stub_all_fail(cand, user_prompt, system_prompt, on_progress, on_status=None):
        raise RuntimeError(f"模型 {cand['id']} 全挂")

    monkeypatch.setattr(perf, "_invoke_rw", stub_all_fail)

    with pytest.raises(RuntimeError, match="全部失败"):
        perf.run_rw_step(_noop, job_dir=str(jd), asr_items=asr_items)


def test_run_rw_step_profile_passed_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """profile 参数透传到返回值 + 影响 _build_rw_prompt（不验证 prompt 内容，只验证返回字段）。"""
    jd = tmp_path / "job3"
    asr_items = _seed_asr_items(jd, [{"content": "内容。"}])

    async def stub_one_ok(cand, user_prompt, system_prompt, on_progress, on_status=None):
        if cand["id"] == "opus":
            return "# 仅 opus 成功\n\n正文。"
        from ncds_opus_factory.server.pipeline_runner import _ModelUnavailable as MU
        raise MU("跳过")

    monkeypatch.setattr(perf, "_invoke_rw", stub_one_ok)

    out = perf.run_rw_step(_noop, job_dir=str(jd), asr_items=asr_items, profile="paper_card_talk")
    assert out["profile"] == "paper_card_talk"


def test_run_rw_step_codeblock_stripped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """模型输出带 ```json ... ``` 包裹 → 去壳后写盘（与原版 make_draft 对齐）。"""
    jd = tmp_path / "job4"
    asr_items = _seed_asr_items(jd, [{"content": "内容。"}])

    raw_with_fence = "```json\n# 被包裹的改写稿\n\n正文。\n```"

    async def stub_fence(cand, user_prompt, system_prompt, on_progress, on_status=None):
        if cand["id"] == "opus":
            return raw_with_fence
        from ncds_opus_factory.server.pipeline_runner import _ModelUnavailable as MU
        raise MU("skip")

    monkeypatch.setattr(perf, "_invoke_rw", stub_fence)

    out = perf.run_rw_step(_noop, job_dir=str(jd), asr_items=asr_items)
    assert out["success_count"] == 1
    draft_content = (jd / "02_rw" / "opus" / "draft.md").read_text(encoding="utf-8")
    # ``` 包裹应被去掉
    assert "```" not in draft_content
    assert "被包裹的改写稿" in draft_content
