"""E1-b2 slice-1：015 step-performer + 引擎驱动真实 015 链端到端验证（hermetic）。

- lines：复用 shared fallback 边界（LLM 调用注入桩）；storyboard：复用 director 结构化算法。
- e2e：引擎按真实 015 拓扑（含 content_edit 闸门）驱动 lines/storyboard 真实 performer
  + 重步骤桩（asr/rw/tts/image/render），经共享 02_rw/episode.json 耦合，端到端出 mp4。
  不依赖 015 样例素材 / 真 LLM / node / ffmpeg。
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


@pytest.fixture(autouse=True)
def _stub_rw_qc(monkeypatch: pytest.MonkeyPatch):
    """rw 质检闸门（ai_taste + quality_rubric 调 opus）默认 stub 掉：这些 step 测试只验
    出稿编排，质检逻辑由 test_shenkuo_collect 等专门覆盖；避免真调 opus 拖慢 / 依赖环境。"""
    monkeypatch.setattr(perf, "_apply_rw_qc", lambda *a, **k: {})


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


def _fake_lines_fallback(user_prompt: str, system_prompt: str, on_progress, **_: Any) -> Any:
    on_progress("正在准备视觉方案...")
    return json.loads(_LINES_JSON)


# --------------------------------------------------------------------------- #
# B1) lines performer：真实编排 + LLM fallback 桩 → 写出 beats 的 episode.json
# --------------------------------------------------------------------------- #
def test_run_lines_step_structures_beats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(perf, "structure_lines_json_with_fallback", _fake_lines_fallback)
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


def test_run_lines_step_passes_progress_to_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    progress: list[str] = []
    prompts: dict[str, str] = {}

    def fake_fallback(user_prompt: str, system_prompt: str, on_progress, **_: Any) -> Any:
        prompts["user"] = user_prompt
        prompts["system"] = system_prompt
        on_progress("正在准备视觉方案...")
        return json.loads(_LINES_JSON)

    monkeypatch.setattr(perf, "structure_lines_json_with_fallback", fake_fallback)
    jd = tmp_path / "job"
    (jd / "02_rw").mkdir(parents=True)
    (jd / "02_rw" / "draft.md").write_text("# 草稿\n\n正文。", encoding="utf-8")

    out = perf.run_lines_step(progress.append, job_dir=str(jd))
    assert "脚本结构化助手" in prompts["system"]
    assert "正文。" in prompts["user"]
    assert progress == ["正在准备视觉方案...", "视觉方案准备完成：3 句"]
    assert out["beats_count"] == 3


def test_run_lines_step_propagates_fallback_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fail_fallback(user_prompt: str, system_prompt: str, on_progress, **_: Any) -> Any:
        raise RuntimeError("视觉方案准备暂时失败：备用通道都没有成功，请稍后重试。")

    monkeypatch.setattr(perf, "structure_lines_json_with_fallback", fail_fallback)
    jd = tmp_path / "job"
    (jd / "02_rw").mkdir(parents=True)
    (jd / "02_rw" / "draft.md").write_text("# 草稿\n\n正文。", encoding="utf-8")

    with pytest.raises(RuntimeError, match="视觉方案准备暂时失败"):
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
    monkeypatch.setattr(perf, "structure_lines_json_with_fallback", _fake_lines_fallback)
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
# D) run_asr_step：hermetic，stub 沈括 collect_one 快采
# --------------------------------------------------------------------------- #

def test_run_asr_step_normal_two_urls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """正常 2 条 URL → 调 collect_one 快采，返回 collected/items 兼容字段。"""
    jd = tmp_path / "video-jobs" / "job1"
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(perf, "_resolve_aweme_id", lambda url: url.rsplit("/", 1)[-1])
    monkeypatch.setattr(perf, "_fetch_one_video_detail", lambda aweme_id: {"aweme_id": aweme_id})
    monkeypatch.setattr(perf, "_extract_meta", lambda detail: {"desc": f"标题 {detail['aweme_id']}"})

    def fake_collect_one(aweme_id, author_dir, **kwargs):
        calls.append({"aweme_id": aweme_id, "author_dir": author_dir, **kwargs})
        return {
            "aweme_id": aweme_id,
            "text": f"采集文案 {aweme_id}",
            "desc": kwargs["meta"].get("desc"),
            "status": {"ok": True},
        }

    monkeypatch.setattr(perf, "_collect_one", fake_collect_one)

    urls = ["https://example.com/a", "https://example.com/b"]
    out = perf.run_asr_step(_noop, job_dir=str(jd), urls=urls)

    assert out["items"] is out["collected"]
    assert out["collect_dir"].endswith("01_collect")
    assert [it["aweme_id"] for it in out["collected"]] == ["a", "b"]
    assert [it["index"] for it in out["collected"]] == [1, 2]
    assert [it["url"] for it in out["collected"]] == urls
    assert all(c["author_dir"] == jd / "01_collect" for c in calls)
    assert all(c["do_audio"] is False and c["do_frames"] is False for c in calls)


def test_run_asr_step_metadata_failure_uses_share_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """元数据获取失败不阻塞 collect_one；shares title/author 作为 meta 兜底。"""
    jd = tmp_path / "video-jobs" / "job1"
    seen: dict[str, Any] = {}
    monkeypatch.setattr(perf, "_resolve_aweme_id", lambda _url: "aweme1")
    monkeypatch.setattr(perf, "_fetch_one_video_detail", lambda _aweme_id: (_ for _ in ()).throw(RuntimeError("meta down")))
    monkeypatch.setattr(perf, "_extract_meta", lambda _detail: {})

    def fake_collect_one(aweme_id, author_dir, **kwargs):
        seen.update(kwargs["meta"])
        return {"aweme_id": aweme_id, "text": "兜底文案", "status": {}}

    monkeypatch.setattr(perf, "_collect_one", fake_collect_one)
    progress: list[str] = []
    out = perf.run_asr_step(
        progress.append,
        job_dir=str(jd),
        urls=["https://example.com/a"],
        shares=[{"url": "https://example.com/a", "title": "分享标题", "author": "作者"}],
    )

    assert out["collected"][0]["error"] is None
    assert seen["desc"] == "分享标题"
    assert seen["author"] == "作者"
    assert any("元数据获取失败" in msg for msg in progress)


def test_run_asr_step_single_item_failure_captured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """单条 URL 失败 → item.error 被捕获，其余成功仍返回。"""
    jd = tmp_path / "video-jobs" / "job1"
    monkeypatch.setattr(perf, "_resolve_aweme_id", lambda url: "bad" if "bad" in url else "ok")
    monkeypatch.setattr(perf, "_fetch_one_video_detail", lambda aweme_id: {"aweme_id": aweme_id})
    monkeypatch.setattr(perf, "_extract_meta", lambda detail: {"desc": detail["aweme_id"]})

    def fake_collect_one(aweme_id, _author_dir, **_kwargs):
        if aweme_id == "bad":
            raise RuntimeError("bad url boom")
        return {"aweme_id": aweme_id, "text": "ok transcript", "status": {}}

    monkeypatch.setattr(perf, "_collect_one", fake_collect_one)

    urls = ["https://example.com/ok", "https://example.com/bad"]
    out = perf.run_asr_step(_noop, job_dir=str(jd), urls=urls)

    # 两条都处理（call_count 仅 bad 那条 pipeline 失败但 ok 那条成功）
    ok_items = [it for it in out["items"] if not it["error"]]
    fail_items = [it for it in out["items"] if it["error"]]
    assert len(ok_items) == 1 and ok_items[0]["url"] == "https://example.com/ok"
    assert len(fail_items) == 1 and "boom" in fail_items[0]["error"]
    # 全量函数仍返回（没有 raise），因为有 1 条成功
    assert out["collect_dir"].endswith("01_collect")


def test_run_asr_step_all_failed_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """全部 URL 失败 → raise RuntimeError（包含失败数）。"""
    jd = tmp_path / "video-jobs" / "job1"
    monkeypatch.setattr(perf, "_resolve_aweme_id", lambda _url: None)

    with pytest.raises(RuntimeError, match="全部"):
        perf.run_asr_step(_noop, job_dir=str(jd), urls=["https://example.com/x"])


def test_run_asr_step_task_cancelled_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """TaskCancelled 不能被 per-item failure 捕获吞掉。"""
    jd = tmp_path / "video-jobs" / "job1"
    monkeypatch.setattr(perf, "_resolve_aweme_id", lambda _url: "aweme1")
    monkeypatch.setattr(perf, "_fetch_one_video_detail", lambda aweme_id: {"aweme_id": aweme_id})
    monkeypatch.setattr(perf, "_extract_meta", lambda _detail: {})
    monkeypatch.setattr(
        perf,
        "_collect_one",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(perf._cancel.TaskCancelled("stop")),
    )

    with pytest.raises(perf._cancel.TaskCancelled):
        perf.run_asr_step(_noop, job_dir=str(jd), urls=["https://example.com/a"])


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


def test_run_rw_step_opus_deepseek_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """MODEL_CANDIDATES 全部成功 →
    success/candidate count 跟随候选表、各自 draft.md 写盘、drafts 按 MODEL_CANDIDATES 顺序。"""
    jd = tmp_path / "job1"
    asr_items = _seed_asr_items(jd, [{"content": "测试素材文章。"}])
    expected_ids = [c["id"] for c in perf.MODEL_CANDIDATES]

    async def stub_invoke(cand, user_prompt, system_prompt, on_progress, on_status=None):
        # 每个候选回各自可辨识的稿，验证 model_dir 隔离写盘。
        return f"# 改写稿 {cand['id']}\n\n正文内容。"

    monkeypatch.setattr(perf, "_invoke_rw", stub_invoke)
    # 质检闸门走真 ai_taste/quality_rubric 会调 opus（慢/依赖环境）；本测试只验出稿编排，跳过质检。
    monkeypatch.setattr(perf, "_apply_rw_qc", lambda *a, **k: {})

    out = perf.run_rw_step(_noop, job_dir=str(jd), asr_items=asr_items)

    assert out["success_count"] == len(expected_ids)
    assert out["candidate_count"] == len(expected_ids)
    # drafts 顺序锁定 MODEL_CANDIDATES，前端默认 tab 落第一个候选。
    assert [d["model_id"] for d in out["drafts"]] == expected_ids

    drafts = {d["model_id"]: d for d in out["drafts"]}
    for mid in expected_ids:
        assert drafts[mid]["status"] == "success"
        assert drafts[mid]["draft_relpath"] == f"02_rw/{mid}/draft.md"
        path = jd / "02_rw" / mid / "draft.md"
        assert path.is_file()
        assert f"改写稿 {mid}" in path.read_text(encoding="utf-8")


def test_run_rw_step_all_failed_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """全部模型失败 → raise RuntimeError 包含失败详情。"""
    jd = tmp_path / "job2"
    asr_items = _seed_asr_items(jd, [{"content": "素材。"}])

    async def stub_all_fail(cand, user_prompt, system_prompt, on_progress, on_status=None):
        raise RuntimeError(f"模型 {cand['id']} 全挂")

    monkeypatch.setattr(perf, "_invoke_rw", stub_all_fail)

    with pytest.raises(RuntimeError, match="全部失败"):
        perf.run_rw_step(_noop, job_dir=str(jd), asr_items=asr_items)


def test_run_rw_step_ignores_legacy_profile_kwarg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """体裁 profile 已废（写作改 domain 驱动）：传入 legacy ``profile`` kwarg 应被静默忽略——
    不报错、不出现在返回值里（向前兼容老调用方）。"""
    jd = tmp_path / "job3"
    asr_items = _seed_asr_items(jd, [{"content": "内容。"}])

    async def stub_one_ok(cand, user_prompt, system_prompt, on_progress, on_status=None):
        if cand["id"] == "deepseek":
            return "# 仅 deepseek 成功\n\n正文。"
        from ncds_opus_factory.server.pipeline_rw_helpers import _ModelUnavailable as MU
        raise MU("跳过")

    monkeypatch.setattr(perf, "_invoke_rw", stub_one_ok)
    monkeypatch.setattr(perf, "_apply_rw_qc", lambda *a, **k: {})

    out = perf.run_rw_step(_noop, job_dir=str(jd), asr_items=asr_items, profile="paper_card_talk")
    assert "profile" not in out                  # 体裁已不再是返回契约
    assert out["success_count"] == 1             # opus 被 stub skip，仅 deepseek 成功


def test_run_rw_step_codeblock_stripped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """模型输出带 ```json ... ``` 包裹 → 去壳后写盘（与原版 make_draft 对齐）。"""
    jd = tmp_path / "job4"
    asr_items = _seed_asr_items(jd, [{"content": "内容。"}])

    raw_with_fence = "```json\n# 被包裹的改写稿\n\n正文。\n```"

    async def stub_fence(cand, user_prompt, system_prompt, on_progress, on_status=None):
        if cand["id"] == "deepseek":
            return raw_with_fence
        from ncds_opus_factory.server.pipeline_rw_helpers import _ModelUnavailable as MU
        raise MU("skip")

    monkeypatch.setattr(perf, "_invoke_rw", stub_fence)

    out = perf.run_rw_step(_noop, job_dir=str(jd), asr_items=asr_items)
    assert out["success_count"] == 1
    draft_content = (jd / "02_rw" / "deepseek" / "draft.md").read_text(encoding="utf-8")
    # ``` 包裹应被去掉
    assert "```" not in draft_content
    assert "被包裹的改写稿" in draft_content
