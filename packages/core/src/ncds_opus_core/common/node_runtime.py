"""解析一个"自带全局 fetch"的 node 可执行文件。

背景：commands/*.py spawn 的若干 Node runner（content_rewrite / render）内部用全局
`fetch`。global fetch 是 Node 18 才引入的；而本机 nvm 下同时装着 v16/v17 等老版本，
当服务端（launchd / 非交互 shell）的 PATH 恰好把老 node 排在前面时，runner 会直接
`ReferenceError: fetch is not defined` 失败。

策略（保守，正常情况下行为与直接用 `node` 完全一致）：
  1. 显式 override：环境变量 NOF_NODE_BIN
  2. PATH 上的 `node`（绝大多数情况已是 v22，自带 fetch）
  3. 回退：扫 ~/.nvm/versions/node/*/bin/node，挑版本最高且有 fetch 的
  4. 兜底：返回 PATH 的 `node`（让调用方拿到明确的 runner 报错，而不是这里吞掉）

结果缓存：一次进程内 node 不会变，用 lru_cache 避免每次 spawn 都跑一遍探测子进程。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

_FETCH_PROBE = "process.exit(typeof fetch==='function'?0:1)"


def _has_fetch(node_bin: str) -> bool:
    """探测某个 node 二进制是否有全局 fetch。探测失败一律当作没有。"""
    try:
        proc = subprocess.run(
            [node_bin, "-e", _FETCH_PROBE],
            capture_output=True,
            timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _version_key(node_path: Path) -> tuple[int, int, int]:
    m = re.search(r"v(\d+)\.(\d+)\.(\d+)", str(node_path))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)  # type: ignore[return-value]


@lru_cache(maxsize=1)
def resolve_node() -> str:
    """返回一个自带全局 fetch 的 node 可执行路径；找不到则兜底返回 'node'。"""
    override = os.environ.get("NOF_NODE_BIN")
    if override and _has_fetch(override):
        return override

    path_node = shutil.which("node")
    if path_node and _has_fetch(path_node):
        return path_node

    # PATH 上的 node 没有 fetch：扫 nvm 目录挑版本最高且有 fetch 的
    candidates = sorted(
        Path.home().glob(".nvm/versions/node/*/bin/node"),
        key=_version_key,
        reverse=True,
    )
    for cand in candidates:
        if _has_fetch(str(cand)):
            return str(cand)

    # 实在找不到：返回 PATH node（可能为 None 则退回字面量 'node'），由调用方报错
    return path_node or "node"
