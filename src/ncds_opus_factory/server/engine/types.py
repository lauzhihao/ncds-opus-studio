"""生产引擎数据模型 + 步骤状态机。

对齐 [PRODUCTION-ENGINE-DESIGN.md](../../../../docs/PRODUCTION-ENGINE-DESIGN.md) §2/§4：
- Recipe / RecipeStep：配方骨架（纯数据，步骤只引字符串 key，引擎晚绑定派发）
- InstanceMeta / StepState / InstanceState：一条生产实例的运行态
- InstanceEvent：分层事件（meta / step / detail），给 app/web 两视图按需订阅
- 步骤状态机：idle→queued→running→draft_ready→(awaiting_review→approved/rejected/editing)→done|failed
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 复用既有「人工决策」模型（state/tasks 里早有）；统一实例沿用同一语义。
from ncds_opus_factory.server.schemas import Review

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------
InstanceStatus = Literal["pending", "running", "paused", "completed", "failed", "cancelled"]

StepStatus = Literal[
    "idle",            # 未开始
    "queued",          # 已排队
    "running",         # 执行中
    "draft_ready",     # agent 出了草稿
    "awaiting_review", # 等人工介入（content_edit / decision_only）
    "approved",        # 人批准
    "rejected",        # 人打回
    "editing",         # 人正在改内容（web content_edit）
    "done",            # 定稿完成
    "failed",          # 出错
    "skipped",         # 跳过（如纯 UI 节点 / 被打回略过）
]

DriverKind = Literal["manual", "wolong"]
InterventionKind = Literal["content_edit", "decision_only"]  # None = 无介入点
MaterialSource = Literal["generated", "collected"]
StepKind = Literal["input", "process", "output"]


# ---------------------------------------------------------------------------
# 步骤状态机（design §4）
# ---------------------------------------------------------------------------
# 允许的状态迁移；engine 据此守门，避免 running+rejected 之类的非法态。
_STEP_TRANSITIONS: dict[str, set[str]] = {
    "idle": {"queued", "running", "skipped", "done", "failed"},  # done：直通步；failed：派发前出错
    "queued": {"running", "idle", "failed"},
    "running": {"draft_ready", "done", "failed"},
    "draft_ready": {"awaiting_review", "done"},          # 有介入点→awaiting_review；无→done
    "awaiting_review": {"approved", "rejected", "editing"},
    "editing": {"running", "done", "awaiting_review"},    # 改完重跑 / 直接定稿
    "approved": {"done", "running"},                      # 批准后定稿或触发后续
    "rejected": {"idle", "skipped", "running"},           # 打回重做 / 略过
    "done": {"idle"},                                     # 重跑回 idle
    "failed": {"idle", "queued"},                         # 重试
    "skipped": {"idle"},
}


def can_transition(old: str, new: str) -> bool:
    """``old`` → ``new`` 是否是合法步骤状态迁移（同态 no-op 视为合法）。"""
    if old == new:
        return True
    return new in _STEP_TRANSITIONS.get(old, set())


# ---------------------------------------------------------------------------
# Recipe（配方骨架）
# ---------------------------------------------------------------------------
class RecipeStep(BaseModel):
    """配方里的一步（静态定义）。"""

    step_id: str
    label: str = ""
    cmd: str | None = None         # 命中 build_full_registry() 的 key（render_015/tts/wst…）
    agent: str | None = None       # agent 步（liuyong/wudaozi/shenkuo…）；与 cmd 二选一
    deps: list[str] = Field(default_factory=list)
    expensive: bool = False        # 贵步骤（生图/tts/render）；driver 据此在其前插强制闸门
    intervention: InterventionKind | None = None
    material_source: MaterialSource | None = None
    kind: StepKind = "process"

    @property
    def performer(self) -> str | None:
        """派发用的 registry key：优先 cmd，其次 agent。input/output/无执行体的步返回 None。"""
        return self.cmd or self.agent


class Recipe(BaseModel):
    """一条生产配方（DAG 骨架）。"""

    recipe_id: str
    name: str
    description: str = ""
    template_renderer: str = ""    # 渲染模板（paper_card_talk_015 / figure_talk…）
    steps: list[RecipeStep]

    def step(self, step_id: str) -> RecipeStep:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        raise KeyError(f"step not found: {step_id} in recipe {self.recipe_id}")

    def step_ids(self) -> list[str]:
        return [s.step_id for s in self.steps]

    def topological_order(self) -> list[str]:
        """按 deps 的拓扑序返回 step_id（输入顺序作 tie-break）。"""
        order: list[str] = []
        seen: set[str] = set()
        by_id = {s.step_id: s for s in self.steps}

        def visit(sid: str) -> None:
            if sid in seen:
                return
            for dep in by_id[sid].deps:
                if dep in by_id:
                    visit(dep)
            seen.add(sid)
            order.append(sid)

        for s in self.steps:
            visit(s.step_id)
        return order


# ---------------------------------------------------------------------------
# 运行态
# ---------------------------------------------------------------------------
class StepState(BaseModel):
    """单步运行态。"""

    step_id: str
    status: StepStatus = "idle"
    task_id: str | None = None      # 关联到底层执行体（后续接 TaskRunner 时填）
    started_at: str | None = None
    finished_at: str | None = None
    progress: str = ""              # 最新进度文本（来自 on_progress）
    draft: dict | None = None       # agent 出的草稿（review/edit 前；web 可改）
    draft_source: Literal["agent", "user"] = "agent"
    decision: Literal["approved", "rejected", "pending"] | None = None
    review: Review | None = None    # 人工审看记录（喂 label_store / retro）
    outputs: dict = Field(default_factory=dict)   # 定稿产物
    config: dict = Field(default_factory=dict)    # 该步运行参数
    error: str | None = None


class InstanceMeta(BaseModel):
    """生产实例元信息（独立于步骤）。"""

    instance_id: str
    owner_id: str | None = None     # 多租户预留（演示 None，生产由鉴权中间件填）
    recipe_id: str
    recipe_version: str = "latest"
    status: InstanceStatus = "pending"
    title: str = ""
    created_at: str
    updated_at: str
    # 溯源（沿用 TaskMeta 语义）
    source: str | None = None       # user/wolong/gate/cron/retro
    driver: DriverKind = "manual"   # 谁在驱动这条实例
    round_id: str | None = None
    parent_instance_id: str | None = None
    intent_key: str | None = None


class InstanceState(BaseModel):
    """一条生产实例的完整状态（meta + 各步 + 输入）。"""

    meta: InstanceMeta
    inputs: dict = Field(default_factory=dict)
    steps: dict[str, StepState] = Field(default_factory=dict)  # key=step_id
    canvas_state: dict = Field(default_factory=dict)           # web 画布（纯视图）
    selected_choices: dict = Field(default_factory=dict)       # step_id -> choice


class InstanceEvent(BaseModel):
    """实例状态变更事件（分层 SSE）。"""

    instance_id: str
    ts: int                         # Unix ms
    level: Literal["meta", "step", "detail"]
    type: Literal["status", "progress", "draft", "decision", "output"]
    step_id: str | None = None
    payload: dict = Field(default_factory=dict)
