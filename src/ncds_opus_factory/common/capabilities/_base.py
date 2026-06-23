"""capabilities 公共基座：进度回调类型 + 仓库根定位（无业务依赖，避免循环 import）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

ProgressFn = Callable[[str], None]

# 仓库根：src/ncds_opus_factory/common/capabilities/_base.py -> parents[4]
REPO_ROOT = Path(__file__).resolve().parents[4]


def noop(_text: str) -> None:
    """默认进度回调：吞掉（采集能力默认静默，调用方按需传 on_progress）。"""


def read_repo_env_value(key: str) -> str | None:
    """读取仓库根 .env 中的单个 key；只服务项目内能力配置。"""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def read_dashscope_key() -> str | None:
    """DashScope key: env DASHSCOPE_API_KEY > 仓库根 .env。"""
    if os.getenv("DASHSCOPE_API_KEY"):
        return os.environ["DASHSCOPE_API_KEY"]
    return read_repo_env_value("DASHSCOPE_API_KEY")
