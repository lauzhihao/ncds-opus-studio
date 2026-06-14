"""capabilities 公共基座：进度回调类型 + 仓库根定位（无业务依赖，避免循环 import）。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

ProgressFn = Callable[[str], None]

# 仓库根：src/ncds_opus_factory/common/capabilities/_base.py -> parents[4]
REPO_ROOT = Path(__file__).resolve().parents[4]


def noop(_text: str) -> None:
    """默认进度回调：吞掉（采集能力默认静默，调用方按需传 on_progress）。"""
