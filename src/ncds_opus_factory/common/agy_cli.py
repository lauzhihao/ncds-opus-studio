"""agy CLI helper：直接调 agy（绕过 g.sh 以避免知识文件偏置）。

背景：g.sh 会在 prompt 头部注入 ~/.gemini/.gemini_knowledge.md 记忆库，且
~/.gemini/GEMINI.md 将角色固化为「抖音口播稿写作助手」，导致鬼谷子选题时
AGY 产出偏置（偏心理赛道、角色错位）。本模块直接调 agy --print，彻底规避
g.sh 的 prompt 构造逻辑；同时临时隐藏 GEMINI.md 解除角色硬约束。

保持与 opus/deepseek 相同的 CallerFn 接口：吃 prompt 文本，返回原始输出文本，
失败抛 RuntimeError。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading

_DISABLED_VALUES = {"0", "false", "off", "no", "disabled"}


def is_agy_enabled() -> bool:
    """NOF_AGY=0 时彻底禁用 agy，避免 worker 触发 Google 登录流。"""
    return os.environ.get("NOF_AGY", "1").strip().lower() not in _DISABLED_VALUES


def agy_unavailable_reason() -> str:
    """返回 agy 不可用原因；空串表示可用。"""
    if not is_agy_enabled():
        return "NOF_AGY=0 已禁用 agy"
    if shutil.which("agy") is None:
        return "本机未安装 agy 启动器"
    return ""


def is_agy_available() -> bool:
    """本机是否允许并可执行 agy。"""
    return not agy_unavailable_reason()


def _resolve_agy() -> str:
    """解析 agy 可执行文件的绝对路径。"""
    reason = agy_unavailable_reason()
    if reason:
        raise RuntimeError(reason)
    found = shutil.which("agy")
    if found:
        return found
    raise RuntimeError("agy not found on PATH")


_END_PATTERN = re.compile(r"^(?:——END——|————END————)$")

# 线程锁：保护 GEMINI.md 临时隐藏/恢复操作不被并发抢写
_GEMINI_LOCK = threading.Lock()
_GEMINI_DIR = os.path.expanduser("~/.gemini")
_GEMINI_MD = os.path.join(_GEMINI_DIR, "GEMINI.md")
_GEMINI_DISABLED = os.path.join(_GEMINI_DIR, "GEMINI.md.disabled")


def _disable_gemini_md() -> bool:
    """临时隐藏 GEMINI.md 以解除角色硬约束。返回 True 表示实际移除了文件。"""
    if not os.path.isfile(_GEMINI_MD):
        return False
    os.rename(_GEMINI_MD, _GEMINI_DISABLED)
    return True


def _restore_gemini_md(was_hidden: bool) -> None:
    """恢复 GEMINI.md。"""
    if was_hidden and os.path.isfile(_GEMINI_DISABLED):
        os.rename(_GEMINI_DISABLED, _GEMINI_MD)


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


def _recover_orphaned_disabled() -> None:
    """crash 兜底：若上次进程崩溃导致 GEMINI.md 未恢复，这里自动恢复。"""
    if not os.path.isfile(_GEMINI_MD) and os.path.isfile(_GEMINI_DISABLED):
        os.rename(_GEMINI_DISABLED, _GEMINI_MD)


def call_agy(
    prompt: str,
    *,
    model: str | None = None,
    timeout_seconds: int = 900,
) -> str:
    """直接调 agy --print 跑一次，返回模型输出文本。

    - 绕过 g.sh，避免 .gemini_knowledge.md 偏置注入。
    - 临时隐藏 GEMINI.md（线程安全），解除角色硬约束，用后自动恢复。
    - 设 cwd=$HOME，防止 agy 扫描项目目录下的 CLAUDE.md 等上下文文件。
    - 输出过滤：丢弃 ————END———— 记忆更新块（与 g.sh --safe 一致）。
    - 可指定 model（如 gemini-3.5-flash-high），不传则用 agy CLI 默认模型。
    stdin=/dev/null，防止 agy 在非 TTY 环境下死锁等待 EOF。
    """
    _recover_orphaned_disabled()
    agy = _resolve_agy()
    with _GEMINI_LOCK:
        was_hidden = _disable_gemini_md()
    try:
        cmd = [agy, "--print-timeout", f"{timeout_seconds}s"]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["-p", prompt])
        proc = subprocess.run(  # noqa: S603 — agy path 由 _resolve_agy() 解析，非用户输入
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 30,
            stdin=subprocess.DEVNULL,
            cwd=os.path.expanduser("~"),
        )
    finally:
        with _GEMINI_LOCK:
            _restore_gemini_md(was_hidden)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"agy exited {proc.returncode}: {tail}")

    out = _strip_memory_update_block(proc.stdout.strip())
    if not out:
        raise RuntimeError(f"agy returned empty result; stderr={proc.stderr[-300:]!r}")
    return out
