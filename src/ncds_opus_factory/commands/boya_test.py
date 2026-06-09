"""伯牙 boya 的测试:纯逻辑(时间线/选择/质检)免依赖,整条混音管线在 ffmpeg 可用时跑。"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ncds_opus_factory.commands import boya
from ncds_opus_factory.common import tts_provider as tp
from ncds_opus_factory.common.tts_provider import SynthSpec, TtsProvider

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@tp.register("boya_fake")
class _FakeVoiceProvider(TtsProvider):
    """测试用配音引擎:不联网,用 ffmpeg 写一段真 mp3(时长按字数 ~5 字/秒),供后续 ffprobe/混音。"""
    default_spec = SynthSpec(voice="fake")

    def synth(self, text, out_path: Path, spec, *, attempts=4, timeout=60, on_progress=tp._noop):
        sec = max(0.6, len(text) / 5)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"sine=frequency=300:duration={sec}",
             "-acodec", "libmp3lame", str(out_path)], check=True)


# --------------------------- 纯函数:时间线 --------------------------- #
def test_beat_timeline_mirrors_render_constants():
    starts = boya.beat_timeline([2.0, 3.0, 1.0])
    # INTRO=0.3; 第二句 = 0.3 + 2.0 + GAP0.08; 第三句再 +3.0+0.08
    assert starts == [0.3, 2.38, 5.46]


def test_total_duration():
    # INTRO + (2+3+1) + GAP*2 + ENDING = 0.3 + 6 + 0.16 + 1.5
    assert boya.total_duration([2.0, 3.0, 1.0]) == pytest.approx(7.96)


def test_total_duration_single_clip_no_gap():
    assert boya.total_duration([4.0]) == pytest.approx(0.3 + 4.0 + 1.5)


# --------------------------- 纯函数:kind 推断 --------------------------- #
def test_infer_kind_explicit_wins():
    assert boya.infer_kind(1, 5, {"kind": "golden"}) == "golden"


def test_infer_kind_position_fallback():
    assert boya.infer_kind(0, 3, {}) == "hook"
    assert boya.infer_kind(2, 3, {}) == "close"


def test_infer_kind_reveal_by_keyword():
    assert boya.infer_kind(1, 3, {"zh": "其实你根本不是不行"}) == "reveal"
    assert boya.infer_kind(1, 3, {"zh": "今天讲个方法"}) == "body"


# --------------------------- 纯函数:BGM 选择 --------------------------- #
def test_select_bgm_prefers_scene_and_mood():
    lib = [
        {"file": "bgm/a.mp3", "scene": ["职场"], "mood": ["沉静"], "loopable": True},
        {"file": "bgm/b.mp3", "scene": ["认知"], "mood": ["沉静"], "loopable": True},
    ]
    chosen = boya.select_bgm(lib, scene="认知", mood="沉静")
    assert chosen["file"] == "bgm/b.mp3"
    assert "_reason" in chosen


def test_select_bgm_empty_lib_returns_none():
    assert boya.select_bgm([], "认知", "沉静") is None


# --------------------------- 纯函数:SFX 排布 --------------------------- #
def test_plan_sfx_maps_kind_to_cue():
    sfx_lib = [
        {"file": "sfx/hook.mp3", "cue": "hook", "gain_db": -4},
        {"file": "sfx/close.mp3", "cue": "close", "gain_db": -6},
    ]
    starts = [0.3, 2.0, 4.0]
    beats = [{"zh": "开场"}, {"zh": "中间"}, {"zh": "收尾"}]
    cues = boya.plan_sfx(beats, starts, sfx_lib)
    kinds = {c["kind"]: c for c in cues}
    # 首=hook 尾=close 命中库;中间无对应 cue 被跳过
    assert "hook" in kinds and "close" in kinds
    assert kinds["hook"]["time_s"] == 0.3
    assert kinds["close"]["time_s"] == 4.0


def test_plan_sfx_explicit_beat_sfx_field():
    sfx_lib = [{"file": "sfx/golden.mp3", "cue": "golden", "gain_db": -6}]
    cues = boya.plan_sfx([{"zh": "x", "sfx": "golden"}], [1.0], sfx_lib)
    assert len(cues) == 1 and cues[0]["cue"] == "golden"


# --------------------------- 纯函数:听感质检 --------------------------- #
def test_audition_flags_fast_speech():
    qc = boya.audition([1.0], [{"zh": "这是一句非常非常长会被判语速过快的台词内容啊啊"}])
    assert qc["verdict"] == "warn"
    assert qc["notes"]


def test_audition_ok_when_normal():
    # 15 字 / 3 秒 = 5 字/秒,落在正常口播区间(3.0~8.5)
    qc = boya.audition([3.0], [{"zh": "这是一句语速正常的口播台词示例"}])
    assert qc["verdict"] == "ok"


# --------------------------- 集成:整条混音(需 ffmpeg) --------------------------- #
@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_run_end_to_end(tmp_path: Path):
    job = tmp_path / "job"
    (job / "audio").mkdir(parents=True)
    # 造 2 段人声(正弦占位)
    for i, sec in enumerate([1.5, 2.0], start=1):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"sine=frequency=300:duration={sec}",
             "-acodec", "libmp3lame", str(job / "audio" / f"{i}.mp3")],
            check=True,
        )
    (job / "beats.json").write_text(json.dumps(
        [{"zh": "开场钩子", "tag": "职场 · 认知"}, {"zh": "收尾"}], ensure_ascii=False), encoding="utf-8")

    # 内联建一个最小库:1 条 BGM + hook/close 两个音效 + manifest
    lib = tmp_path / "lib"
    (lib / "bgm").mkdir(parents=True)
    (lib / "sfx").mkdir(parents=True)

    def tone(path: Path, freq: int, sec: float) -> None:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"sine=frequency={freq}:duration={sec}",
             "-acodec", "libmp3lame", str(path)], check=True)

    tone(lib / "bgm" / "calm.mp3", 220, 10)
    tone(lib / "sfx" / "hook.mp3", 880, 0.4)
    tone(lib / "sfx" / "close.mp3", 440, 0.6)
    (lib / "library.json").write_text(json.dumps({
        "bgm": [{"file": "bgm/calm.mp3", "scene": ["认知"], "mood": ["沉静"], "loopable": True}],
        "sfx": [{"file": "sfx/hook.mp3", "cue": "hook", "gain_db": -4},
                {"file": "sfx/close.mp3", "cue": "close", "gain_db": -6}],
    }, ensure_ascii=False), encoding="utf-8")

    plan = boya.run(job_dir=job, library_dir=lib)
    assert (job / "master.mp3").exists()
    assert (job / "audio_plan.json").exists()
    assert plan["bgm"] is not None  # 占位库里有 BGM
    assert plan["voice"]["clips"] == 2
    # master 时长应 ~= 计划时长
    got = boya._ffprobe_duration(job / "master.mp3")
    assert got == pytest.approx(plan["voice"]["duration_s"], abs=0.5)


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_run_synthesizes_missing_voice_via_provider(tmp_path: Path):
    """没有 audio/ 时,伯牙复用 tts.run() 经 provider 抽象合成人声,再混音。"""
    job = tmp_path / "job"
    job.mkdir()
    (job / "beats.json").write_text(json.dumps([
        {"zh": "开场就被同事阴阳的人先听我一句", "tag": "职场 · 认知"},
        {"zh": "其实你最大的弱点是太急着证明自己"},
        {"zh": "下次只问一句你是在建议还是评价"},
    ], ensure_ascii=False), encoding="utf-8")

    lib = tmp_path / "lib"
    (lib / "bgm").mkdir(parents=True)
    (lib / "sfx").mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=220:duration=10", "-acodec", "libmp3lame",
                    str(lib / "bgm" / "calm.mp3")], check=True)
    (lib / "library.json").write_text(json.dumps({
        "bgm": [{"file": "bgm/calm.mp3", "scene": ["认知"], "loopable": True}], "sfx": [],
    }, ensure_ascii=False), encoding="utf-8")

    # audio/ 不存在 -> 伯牙用 boya_fake 引擎合成 3 句,再混音出 master
    plan = boya.run(job_dir=job, library_dir=lib, tts_provider="boya_fake")
    assert plan["voice"]["clips"] == 3
    assert (job / "audio" / "0001.mp3").exists()
    assert (job / "master.mp3").exists()
