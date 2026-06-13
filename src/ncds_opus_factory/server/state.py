"""Server 端单例：TaskStore + TaskRunner + PipelineRunner + 生产引擎(InstanceStore/Runner)。

放到独立模块避免 app.py 与 routes/* 之间的循环 import。
STATE_DIR 默认 ncds-opus-studio/state/tasks/，可用 NOF_STATE_DIR 覆盖。
VIDEO_JOBS_DIR 默认 ncds-opus-studio/video-jobs/，可用 NOF_VIDEO_JOBS_DIR 覆盖。
INSTANCES_DIR 默认 ncds-opus-studio/state/instances/，可用 NOF_INSTANCES_DIR 覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path

from ncds_opus_factory.commands import build_full_registry
from ncds_opus_factory.server.engine.instance_runner import InstanceRunner
from ncds_opus_factory.server.engine.instance_store import InstanceStore
from ncds_opus_factory.server.label_store import LabelStore
from ncds_opus_factory.server.mock_agents import maybe_mock_registry
from ncds_opus_factory.server.pipeline_runner import PipelineRunner
from ncds_opus_factory.server.task_runner import TaskRunner
from ncds_opus_factory.server.task_store import TaskStore

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_STATE_DIR = _REPO_ROOT / "state" / "tasks"
_DEFAULT_VIDEO_JOBS_DIR = _REPO_ROOT / "video-jobs"
_DEFAULT_INSTANCES_DIR = _REPO_ROOT / "state" / "instances"

STATE_DIR: Path = Path(os.environ.get("NOF_STATE_DIR", _DEFAULT_STATE_DIR))
# 案卷库与任务目录解耦（清扫删任务,案卷永存）。默认 state/wolong/labels,可 NOF_LABELS_DIR 覆盖。
LABELS_DIR: Path = Path(os.environ.get("NOF_LABELS_DIR", STATE_DIR.parent / "wolong" / "labels"))
VIDEO_JOBS_DIR: Path = Path(os.environ.get("NOF_VIDEO_JOBS_DIR", _DEFAULT_VIDEO_JOBS_DIR))
INSTANCES_DIR: Path = Path(os.environ.get("NOF_INSTANCES_DIR", _DEFAULT_INSTANCES_DIR))

STORE: TaskStore = TaskStore(STATE_DIR)
LABELS: LabelStore = LabelStore(LABELS_DIR)
# NOF_MOCK_AGENTS 控制哪些命令走 mock（测试期不真调 codex/opus）
# labels 注入:cron 任务完成自动归档时由 runner 直接落案卷
RUNNER: TaskRunner = TaskRunner(STORE, maybe_mock_registry(build_full_registry()), labels=LABELS)
PIPELINE_RUNNER: PipelineRunner = PipelineRunner(VIDEO_JOBS_DIR)
# 生产引擎（E1）：与 TaskRunner 同款晚绑定派发表（含 mock 门），recipes 取内置 RECIPE_REGISTRY。
INSTANCE_STORE: InstanceStore = InstanceStore(INSTANCES_DIR)
INSTANCE_RUNNER: InstanceRunner = InstanceRunner(
    INSTANCE_STORE, maybe_mock_registry(build_full_registry())
)
