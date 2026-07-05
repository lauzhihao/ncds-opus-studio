from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "digital_human_lipsync.py"


def run_tool(manifest: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--dry-run"],
        input=json.dumps(manifest),
        text=True,
        capture_output=True,
        check=False,
    )


def test_dry_run_accepts_local_assets_and_outputs_json(tmp_path: Path) -> None:
    video = tmp_path / "presenter source.mp4"
    audio = tmp_path / "speech.wav"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")

    proc = run_tool(
        {
            "job_id": "Demo Job",
            "source_video": {"path": str(video)},
            "speech_audio": {"path": str(audio)},
            "outputs": {"local_dir": str(tmp_path / "out"), "name": "result"},
        }
    )

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["ok"] is True
    assert result["job_id"] == "Demo-Job"
    assert result["outputs"]["remote_video"].endswith("/v15/result.mp4")
    assert result["outputs"]["local_video"].endswith("/result.mp4")
    assert result["inputs"]["source_video"].endswith("/inputs/presenter-source.mp4")
    assert result["inputs"]["speech_audio"].endswith("/inputs/speech.wav")
    assert "result.mp4" in result["inference_config"]

    commands = [" ".join(command) for command in result["commands"]]
    assert any(command.startswith("scp ") and "presenter source.mp4" in command for command in commands)
    assert any("scripts.inference" in command and "--use_float16" in command for command in commands)


def test_dry_run_uses_remote_assets_without_uploads(tmp_path: Path) -> None:
    proc = run_tool(
        {
            "job_id": "remote-demo",
            "source_video": {"path": "/root/lipsync-poc/inputs/base.mp4", "location": "remote"},
            "speech_audio": {"path": "/root/lipsync-poc/inputs/speech.wav", "location": "remote"},
            "outputs": {"local_dir": str(tmp_path / "out"), "name": "remote-demo.mp4"},
            "musetalk": {"use_saved_coord": True, "batch_size": 8},
        }
    )

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    commands = [" ".join(command) for command in result["commands"]]
    assert not any(command.startswith("scp ") and "/inputs/base.mp4" in command for command in commands)
    assert any("--saved_coord" in command and "--batch_size 8" in command for command in commands)
    assert result["inputs"]["source_video"] == "/root/lipsync-poc/inputs/base.mp4"


def test_manifest_requires_source_video() -> None:
    proc = run_tool({"job_id": "bad", "speech_audio": {"path": "/root/lipsync-poc/inputs/a.wav", "location": "remote"}})

    assert proc.returncode == 1
    result = json.loads(proc.stdout)
    assert result["ok"] is False
    assert "source_video" in result["error"]
