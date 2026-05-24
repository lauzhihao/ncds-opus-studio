"""/asr —— 多链路并行转写 + 爆款精华分析。

Python 薄包装：spawn `scripts/asr_command_runner.mjs`，由 Node runner 调度
yt-dlp / Whisper / Tingwu / DashScope，最终把精华文档写到飞书。

注意：当前 scripts/ 下的 .mjs 还在用 feishu_sdk_adapter.mjs 直调飞书 OpenAPI。
按本项目约束，这一层最终要重写为 lark-cli 子进程调用。详见
docs/FEISHU-REFACTOR.md。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_DIR = ROOT
ASR_RUNNER = ROOT / "scripts" / "asr_command_runner.mjs"

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("NOF_ASR_TIMEOUT", "3600"))

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


def _build_job_id() -> str:
    return f"vj_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


def build_payload(
    text: str,
    job_id: str | None = None,
    chat_id: str | None = None,
    sender_open_id: str | None = None,
    channel: str = "feishu",
    provider: str = "feishu",
    message_id: str | None = None,
    chat_type: str = "direct",
) -> dict[str, Any]:
    return {
        "jobId": job_id or _build_job_id(),
        "text": text,
        "chatId": chat_id,
        "senderOpenId": sender_open_id,
        "channel": channel,
        "provider": provider,
        "messageId": message_id,
        "chatType": chat_type,
    }


def run(
    text: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """同步执行 ASR runner，返回 {job_id, trace_log, exit_code}。"""
    if not ASR_RUNNER.exists():
        raise RuntimeError(f"ASR runner 未就绪: {ASR_RUNNER}")
    asr_payload = dict(payload) if payload else build_payload(text=text)
    asr_payload.setdefault("text", text)
    if not asr_payload.get("jobId"):
        asr_payload["jobId"] = _build_job_id()
    job_id = asr_payload["jobId"]
    trace_log = WORKSPACE_DIR / "video-jobs" / job_id / "trace.log"

    env = os.environ.copy()
    env.setdefault("LARK_CLI_NO_PROXY", "1")
    env.setdefault("NO_PROXY", "localhost,127.0.0.1,.local,.feishu.cn,.larksuite.com,.larksuite.cn")
    env.setdefault("no_proxy", env["NO_PROXY"])
    env.setdefault("OPENCLAW_WORKSPACE_DIR", str(WORKSPACE_DIR))

    on_progress(f"启动 ASR runner（job={job_id}）")
    command = ["node", str(ASR_RUNNER), "--sync", json.dumps(asr_payload, ensure_ascii=False)]
    proc = subprocess.run(
        command,
        cwd=str(WORKSPACE_DIR),
        env=env,
        timeout=timeout_seconds,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ASR runner exited with code {proc.returncode}. trace_log={trace_log}")
    on_progress("ASR 任务完成")
    # 冒泡产物约定路径：daoer 等下游通过 ncds /jobs/{job_id}/files/{relpath}
    # HTTP 端点按 relpath 拉本地产物，无需共享 fs / 无需猜路径。
    return {
        "job_id": job_id,
        "trace_log": str(trace_log),
        "exit_code": proc.returncode,
        "deliverables_dir": f"video-jobs/{job_id}/deliverables",
        "results_json_relpath": "deliverables/results.json",
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof asr", description="多链路并行转写 + 爆款精华分析")
    parser.add_argument("--text", required=True, help="包含媒体 URL 或抖音分享文案的整段文本")
    parser.add_argument("--chat-id", default=None)
    parser.add_argument("--sender-open-id", default=None)
    parser.add_argument("--message-id", default=None)
    parser.add_argument("--chat-type", default="direct")
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    payload = build_payload(
        text=args.text,
        job_id=args.job_id,
        chat_id=args.chat_id,
        sender_open_id=args.sender_open_id,
        message_id=args.message_id,
        chat_type=args.chat_type,
    )

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(text=args.text, payload=payload, timeout_seconds=args.timeout, on_progress=on_progress)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
