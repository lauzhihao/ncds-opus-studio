"""/asr —— 本地媒体下载 + ASR 转写 pipeline。

本命令只负责把媒体链接交给本地 Python pipeline，产物落在
``video-jobs/{job_id}/``。不包含任何聊天平台、文档平台或外部消息交付逻辑。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_DIR = ROOT
VIDEO_PIPELINE = ROOT / "skills" / "video-pipeline" / "scripts" / "video_pipeline.py"
SUPPORTED_MEDIA_URL = re.compile(r"https?://[^\s\]）)>]+", re.IGNORECASE)

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("NOF_ASR_TIMEOUT", "3600"))

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


def _build_job_id() -> str:
    return f"asr_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for item in SUPPORTED_MEDIA_URL.findall(text or ""):
        cleaned = item.rstrip(".,;!?)]}")
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
    return urls


def build_payload(text: str, job_id: str | None = None, urls: list[str] | None = None) -> dict[str, Any]:
    inputs = list(urls) if urls is not None else extract_urls(text)
    return {
        "jobId": job_id or _build_job_id(),
        "text": text,
        "inputs": inputs,
    }


def run(
    text: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """同步执行本地 video pipeline，返回 job 目录和本地产物路径。"""
    if not VIDEO_PIPELINE.exists():
        raise RuntimeError(f"ASR pipeline 未就绪: {VIDEO_PIPELINE}")

    asr_payload = dict(payload) if payload else build_payload(text=text)
    asr_payload.setdefault("text", text)
    if not asr_payload.get("jobId"):
        asr_payload["jobId"] = _build_job_id()
    inputs = asr_payload.get("inputs") or asr_payload.get("urls") or extract_urls(asr_payload.get("text", ""))
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("ASR 输入中没有可处理的媒体 URL")

    job_id = str(asr_payload["jobId"])
    job_dir = WORKSPACE_DIR / "video-jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.setdefault("OPENCLAW_WORKSPACE_DIR", str(WORKSPACE_DIR))

    on_progress(f"启动本地 ASR pipeline（job={job_id}）")
    command = [sys.executable, str(VIDEO_PIPELINE), "-o", str(job_dir), *[str(item) for item in inputs]]
    proc = subprocess.run(
        command,
        cwd=str(WORKSPACE_DIR),
        env=env,
        timeout=timeout_seconds,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ASR pipeline exited with code {proc.returncode}: {detail}")

    result_json = job_dir / "deliverables" / "result.json"
    on_progress("ASR pipeline 完成")
    return {
        "job_id": job_id,
        "exit_code": proc.returncode,
        "job_dir": str(job_dir),
        "deliverables_dir": str(job_dir / "deliverables"),
        "result_json": str(result_json) if result_json.exists() else None,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof asr", description="本地媒体下载 + ASR 转写")
    parser.add_argument("--text", required=True, help="包含媒体 URL 的文本")
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    payload = build_payload(text=args.text, job_id=args.job_id)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(text=args.text, payload=payload, timeout_seconds=args.timeout, on_progress=on_progress)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
