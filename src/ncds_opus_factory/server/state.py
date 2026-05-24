"""Server 端单例：TaskStore + TaskRunner。

放到独立模块避免 app.py 与 routes/* 之间的循环 import。
STATE_DIR 默认 ncds-opus-studio/state/tasks/，可用 NOF_STATE_DIR 覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path

from ncds_opus_factory.commands import COMMAND_REGISTRY
from ncds_opus_factory.server.task_runner import TaskRunner
from ncds_opus_factory.server.task_store import TaskStore

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_STATE_DIR = _REPO_ROOT / "state" / "tasks"

STATE_DIR: Path = Path(os.environ.get("NOF_STATE_DIR", _DEFAULT_STATE_DIR))
STORE: TaskStore = TaskStore(STATE_DIR)
RUNNER: TaskRunner = TaskRunner(STORE, COMMAND_REGISTRY)
