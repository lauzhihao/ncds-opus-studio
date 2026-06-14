"""共享 opus CLI helper：本机 opus 启动器 -> claude CLI，取最后一条 type=result 的文本。

codex 订阅失效后，原先走 scodex shim（gpt-5.5）的 agent —— 吴道子分镜 / 鬼谷子选题 /
预筛判官 / 柳永 AI 味消除 —— 统一改调 opus。集中一处实现 launch+parse，避免在各
command 里重复 ~30 行同构代码。默认 ``claude-opus-4-8`` + ``--effort max``
（claude CLI 的 reasoning effort 旋钮）。
"""

from __future__ import annotations

import json
import subprocess
from typing import Optional

DEFAULT_OPUS_MODEL = "claude-opus-4-8"
DEFAULT_EFFORT = "max"


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
        "opus", "launch", "--no-resume", "--",
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
