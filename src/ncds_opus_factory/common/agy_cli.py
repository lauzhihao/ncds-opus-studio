"""agy CLI helper：直接调 agy（绕过 g.sh 以避免知识文件偏置）。

背景：g.sh 会在 prompt 头部注入 ~/.gemini/.gemini_knowledge.md 记忆库，且
~/.gemini/GEMINI.md 将角色固化为「抖音口播稿写作助手」，导致鬼谷子选题时
AGY 产出偏置（偏心理赛道、角色错位）。本模块直接调 agy --print，彻底规避
g.sh 的 prompt 构造逻辑。

保持与 opus/deepseek 相同的 CallerFn 接口：吃 prompt 文本，返回原始输出文本，
失败抛 RuntimeError。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess


def _resolve_agy() -> str:
    """解析 agy 可执行文件的绝对路径。"""
    found = shutil.which("agy")
    if found:
        return found
    raise RuntimeError("agy not found on PATH")


_END_PATTERN = re.compile(r"^(?:——END——|————END————)$")


def _strip_memory_update_block(text: str) -> str:
    """移除 GEMINI.md 协议的记忆更新块（————END———— 之后内容），与 g.sh --safe 一致。"""
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        if _END_PATTERN.match(line.strip()):
            break
        if line.strip() == "YOLO mode is enabled.":
            continue
        out.append(line)
    return "\n".join(out).rstrip()


def call_agy(
    prompt: str,
    *,
    timeout_seconds: int = 900,
) -> str:
    """直接调 agy --print 跑一次，返回模型输出文本。

    - 绕过 g.sh，避免 .gemini_knowledge.md / GEMINI.md 偏置注入。
    - 设 cwd=$HOME，防止 agy 扫描项目目录下的 CLAUDE.md 等上下文文件。
    - 输出过滤：丢弃 ————END———— 记忆更新块（与 g.sh --safe 一致）。
    stdin=/dev/null，防止 agy 在非 TTY 环境下死锁等待 EOF。
    """
    agy = _resolve_agy()
    proc = subprocess.run(  # noqa: S603 — agy path 由 _resolve_agy() 解析，非用户输入
        [agy, "--print-timeout", f"{timeout_seconds}s", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=timeout_seconds + 30,
        stdin=subprocess.DEVNULL,
        cwd=os.path.expanduser("~"),
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"agy exited {proc.returncode}: {tail}")

    out = _strip_memory_update_block(proc.stdout.strip())
    if not out:
        raise RuntimeError(f"agy returned empty result; stderr={proc.stderr[-300:]!r}")
    return out
