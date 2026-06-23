"""Shared media/process helpers for pipeline runner and 015 performers."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ncds_opus_core.common import cancel as _cancel
from ncds_opus_core.gpt_image.paths import GPT_IMAGE_OUTPUT_ROOT

TTS_PROC_TIMEOUT_SEC = int(os.getenv("NOF_TTS_PROC_TIMEOUT", "3600"))
IMAGE_GEN_TIMEOUT_SEC = int(os.getenv("NOF_PIPELINE_IMAGE_GEN_TIMEOUT", "600"))


def _rebuild_tts_items_015(episode: dict[str, Any]) -> list[dict[str, Any]]:
    """从写好时间戳的 episode 组装 beat 级 items（audio 指向所属 scene 整段 mp3）。"""
    items: list[dict[str, Any]] = []
    for i, b in enumerate(episode.get("beats") or [], start=1):
        af = str(b.get("audioFile") or "")
        name = af.split("/")[-1] if af else ""
        items.append({
            "index": i,
            "zh": str(b.get("zh") or ""),
            "scene": str(b.get("scene") or ""),
            "audio_relpath": f"04_tts/{name}" if name else "",
            "audio_start": b.get("audioStart"),
            "audio_end": b.get("audioEnd"),
        })
    return items


def _terminate_proc_group(proc: "subprocess.Popen[str]") -> None:
    """杀整个进程组（直接子进程 + 孙进程 yt-dlp/ffmpeg/whisper/Demucs）。"""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass


def _run_tts_gen_015(
    *,
    script: Path,
    episode_path: Path,
    audio_dir: Path,
    on_line: Callable[[str], None],
    only: str | None = None,
    force: bool = False,
) -> None:
    """同步调 015 tts_gen.py 按 scene 整段合成 + 写回 episode.json 时间戳。"""
    cmd = [
        sys.executable, str(script),
        "--episode", str(episode_path.resolve()),
        "--audio-dir", str(audio_dir.resolve()),
        "--workers", "6",
    ]
    if only:
        cmd += ["--only", only]
    if force:
        cmd += ["--force"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        start_new_session=True,
    )
    assert proc.stdout is not None
    tail: list[str] = []
    checker = _cancel.current()
    for line in iter(proc.stdout.readline, ""):
        if checker():
            _terminate_proc_group(proc)
            raise _cancel.TaskCancelled("cancelled during tts_gen subprocess")
        s = line.rstrip("\n")
        if s:
            on_line(s)
            tail.append(s)
            if len(tail) > 20:
                tail.pop(0)
    proc.stdout.close()
    code = proc.wait(timeout=TTS_PROC_TIMEOUT_SEC)
    if code != 0:
        snippet = "\n".join(tail).strip()
        raise RuntimeError(f"tts_gen.py exited {code}\n--- last output ---\n{snippet}")


def _extract_first_frame(mp4: Path, out: Path) -> None:
    """ffmpeg 抽 mp4 首帧到 out（jpg）。同步、在 to_thread 里调。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(mp4),
        "-frames:v", "1", "-q:v", "3",
        str(tmp),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0 or not tmp.is_file():
        tail = (res.stderr or res.stdout or "").strip()[-300:]
        raise RuntimeError(f"ffmpeg first-frame failed: {tail}")
    tmp.rename(out)


def _generate_scene_image(
    *,
    scene_id: str,
    prompt: str,
    size: str,
    quality: str,
    target: Path,
    job_id: str,
    n: int = 1,
) -> list[Path]:
    """单 scene 出图：subprocess 调 gpt_image_gen.py → Pillow PNG→WebP。

    n=1 时直接落 ``target``；n>1 时落 ``*-v1.webp`` ... ``*-vN.webp``，
    并把 v1 复制为标准主图 ``target``，让下游渲染继续按固定文件名取图。
    """
    from ncds_opus_core.gpt_image import script_path as _gpt_image_script

    gen_script = _gpt_image_script("gpt_image_gen.py")
    if not gen_script.is_file():
        raise RuntimeError(f"gpt_image_gen.py not found at {gen_script}")

    try:
        count = int(n or 1)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(4, count))
    gen_out_dir = GPT_IMAGE_OUTPUT_ROOT / f"job-{job_id}-{scene_id}-{uuid.uuid4().hex[:8]}"
    gen_out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(gen_script),
        "--out-dir", str(gen_out_dir),
        "--size", size,
        "--n", str(count),
        "--overwrite",
        "--prompt", prompt,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    checker = _cancel.current()
    deadline = time.monotonic() + IMAGE_GEN_TIMEOUT_SEC
    while proc.poll() is None:
        if checker():
            _terminate_proc_group(proc)
            raise _cancel.TaskCancelled("cancelled during gpt-image subprocess")
        if time.monotonic() > deadline:
            _terminate_proc_group(proc)
            raise RuntimeError(f"gpt-image gen timed out after {IMAGE_GEN_TIMEOUT_SEC}s")
        time.sleep(0.25)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        tail = (stderr or stdout or "").strip()[-500:]
        raise RuntimeError(f"gpt-image gen failed: {tail}")

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow not installed; pip install Pillow") from exc

    def variant_target(index: int) -> Path:
        if count == 1:
            return target
        return target.with_name(f"{target.stem}-v{index}{target.suffix}")

    saved: list[Path] = []
    for index in range(1, count + 1):
        local_png = gen_out_dir / f"image_{index:02d}.png"
        if not local_png.is_file():
            continue
        out = variant_target(index)
        img = Image.open(local_png).convert("RGB")
        tmp = out.with_suffix(out.suffix + ".part")
        img.save(tmp, format="WEBP", quality=85, method=6)
        tmp.rename(out)
        saved.append(out)

    if not saved:
        expected = gen_out_dir / "image_01.png"
        raise RuntimeError(f"expected {expected} not found after gen")

    if count > 1:
        shutil.copyfile(saved[0], target)

    shutil.rmtree(gen_out_dir, ignore_errors=True)
    return saved


def _read_episode(job_dir: Path) -> dict[str, Any] | None:
    """读 job_dir/02_rw/episode.json，找不到或解析失败返回 None。"""
    ep_path = job_dir / "02_rw" / "episode.json"
    if not ep_path.exists():
        return None
    try:
        return json.loads(ep_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
