from __future__ import annotations

from pathlib import Path

from ncds_opus_factory.common.capabilities import audio


def test_separate_audio_skips_demucs_for_long_audio(tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    original = audio_dir / "original.mp3"
    original.write_bytes(b"mp3")
    video = tmp_path / "video.mp4"
    messages: list[str] = []

    monkeypatch.setattr(audio, "_probe_duration_s", lambda *_: audio.DEMUCS_MAX_DURATION_S + 1)

    def fail_demucs(*_args, **_kwargs):
        raise AssertionError("long audio should not run Demucs")

    monkeypatch.setattr(audio, "_run_proc_cancellable", fail_demucs)

    out = audio.separate_audio(video, audio_dir, messages.append)

    assert out == {"original": original}
    assert not (audio_dir / "vocals.mp3").exists()
    assert not (audio_dir / "bgm.mp3").exists()
    assert any("Demucs 跳过" in msg for msg in messages)


def test_separate_audio_runs_demucs_with_low_memory_args(tmp_path, monkeypatch):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    original = audio_dir / "original.mp3"
    original.write_bytes(b"mp3")
    video = tmp_path / "video.mp4"
    seen: dict[str, list[str]] = {}

    monkeypatch.setattr(audio, "_probe_duration_s", lambda *_: 120.0)

    def fake_demucs(args: list[str], _check):
        seen["args"] = args
        out_index = args.index("-o") + 1
        sep_dir = Path(args[out_index])
        stem = sep_dir / "htdemucs" / original.stem
        stem.mkdir(parents=True)
        (stem / "vocals.mp3").write_bytes(b"vocals")
        (stem / "no_vocals.mp3").write_bytes(b"bgm")

    monkeypatch.setattr(audio, "_run_proc_cancellable", fake_demucs)

    out = audio.separate_audio(video, audio_dir)

    assert out["original"] == original
    assert out["vocals"] == audio_dir / "vocals.mp3"
    assert out["bgm"] == audio_dir / "bgm.mp3"
    assert (audio_dir / "vocals.mp3").read_bytes() == b"vocals"
    assert (audio_dir / "bgm.mp3").read_bytes() == b"bgm"
    assert "--segment" in seen["args"]
    assert seen["args"][seen["args"].index("--segment") + 1] == str(audio.DEMUCS_SEGMENT_S)
    assert "-j" in seen["args"]
    assert seen["args"][seen["args"].index("-j") + 1] == "1"
