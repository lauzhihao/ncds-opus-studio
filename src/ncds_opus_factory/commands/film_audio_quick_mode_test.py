"""Film 快速去解说模式：时间掩码与 ffmpeg 实际音量契约。"""

# ruff: noqa: S101, S603, S607

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from ncds_opus_factory.commands import film_audio_quick_mode

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def test_merge_narration_intervals() -> None:
    intervals = film_audio_quick_mode.narration_intervals([
        {
            "role": "replaceable_narration",
            "start_ms": 2000,
            "end_ms": 3000,
        },
        {
            "role": "preserved_original",
            "start_ms": 3000,
            "end_ms": 3500,
        },
        {
            "role": "replaceable_narration",
            "start_ms": 500,
            "end_ms": 1200,
        },
        {
            "role": "replaceable_narration",
            "start_ms": 1100,
            "end_ms": 1800,
        },
    ])

    assert intervals == [(500, 1800), (2000, 3000)]


def test_clip_intervals_uses_preview_local_time() -> None:
    assert film_audio_quick_mode.clip_intervals(
        [(500, 1800), (2000, 3000), (5000, 6000)],
        start_ms=1000,
        end_ms=5500,
    ) == [(0.0, 0.8), (1.0, 2.0), (4.0, 4.5)]


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_render_quick_bed_mutes_only_narration_interior(
    tmp_path: Path,
) -> None:
    vocals = tmp_path / "vocals.wav"
    bgm = tmp_path / "bgm.wav"
    output = tmp_path / "mute.wav"
    _tone(vocals, "sine=frequency=440:duration=3")
    _tone(bgm, "anullsrc=r=44100:cl=mono:d=3")

    film_audio_quick_mode.render_quick_bed(
        vocals_path=vocals,
        bgm_path=bgm,
        output_path=output,
        narration_ranges_ms=[(1000, 2000)],
        start_ms=0,
        end_ms=3000,
        narration_gain=0.0,
        fade_ms=50,
    )

    before = _window_rms(output, 300, 800)
    muted = _window_rms(output, 1200, 1800)
    after = _window_rms(output, 2200, 2700)
    assert before > 0.05
    assert muted < before * 0.02
    assert after == pytest.approx(before, rel=0.05)


def test_validation_manifest_records_absolute_ranges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guiguzi_path = tmp_path / "guiguzi.json"
    original = tmp_path / "original.mp3"
    vocals = tmp_path / "vocals.mp3"
    bgm = tmp_path / "bgm.mp3"
    for path in (original, vocals, bgm):
        path.write_bytes(b"audio")
    guiguzi_path.write_text(
        json.dumps({
            "status": "done",
            "segments": [
                {
                    "role": "replaceable_narration",
                    "start_ms": 25000,
                    "end_ms": 40000,
                },
                {
                    "role": "preserved_original",
                    "start_ms": 40000,
                    "end_ms": 45000,
                },
            ],
        }),
        encoding="utf-8",
    )

    def fake_reference(**kwargs: object) -> None:
        Path(str(kwargs["output_path"])).write_bytes(b"reference")

    def fake_bed(**kwargs: object) -> None:
        Path(str(kwargs["output_path"])).write_bytes(b"bed")

    monkeypatch.setattr(
        film_audio_quick_mode,
        "_render_reference",
        fake_reference,
    )
    monkeypatch.setattr(
        film_audio_quick_mode,
        "render_quick_bed",
        fake_bed,
    )

    result = film_audio_quick_mode.render_validation_previews(
        guiguzi_path=guiguzi_path,
        original_path=original,
        vocals_path=vocals,
        bgm_path=bgm,
        output_dir=tmp_path / "preview",
        start_ms=20000,
        duration_ms=30000,
        duck_db=-30.0,
        fade_ms=60,
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["preview"] == {
        "start_ms": 20000,
        "end_ms": 50000,
        "duration_ms": 30000,
    }
    assert manifest["narration_ranges_ms"] == [[25000, 40000]]
    assert manifest["settings"]["duck_db"] == -30.0
    assert set(manifest["outputs"]) == {"original", "duck", "mute"}


def _tone(path: Path, source: str) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            source,
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )


def _window_rms(path: Path, start_ms: int, end_ms: int) -> float:
    with wave.open(str(path), "rb") as handle:
        sample_width = handle.getsampwidth()
        channels = handle.getnchannels()
        sample_rate = handle.getframerate()
        assert sample_width == 2
        start_frame = math.floor(start_ms * sample_rate / 1000)
        frame_count = math.ceil((end_ms - start_ms) * sample_rate / 1000)
        handle.setpos(start_frame)
        raw = handle.readframes(frame_count)
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    values = samples[::channels]
    return math.sqrt(
        sum((sample / 32768.0) ** 2 for sample in values) / len(values)
    )
