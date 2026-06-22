"""共享 opus CLI helper：本机 opus 启动器 -> claude CLI，取最后一条 type=result 的文本。

codex 订阅失效后，原先走 scodex shim（gpt-5.5）的 agent —— 吴道子分镜 / 鬼谷子选题 /
预筛判官 / 柳永 AI 味消除 —— 统一改调 opus。集中一处实现 launch+parse，避免在各
command 里重复 ~30 行同构代码。默认 ``claude-opus-4-8`` + ``--effort max``
（claude CLI 的 reasoning effort 旋钮）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

DEFAULT_OPUS_MODEL = "claude-opus-4-8"
DEFAULT_EFFORT = "max"

# launchd 托管的 nof-worker 不继承交互 shell 的 PATH(opus 实际装在 ~/.sclaude/bin/),
# 裸调 "opus" 会 FileNotFoundError 把 agent 任务打挂。把可执行文件解析成绝对路径:
# PATH 优先(手动起的 nof-server 能命中),回退用户家目录的 sclaude 安装位,都没有才抛清晰错误。
_SCLAUDE_FALLBACK = Path.home() / ".sclaude" / "bin" / "opus"


def _resolve_opus() -> str:
    """解析 opus 可执行文件的绝对路径(PATH 优先,回退 ~/.sclaude/bin/opus)。"""
    found = shutil.which("opus")
    if found:
        return found
    if _SCLAUDE_FALLBACK.exists():
        return str(_SCLAUDE_FALLBACK)
    raise RuntimeError(
        f"opus launcher not found: not on PATH and fallback {_SCLAUDE_FALLBACK} missing"
    )


def is_opus_available() -> bool:
    """本机 opus 启动器是否可用（PATH 或 ~/.sclaude/bin/opus 任一命中）。"""
    try:
        _resolve_opus()
    except RuntimeError:
        return False
    return True


def call_opus(
    prompt: str,
    *,
    system_prompt: str = "",
    model: str = DEFAULT_OPUS_MODEL,
    effort: str = DEFAULT_EFFORT,
    timeout_seconds: int = 900,
    env: Optional[dict] = None,
) -> str:
    """调本机 opus 启动器跑一次 headless claude，返回最后一条 ``type=result`` 的 result 文本。

    沿用 ``_call_opus_for_rw`` 的 ``--no-resume`` / ``--no-session-persistence`` / ``stdin=DEVNULL``
    防会话污染；``--effort`` 透传 claude CLI 的 reasoning effort（low|medium|high|xhigh|max）。
    不可用 / 空输出 / claude 自报错时抛 RuntimeError（与 scodex shim 路径同样是"失败即抛"语义）。
    """
    args = [
        _resolve_opus(), "launch", "--no-resume", "--",
        "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--effort", effort,
        "--permission-mode", "bypassPermissions",
        "--tools", "",
        "--no-session-persistence",
    ]
    if system_prompt:
        args.extend(["--system-prompt", system_prompt])
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        stdin=subprocess.DEVNULL,
        env=env,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"opus launcher exited {proc.returncode}: {tail}")

    final = ""
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "result":
            continue
        if payload.get("is_error"):
            raise RuntimeError(f"claude error: {payload.get('result')}")
        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            final = result.strip()
    if not final:
        raise RuntimeError(f"opus empty result; stdout tail={proc.stdout[-300:]}")
    return final
