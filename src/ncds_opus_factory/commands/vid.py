"""/vid —— DashScope HappyHorse 视频生成。

输入：prompt + 可选 ref_image_urls（公网 URL，1 张走 i2v，多张走 r2v）+ duration。
输出：本地视频文件路径（mp4）。

飞书发送由调用方走 lark-cli，本模块不接入飞书。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

DEFAULT_STATE_DIR = Path(os.getenv("NOF_STATE_DIR", str(Path.home() / ".ncds-opus-factory" / "state")))
VIDEO_DIR = DEFAULT_STATE_DIR / "videos"

DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com")
SUBMIT_URL = f"{DASHSCOPE_BASE_URL}/api/v1/services/aigc/video-generation/video-synthesis"
TASK_URL_TEMPLATE = f"{DASHSCOPE_BASE_URL}/api/v1/tasks/{{task_id}}"

MODEL_T2V = "happyhorse-1.0-t2v"
MODEL_I2V = "happyhorse-1.0-i2v"
MODEL_R2V = "happyhorse-1.0-r2v"

DEFAULT_RESOLUTION = os.getenv("HAPPYHORSE_RESOLUTION", "1080P")
DEFAULT_RATIO = os.getenv("HAPPYHORSE_RATIO", "16:9")
DEFAULT_DURATION = int(os.getenv("HAPPYHORSE_DURATION", "5"))
POLL_INTERVAL_SECONDS = int(os.getenv("HAPPYHORSE_POLL_INTERVAL", "15"))
VIDEO_TIMEOUT_SECONDS = int(os.getenv("HAPPYHORSE_TIMEOUT", "600"))

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


def _urlopen_direct(req: urllib.request.Request, timeout: int = 30) -> Any:
    req.add_header("User-Agent", "ncds-opus-factory/0.1")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout)


def _ensure_api_key() -> str:
    key = os.getenv("DASHSCOPE_API_KEY", "")
    if not key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置。")
    return key


def _choose_mode(ref_image_urls: list[str] | None) -> str:
    if not ref_image_urls:
        return "t2v"
    if len(ref_image_urls) == 1:
        return "i2v"
    return "r2v"


def submit_video_task(
    prompt: str,
    ref_image_urls: list[str] | None = None,
    mode: str | None = None,
    resolution: str = DEFAULT_RESOLUTION,
    ratio: str = DEFAULT_RATIO,
    duration: int = DEFAULT_DURATION,
) -> str:
    api_key = _ensure_api_key()
    mode = mode or _choose_mode(ref_image_urls)
    model = {"i2v": MODEL_I2V, "r2v": MODEL_R2V}.get(mode, MODEL_T2V)

    input_data: dict[str, Any] = {"prompt": prompt}
    params: dict[str, Any] = {"resolution": resolution, "duration": duration, "watermark": False}

    if mode == "t2v":
        params["ratio"] = ratio
    elif mode == "i2v" and ref_image_urls:
        input_data["media"] = [{"type": "first_frame", "url": ref_image_urls[0]}]
    elif mode == "r2v" and ref_image_urls:
        input_data["media"] = [{"type": "reference_image", "url": url} for url in ref_image_urls]
        params["ratio"] = ratio

    body = json.dumps({"model": model, "input": input_data, "parameters": params}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        SUBMIT_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-DashScope-Async": "enable",
        },
    )
    try:
        with _urlopen_direct(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"视频任务提交失败 (HTTP {exc.code}): {error_body}") from exc

    task_id = (data.get("output") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"视频任务提交异常，未返回 task_id: {json.dumps(data, ensure_ascii=False)}")
    return str(task_id)


def poll_video_task(
    task_id: str,
    timeout_seconds: int = VIDEO_TIMEOUT_SECONDS,
    poll_interval: int = POLL_INTERVAL_SECONDS,
    on_progress: ProgressFn = _noop,
) -> str:
    api_key = _ensure_api_key()
    url = TASK_URL_TEMPLATE.format(task_id=task_id)
    deadline = time.monotonic() + timeout_seconds
    last_status = ""

    while True:
        req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {api_key}"})
        try:
            with _urlopen_direct(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"查询视频任务失败 (HTTP {exc.code}): {error_body}") from exc

        output = data.get("output") or {}
        status = str(output.get("task_status") or "").upper()

        if status == "SUCCEEDED":
            video_url = output.get("video_url")
            if not video_url:
                raise RuntimeError("视频任务完成但未返回 video_url")
            return str(video_url)
        if status == "FAILED":
            raise RuntimeError(f"视频生成失败: [{output.get('code', '')}] {output.get('message', '')}")
        if status == "UNKNOWN":
            raise RuntimeError(f"视频任务不存在或已过期: {task_id}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"视频生成超时 ({timeout_seconds}s): {task_id}")

        if status != last_status:
            on_progress("排队中，请稍候" if status == "PENDING" else "视频生成中，请稍候")
            last_status = status
        time.sleep(poll_interval)


def download_video(video_url: str, job_id: str) -> Path:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    output_path = VIDEO_DIR / f"{job_id}.mp4"
    req = urllib.request.Request(video_url, method="GET")
    try:
        with _urlopen_direct(req, timeout=120) as resp, output_path.open("wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"视频下载失败: {exc}") from exc

    if output_path.stat().st_size == 0:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("视频下载失败: 文件为空")
    return output_path


def extract_video_cover(video_path: Path) -> Path | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    cover_path = video_path.with_suffix(".jpg")
    try:
        subprocess.run(
            [ffmpeg, "-i", str(video_path), "-vframes", "1", "-q:v", "2", "-y", str(cover_path)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
        )
    except Exception:
        return None
    return cover_path if cover_path.exists() and cover_path.stat().st_size > 0 else None


def run(
    prompt: str,
    ref_image_urls: list[str] | None = None,
    duration: int = DEFAULT_DURATION,
    timeout_seconds: int = VIDEO_TIMEOUT_SECONDS,
    job_id: str | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """生成视频，返回 {video_path, cover_path, mode, dashscope_task_id}。"""
    if not prompt.strip():
        raise ValueError("prompt 不能为空")

    mode = _choose_mode(ref_image_urls)
    mode_label = {"t2v": "文生视频", "i2v": "图生视频", "r2v": "参考图生视频"}[mode]
    effective_job_id = job_id or f"VID{int(time.time() * 1000)}"

    on_progress(f"提交{mode_label}任务")
    task_id = submit_video_task(prompt=prompt, ref_image_urls=ref_image_urls, mode=mode, duration=duration)
    on_progress(f"{mode_label}任务已提交，等待生成")
    video_url = poll_video_task(task_id=task_id, timeout_seconds=timeout_seconds, on_progress=on_progress)
    on_progress("下载视频中")
    video_path = download_video(video_url, effective_job_id)
    cover_path = extract_video_cover(video_path)
    return {
        "video_path": str(video_path),
        "cover_path": str(cover_path) if cover_path else None,
        "mode": mode,
        "mode_label": mode_label,
        "dashscope_task_id": task_id,
        "job_id": effective_job_id,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof vid", description="生成视频（DashScope HappyHorse）")
    parser.add_argument("--prompt", required=True, help="生成提示词")
    parser.add_argument("--ref", action="append", default=[], help="参考图公网 URL（可多次传，1 张走 i2v，多张走 r2v）")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION, help=f"视频秒数（默认 {DEFAULT_DURATION}）")
    parser.add_argument("--timeout", type=int, default=VIDEO_TIMEOUT_SECONDS)
    parser.add_argument("--job-id", default=None)
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        prompt=args.prompt,
        ref_image_urls=args.ref or None,
        duration=args.duration,
        timeout_seconds=args.timeout,
        job_id=args.job_id,
        on_progress=on_progress,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
