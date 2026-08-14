"""说话人分离真模型端到端验收。

重测试:首次运行会从 ModelScope 下载约 1GB 模型,常规 pytest 默认跳过;
显式 NOF_DIARIZE_E2E=1 才执行。用 macOS `say` 两个音色差异大的中文语音
合成一段四轮双人对白,走真 FunASR 链路,验证声纹聚类把两个声音分开、
且每个合成轮次内的句子标签一致(按句子中点时间对回合成时的轮次边界)。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ncds_opus_factory.common.capabilities import diarize

pytestmark = [
    pytest.mark.skipif(
        not os.getenv("NOF_DIARIZE_E2E"),
        reason="heavy: downloads ~1GB model; set NOF_DIARIZE_E2E=1 to run",
    ),
    pytest.mark.skipif(sys.platform != "darwin", reason="uses macOS `say` for TTS"),
]

_VOICE_A = "Grandpa (中文（中国大陆）)"
_VOICE_B = "Meijia"
_TURNS = [
    ("A", "你今天怎么又迟到了,我们等了你半个多小时,大家都快饿死了。"),
    ("B", "别提了,路上堵车堵得离谱,我真的不是故意的,你们先点菜嘛。"),
    ("A", "每次都是这个理由,你就不能早一点出门吗。"),
    ("B", "好好好,下次我一定提前一个小时出发,这样总可以了吧。"),
]


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"


def _duration_ms(path: Path) -> int:
    ffprobe = shutil.which("ffprobe") or "/usr/local/bin/ffprobe"
    out = subprocess.run(  # noqa: S603
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    return int(float(out) * 1000)


def _synth_dialogue(tmp_path: Path) -> tuple[Path, list[tuple[int, int, str]]]:
    """合成对白;返回 (音频, [(start_ms, end_ms, 期望说话人A/B) 逐轮边界])。"""
    parts: list[Path] = []
    for i, (spk, text) in enumerate(_TURNS):
        voice = _VOICE_A if spk == "A" else _VOICE_B
        aiff = tmp_path / f"turn_{i}.aiff"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True, timeout=120)  # noqa: S603, S607
        parts.append(aiff)

    boundaries: list[tuple[int, int, str]] = []
    cursor = 0
    for part, (spk, _text) in zip(parts, _TURNS):
        dur = _duration_ms(part)
        boundaries.append((cursor, cursor + dur, spk))
        cursor += dur

    out = tmp_path / "dialogue.wav"
    cmd = [_ffmpeg(), "-y", "-loglevel", "error"]
    for part in parts:
        cmd += ["-i", str(part)]
    cmd += ["-filter_complex", f"concat=n={len(parts)}:v=0:a=1",
            "-ac", "1", "-ar", "16000", str(out)]
    subprocess.run(cmd, check=True, timeout=120)  # noqa: S603
    return out, boundaries


def _expected_voice(mid_ms: int, boundaries: list[tuple[int, int, str]]) -> str:
    for start, end, spk in boundaries:
        if start <= mid_ms < end:
            return spk
    return boundaries[-1][2]


def test_two_voices_get_distinct_stable_labels(tmp_path: Path) -> None:
    audio, boundaries = _synth_dialogue(tmp_path)
    # 生产路径不传人数提示,验证自动估计
    result = diarize.diarize(audio)

    assert result.sentences, "真模型应识别出句子"
    assert result.speaker_count == 2, f"应自动聚成 2 人,实际 {result.speaker_count}"
    assert "堵车" in result.text

    # 每个合成轮次的句子应有一致标签,且两个声音标签不同
    labels_by_voice: dict[str, set[int]] = {"A": set(), "B": set()}
    for s in result.sentences:
        mid = (s.start_ms + s.end_ms) // 2
        labels_by_voice[_expected_voice(mid, boundaries)].add(s.speaker)
    assert len(labels_by_voice["A"]) == 1, f"声音A标签漂移: {labels_by_voice['A']}"
    assert len(labels_by_voice["B"]) == 1, f"声音B标签漂移: {labels_by_voice['B']}"
    assert labels_by_voice["A"] != labels_by_voice["B"]

    # 对话体渲染:合并后应正好 4 个轮次,A/B 交替
    dialogue = diarize.format_dialogue(result.sentences)
    lines = dialogue.splitlines()
    assert len(lines) == 4, f"应合并为 4 轮发言,实际 {len(lines)}:\n{dialogue}"
