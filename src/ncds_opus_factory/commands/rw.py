"""/rw —— 给一份 /asr 输出的精华文档，做 gpt-5.5 + gemini 双模型改写。

Python 薄包装：spawn `scripts/rewrite_command_runner.mjs`。

注意：当前 scripts/ 下的 .mjs 还在用 feishu_sdk_adapter.mjs 直调飞书 OpenAPI。
按本项目约束，最终要重写为 lark-cli 子进程调用。详见 docs/FEISHU-REFACTOR.md。
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
RW_RUNNER = ROOT / "scripts" / "rewrite_command_runner.mjs"

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("NOF_RW_TIMEOUT", "3600"))
FEISHU_DOC_URL_PATTERN = re.compile(r"https?://[\w.-]+/docx/[A-Za-z0-9]+", re.IGNORECASE)

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


def _build_job_id() -> str:
    return f"RW_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


def parse_command_body(text: str) -> tuple[str | None, str]:
    """从命令体里抽出 (docx_url, user_requirements)。"""
    body = (text or "").strip()
    if not body:
        return None, ""
    match = FEISHU_DOC_URL_PATTERN.search(body)
    if not match:
        return None, ""
    url = match.group(0)
    requirements = (body[: match.start()] + " " + body[match.end():]).strip()
    requirements = re.sub(r"\s+", " ", requirements)
    return url, requirements


def build_payload(
    docx_url: str,
    job_id: str | None = None,
    chat_id: str | None = None,
    sender_open_id: str | None = None,
    channel: str = "feishu",
    provider: str = "feishu",
    message_id: str | None = None,
    chat_type: str = "direct",
    target_profile: str = "douyin",
    user_requirements: str = "",
) -> dict[str, Any]:
    return {
        "jobId": job_id or _build_job_id(),
        "docxUrl": docx_url,
        "chatId": chat_id,
        "senderOpenId": sender_open_id,
        "channel": channel,
        "provider": provider,
        "messageId": message_id,
        "chatType": chat_type,
        "targetProfile": target_profile,
        "userRequirements": user_requirements,
    }


def run(
    docx_url: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    if not RW_RUNNER.exists():
        raise RuntimeError(f"/rw runner 未就绪: {RW_RUNNER}")
    if not docx_url:
        raise ValueError("docx_url 不能为空")
    rw_payload = dict(payload) if payload else build_payload(docx_url=docx_url)
    rw_payload.setdefault("docxUrl", docx_url)
    if not rw_payload.get("jobId"):
        rw_payload["jobId"] = _build_job_id()
    job_id = rw_payload["jobId"]
    trace_log = WORKSPACE_DIR / "video-jobs" / job_id / "trace.log"

    env = os.environ.copy()
    env.setdefault("LARK_CLI_NO_PROXY", "1")
    env.setdefault("NO_PROXY", "localhost,127.0.0.1,.local,.feishu.cn,.larksuite.com,.larksuite.cn")
    env.setdefault("no_proxy", env["NO_PROXY"])
    env.setdefault("OPENCLAW_WORKSPACE_DIR", str(WORKSPACE_DIR))

    on_progress(f"启动 /rw runner（job={job_id}）")
    command = ["node", str(RW_RUNNER), json.dumps(rw_payload, ensure_ascii=False)]
    proc = subprocess.run(command, cwd=str(WORKSPACE_DIR), env=env, timeout=timeout_seconds, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"/rw runner exited with code {proc.returncode}. trace_log={trace_log}")
    on_progress("/rw 任务完成")
    return {
        "job_id": job_id,
        "trace_log": str(trace_log),
        "exit_code": proc.returncode,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof rw", description="gpt-5.5 + gemini 双模型改写")
    parser.add_argument("--docx-url", required=True)
    parser.add_argument("--requirements", default="", help="附加改写要求")
    parser.add_argument("--target-profile", default="douyin")
    parser.add_argument("--chat-id", default=None)
    parser.add_argument("--sender-open-id", default=None)
    parser.add_argument("--message-id", default=None)
    parser.add_argument("--chat-type", default="direct")
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    payload = build_payload(
        docx_url=args.docx_url,
        job_id=args.job_id,
        chat_id=args.chat_id,
        sender_open_id=args.sender_open_id,
        message_id=args.message_id,
        chat_type=args.chat_type,
        target_profile=args.target_profile,
        user_requirements=args.requirements,
    )

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(docx_url=args.docx_url, payload=payload, timeout_seconds=args.timeout, on_progress=on_progress)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
