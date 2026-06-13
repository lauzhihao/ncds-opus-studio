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
