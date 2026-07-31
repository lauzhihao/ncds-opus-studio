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
from ncds_opus_factory.server.auth import AuthConfig, load_auth_config
from ncds_opus_factory.server.auth_store import AuthStore
from ncds_opus_factory.server.engine.instance_runner import InstanceRunner
from ncds_opus_factory.server.engine.instance_store import InstanceStore
from ncds_opus_factory.server.engine.pipeline_performers_final import PERFORMERS_FINAL
from ncds_opus_factory.server.engine.pipeline_performers_film import PERFORMERS_FILM
from ncds_opus_factory.server.label_store import LabelStore
from ncds_opus_factory.server.mock_agents import maybe_mock_registry
from ncds_opus_factory.server.pipeline_runner import PipelineRunner
from ncds_opus_factory.server.queue import get_default_queue
from ncds_opus_factory.server.task_runner import TaskRunner
from ncds_opus_factory.server.task_store import TaskStore

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_STATE_DIR = _REPO_ROOT / "state" / "tasks"
_DEFAULT_VIDEO_JOBS_DIR = _REPO_ROOT / "video-jobs"
_DEFAULT_INSTANCES_DIR = _REPO_ROOT / "state" / "instances"
_DEFAULT_AUTH_DB = _REPO_ROOT / "state" / "auth.db"

STATE_DIR: Path = Path(os.environ.get("NOF_STATE_DIR", _DEFAULT_STATE_DIR))
# 案卷库与任务目录解耦（清扫删任务,案卷永存）。默认 state/wolong/labels,可 NOF_LABELS_DIR 覆盖。
LABELS_DIR: Path = Path(os.environ.get("NOF_LABELS_DIR", STATE_DIR.parent / "wolong" / "labels"))
VIDEO_JOBS_DIR: Path = Path(os.environ.get("NOF_VIDEO_JOBS_DIR", _DEFAULT_VIDEO_JOBS_DIR))
INSTANCES_DIR: Path = Path(os.environ.get("NOF_INSTANCES_DIR", _DEFAULT_INSTANCES_DIR))
AUTH_DB_PATH: Path = Path(os.environ.get("NOF_AUTH_DB", _DEFAULT_AUTH_DB))

STORE: TaskStore = TaskStore(STATE_DIR)
# Google OAuth 用户/session；未配 GOOGLE_* 时 AUTH_CONFIG.enabled=False，不拦请求。
AUTH_STORE: AuthStore = AuthStore(AUTH_DB_PATH)
AUTH_CONFIG: AuthConfig = load_auth_config()
LABELS: LabelStore = LabelStore(LABELS_DIR)
# NOF_MOCK_AGENTS 控制哪些命令走 mock（测试期不真调 codex/opus）
# labels 注入:cron 任务完成自动归档时由 runner 直接落案卷
RUNNER: TaskRunner = TaskRunner(
    STORE,
    maybe_mock_registry(build_full_registry()),
    labels=LABELS,
    redis_queue=get_default_queue(),  # 显式传入，确保配额走 Redis 而非内存
)
PIPELINE_RUNNER: PipelineRunner = PipelineRunner(VIDEO_JOBS_DIR)
# 生产引擎（E1）：派发表 = bare command（含 mock 门）∪ final_preview orchestration performer（final_*）。
# final_preview recipe 各步的 cmd 指向 final_*（见 recipes.py），故引擎须能在合并表里解析它们。
# PERFORMERS_FINAL 是真实编排（不过 mock 门；其内部 helper 自行处理外部依赖）。
INSTANCE_STORE: InstanceStore = InstanceStore(INSTANCES_DIR)
INSTANCE_RUNNER: InstanceRunner = InstanceRunner(
    INSTANCE_STORE,
    {
        **maybe_mock_registry(build_full_registry()),
        **PERFORMERS_FINAL,
        **PERFORMERS_FILM,
    },
)
# 绞杀者（E1-b2 #3）：把引擎注入 PipelineRunner，NOF_ENGINE_NODES 命中的节点执行改走引擎。
# 在两个 runner 都建好后注入，避免 pipeline_runner ↔ state 的 import 环。
PIPELINE_RUNNER.attach_engine(INSTANCE_RUNNER)
# 内部命令：web `/jobs` 节点只在 server 入队，真正执行交给 nof-worker。
# 该命令不展示在 /commands catalog；见 routes/commands.py 的 INTERNAL_COMMANDS。
RUNNER.registry["pipeline_node"] = PIPELINE_RUNNER.run_pipeline_node_task
PIPELINE_RUNNER.attach_task_runner(RUNNER)
