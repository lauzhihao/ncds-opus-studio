#!/usr/bin/env python3
"""Run the remote MuseTalk/DWPose digital human lip-sync job.

The tool intentionally keeps a narrow contract:

- input: a JSON manifest from --input or stdin
- output: one JSON result on stdout
- side effects: local mp4/log/metadata artifacts under outputs.local_dir
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_REMOTE_HOST = "root@100.83.163.41"
DEFAULT_REMOTE_WORKDIR = "/root/lipsync-poc"
DEFAULT_MUSETALK_REPO = "/root/lipsync-poc/repos/MuseTalk"
DEFAULT_REMOTE_PYTHON = "/root/lipsync-poc/envs/musetalk310/bin/python"
DEFAULT_UNET_MODEL_PATH = "models/musetalkV15/unet.pth"
DEFAULT_UNET_CONFIG = "models/musetalkV15/musetalk.json"
DEFAULT_VERSION = "v15"
DEFAULT_FPS = 25
DEFAULT_BATCH_SIZE = 4
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class ManifestError(ValueError):
    """Raised when an input manifest cannot be normalized."""


@dataclass(frozen=True)
class CommandResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a MuseTalk/DWPose lip-sync job from a JSON manifest.")
    parser.add_argument("--input", "-i", default="-", help="JSON manifest path, or '-' for stdin.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print planned commands without running them.")
    return parser.parse_args()


def load_manifest(path: str) -> dict[str, Any]:
    if path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid JSON manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a JSON object")
    return data


def safe_name(value: str, fallback: str) -> str:
    cleaned = SAFE_NAME_RE.sub("-", value.strip()).strip(".-")
    return cleaned or fallback


def q(value: str | os.PathLike[str]) -> str:
    return shlex.quote(str(value))


def as_dict(value: Any, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{key} must be an object")
    return value


def normalize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    job_id = safe_name(str(manifest.get("job_id") or "digital-human-lipsync"), "digital-human-lipsync")

    remote = dict(manifest.get("remote") or {})
    remote_host = str(remote.get("host") or DEFAULT_REMOTE_HOST)
    remote_workdir = str(remote.get("workdir") or DEFAULT_REMOTE_WORKDIR).rstrip("/")
    remote_repo = str(remote.get("musetalk_repo") or f"{remote_workdir}/repos/MuseTalk")
    remote_python = str(remote.get("python") or f"{remote_workdir}/envs/musetalk310/bin/python")
    remote_env = dict(remote.get("env") or {})

    source_video = as_dict(manifest.get("source_video"), "source_video")
    speech_audio = as_dict(manifest.get("speech_audio"), "speech_audio")

    outputs = dict(manifest.get("outputs") or {})
    output_name = safe_name(str(outputs.get("name") or f"{job_id}.mp4"), f"{job_id}.mp4")
    if not output_name.endswith(".mp4"):
        output_name = f"{output_name}.mp4"
    local_dir = Path(outputs.get("local_dir") or f"outputs/digital-human-lipsync/{job_id}")
    remote_result_dir = str(outputs.get("remote_result_dir") or f"{remote_workdir}/outputs/{job_id}")
    remote_job_dir = str(outputs.get("remote_job_dir") or f"{remote_workdir}/jobs/{job_id}")

    musetalk = dict(manifest.get("musetalk") or {})
    artifacts = dict(manifest.get("artifacts") or {})

    return {
        "job_id": job_id,
        "remote": {
            "host": remote_host,
            "workdir": remote_workdir,
            "musetalk_repo": remote_repo,
            "python": remote_python,
            "env": remote_env,
        },
        "source_video": normalize_asset(source_video, "source_video"),
        "speech_audio": normalize_asset(speech_audio, "speech_audio"),
        "outputs": {
            "name": output_name,
            "local_dir": str(local_dir),
            "remote_job_dir": remote_job_dir,
            "remote_result_dir": remote_result_dir,
        },
        "musetalk": {
            "version": str(musetalk.get("version") or DEFAULT_VERSION),
            "fps": int(musetalk.get("fps") or DEFAULT_FPS),
            "batch_size": int(musetalk.get("batch_size") or DEFAULT_BATCH_SIZE),
            "use_float16": bool(musetalk.get("use_float16", True)),
            "use_saved_coord": bool(musetalk.get("use_saved_coord", False)),
            "strict_dwpose": bool(musetalk.get("strict_dwpose", True)),
            "unet_model_path": str(musetalk.get("unet_model_path") or DEFAULT_UNET_MODEL_PATH),
            "unet_config": str(musetalk.get("unet_config") or DEFAULT_UNET_CONFIG),
            "extra_args": list(musetalk.get("extra_args") or []),
        },
        "artifacts": {
            "contact_sheet": bool(artifacts.get("contact_sheet", True)),
            "keyframes": bool(artifacts.get("keyframes", True)),
            "compare_with_local": str(artifacts.get("compare_with_local") or ""),
        },
    }


def normalize_asset(asset: dict[str, Any], key: str) -> dict[str, str]:
    path_value = asset.get("path")
    if not path_value:
        raise ManifestError(f"{key}.path is required")
    path = str(path_value)
    location = str(asset.get("location") or "").lower()
    if not location:
        location = "local" if Path(path).exists() else "remote"
    if location not in {"local", "remote"}:
        raise ManifestError(f"{key}.location must be 'local' or 'remote'")
    return {"path": path, "location": location}


def run_command(cmd: list[str], *, check: bool = False) -> CommandResult:
    start = time.monotonic()
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)  # noqa: S603
    result = CommandResult(
        cmd=cmd,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        elapsed_seconds=time.monotonic() - start,
    )
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr)
    return result


def ssh_cmd(host: str, remote_command: str) -> list[str]:
    return ["ssh", host, "bash", "-lc", remote_command]


def scp_from_remote_cmd(host: str, remote_path: str, local_path: Path) -> list[str]:
    return ["scp", f"{host}:{remote_path}", str(local_path)]


def scp_to_remote_cmd(host: str, local_path: Path, remote_path: str) -> list[str]:
    return ["scp", str(local_path), f"{host}:{remote_path}"]


def remote_asset_path(asset: dict[str, str], remote_inputs_dir: str) -> str:
    if asset["location"] == "remote":
        return asset["path"]
    local_name = safe_name(Path(asset["path"]).name, "asset")
    return f"{remote_inputs_dir}/{local_name}"


def build_inference_config(video_path: str, audio_path: str, result_name: str) -> str:
    return "\n".join(
        [
            "task_0:",
            f"  video_path: {json.dumps(video_path)}",
            f"  audio_path: {json.dumps(audio_path)}",
            f"  result_name: {json.dumps(result_name)}",
            "",
        ]
    )


def build_remote_inference_command(config: dict[str, Any], remote_config_path: str) -> str:
    remote = config["remote"]
    musetalk = config["musetalk"]
    outputs = config["outputs"]
    env = {"PYTHONPATH": ".:./musetalk/utils", **remote["env"]}
    env_prefix = " ".join(f"{key}={q(str(value))}" for key, value in env.items())
    parts = [
        f"cd {q(remote['musetalk_repo'])}",
        "&&",
        env_prefix,
        "/usr/bin/time -p",
        q(remote["python"]),
        "-m scripts.inference",
        "--inference_config",
        q(remote_config_path),
        "--result_dir",
        q(outputs["remote_result_dir"]),
        "--unet_model_path",
        q(musetalk["unet_model_path"]),
        "--unet_config",
        q(musetalk["unet_config"]),
        "--version",
        q(musetalk["version"]),
        "--batch_size",
        str(musetalk["batch_size"]),
        "--fps",
        str(musetalk["fps"]),
    ]
    if musetalk["use_float16"]:
        parts.append("--use_float16")
    if musetalk["use_saved_coord"]:
        parts.append("--saved_coord")
    parts.extend(str(arg) for arg in musetalk["extra_args"])
    return " ".join(parts)


def result_remote_video(config: dict[str, Any]) -> str:
    outputs = config["outputs"]
    version = config["musetalk"]["version"]
    return f"{outputs['remote_result_dir'].rstrip('/')}/{version}/{outputs['name']}"


def ffprobe_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=width,height,r_frame_rate,avg_frame_rate,nb_frames",
        "-of",
        "json",
        str(path),
    ]
    result = run_command(cmd)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def make_contact_sheet(video_path: Path, image_path: Path) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        "fps=1,scale=180:-1,tile=8x4:margin=10:padding=4:color=white",
        "-frames:v",
        "1",
        str(image_path),
    ]
    return run_command(cmd).returncode == 0


def make_keyframes(video_path: Path, image_path: Path) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        "select='eq(n,0)+eq(n,175)+eq(n,350)+eq(n,525)+eq(n,700)',scale=360:-1,tile=5x1:margin=12:padding=6:color=white",
        "-frames:v",
        "1",
        str(image_path),
    ]
    return run_command(cmd).returncode == 0


def make_comparison(left: Path, right: Path, output: Path) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(left),
        "-i",
        str(right),
        "-filter_complex",
        "[0:v][1:v]hstack=inputs=2[v]",
        "-map",
        "[v]",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-shortest",
        str(output),
    ]
    return run_command(cmd).returncode == 0


def append_log(log_path: Path, result: CommandResult) -> None:
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("$ " + " ".join(shlex.quote(arg) for arg in result.cmd) + "\n")
        fh.write(f"returncode={result.returncode} elapsed={result.elapsed_seconds:.2f}s\n")
        if result.stdout:
            fh.write("--- stdout ---\n")
            fh.write(result.stdout)
            if not result.stdout.endswith("\n"):
                fh.write("\n")
        if result.stderr:
            fh.write("--- stderr ---\n")
            fh.write(result.stderr)
            if not result.stderr.endswith("\n"):
                fh.write("\n")
        fh.write("\n")


def run_job(config: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    job_id = config["job_id"]
    remote = config["remote"]
    outputs = config["outputs"]
    artifacts = config["artifacts"]
    remote_inputs_dir = f"{outputs['remote_job_dir'].rstrip('/')}/inputs"
    remote_config_path = f"{outputs['remote_job_dir'].rstrip('/')}/musetalk_inference.yaml"

    local_dir = Path(outputs["local_dir"])
    local_video = local_dir / outputs["name"]
    local_log = local_dir / f"{job_id}.log"
    local_metadata = local_dir / f"{job_id}.metadata.json"
    local_contact_sheet = local_dir / f"{Path(outputs['name']).stem}_contact_sheet_1fps.jpg"
    local_keyframes = local_dir / f"{Path(outputs['name']).stem}_keyframes.jpg"
    local_comparison = local_dir / f"{Path(outputs['name']).stem}_comparison.mp4"

    remote_video = remote_asset_path(config["source_video"], remote_inputs_dir)
    remote_audio = remote_asset_path(config["speech_audio"], remote_inputs_dir)
    remote_output_video = result_remote_video(config)
    inference_config = build_inference_config(remote_video, remote_audio, outputs["name"])
    remote_inference_command = build_remote_inference_command(config, remote_config_path)

    planned_commands: list[list[str]] = [
        ssh_cmd(remote["host"], f"mkdir -p {q(remote_inputs_dir)} {q(outputs['remote_result_dir'])}"),
    ]
    if config["source_video"]["location"] == "local":
        planned_commands.append(scp_to_remote_cmd(remote["host"], Path(config["source_video"]["path"]), remote_video))
    if config["speech_audio"]["location"] == "local":
        planned_commands.append(scp_to_remote_cmd(remote["host"], Path(config["speech_audio"]["path"]), remote_audio))
    planned_commands.extend(
        [
            ssh_cmd(remote["host"], f"cat > {q(remote_config_path)} <<'EOF'\n{inference_config}EOF"),
            ssh_cmd(remote["host"], remote_inference_command),
            scp_from_remote_cmd(remote["host"], remote_output_video, local_video),
        ]
    )

    base_result = {
        "ok": False,
        "job_id": job_id,
        "dry_run": dry_run,
        "inputs": {"source_video": remote_video, "speech_audio": remote_audio},
        "outputs": {
            "remote_video": remote_output_video,
            "local_video": str(local_video),
            "metadata": str(local_metadata),
            "log": str(local_log),
        },
        "commands": planned_commands,
        "warnings": [],
    }
    if dry_run:
        return {**base_result, "ok": True, "inference_config": inference_config}

    local_dir.mkdir(parents=True, exist_ok=True)
    local_log.write_text("", encoding="utf-8")
    start = time.monotonic()

    for cmd in planned_commands[:-2]:
        result = run_command(cmd)
        append_log(local_log, result)
        if result.returncode != 0:
            return write_result(
                local_metadata,
                {**base_result, "error": "setup command failed", "failed_command": cmd, "returncode": result.returncode},
            )

    inference_result = run_command(planned_commands[-2])
    append_log(local_log, inference_result)
    if inference_result.returncode != 0:
        return write_result(
            local_metadata,
            {
                **base_result,
                "error": "remote inference failed",
                "failed_command": planned_commands[-2],
                "returncode": inference_result.returncode,
            },
        )

    log_text = local_log.read_text(encoding="utf-8")
    if "using face detector bbox fallback" in log_text or "dwpose init failed" in log_text:
        base_result["warnings"].append("DWPose did not run; output used face detector bbox fallback.")
        if config["musetalk"]["strict_dwpose"]:
            return write_result(local_metadata, {**base_result, "error": "strict_dwpose rejected fallback output"})
    if "Manually adjust range" not in log_text:
        base_result["warnings"].append("DWPose landmark confirmation was not found in the log.")

    copy_result = run_command(planned_commands[-1])
    append_log(local_log, copy_result)
    if copy_result.returncode != 0:
        return write_result(
            local_metadata,
            {**base_result, "error": "copy output failed", "failed_command": planned_commands[-1]},
        )

    if artifacts["contact_sheet"]:
        if make_contact_sheet(local_video, local_contact_sheet):
            base_result["outputs"]["contact_sheet"] = str(local_contact_sheet)
    if artifacts["keyframes"]:
        if make_keyframes(local_video, local_keyframes):
            base_result["outputs"]["keyframes"] = str(local_keyframes)
    compare_with = artifacts["compare_with_local"]
    if compare_with and Path(compare_with).exists():
        if make_comparison(Path(compare_with), local_video, local_comparison):
            base_result["outputs"]["comparison"] = str(local_comparison)

    return write_result(
        local_metadata,
        {
            **base_result,
            "ok": True,
            "duration_seconds": round(time.monotonic() - start, 2),
            "probe": ffprobe_json(local_video),
        },
    )


def write_result(path: Path, result: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    args = parse_args()
    try:
        manifest = load_manifest(args.input)
        config = normalize_manifest(manifest)
        result = run_job(config, dry_run=args.dry_run)
    except (ManifestError, OSError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
