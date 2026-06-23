"""Server 端 pydantic 模型。

任务模型设计：
- TaskMeta 写在 state/{task_id}/meta.json，记录命令名、参数、状态、时间戳
- TaskEvent 逐行写到 state/{task_id}/events.jsonl，SSE tail 这个文件
- 终态 result 写到 state/{task_id}/result.json
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["pending", "running", "completed", "failed", "cancelled"]

# 移动端「点同意/拒绝」的决策值。决策落在 state/tasks/{id}/review.json，
# 与 meta/events/result 并列，不污染 agent 代码，也不改任务执行流程。
ReviewDecision = Literal["approved", "rejected"]

# 任务发起方（docs/WOLONG-DESIGN.md §3）。缺省 None 视为 user（向后兼容旧请求/旧任务）：
#   user=移动端手发  wolong=卧龙派单段  gate=验收/终态事件触发的续跑段
#   cron=订阅传感器  retro=离线复盘  pipeline=web 画布节点内部调度
TaskSource = Literal["user", "wolong", "gate", "cron", "retro", "pipeline"]

# 决策是谁写的（docs/WOLONG-DESIGN.md §4.7/§5.1 的防火墙字段）：
#   user=Leader 人工验收  wolong=卧龙预筛预测  system=后端自动归档(免验收任务)
# 离线复盘只把 reviewer=user 的案卷当标注样本。
Reviewer = Literal["user", "wolong", "system"]

# 备注的来源：user=人工口述/输入  machine=客户端模板生成(如柳永"采用 X 稿")
# inferred=卧龙事后推断。复盘归纳时 machine/inferred 只作辅助线索。
NoteOrigin = Literal["user", "machine", "inferred"]


class TaskCreateRequest(BaseModel):
    """POST /tasks 请求体。

    cmd   = 要执行的命令名（见 GET /commands）。
    params = 直接 spread 给 command.run(**params)，每个命令字段不同（见 GET /commands/{cmd}/schema）：
        {"cmd": "wst", "params": {"prompt": "...", "timeout_seconds": 600}}
        {"cmd": "vid", "params": {"prompt": "...", "ref_image_urls": [...], "duration": 5}}
    """

    cmd: str
    params: dict[str, Any] = Field(default_factory=dict)
    # 溯源（可选，缺省=用户手发）。agent 派单/cron/续跑段创建任务时填写。
    source: TaskSource | None = None
    parent_task_id: str | None = None
    round_id: str | None = None
    # 派发幂等键(round_id+intent_key 唯一,§4.1):卧龙段崩溃重发不产生重复任务,
    # 调度器查重直接返回既有 task_id
    intent_key: str | None = None


class TaskMeta(BaseModel):
    """任务元信息。"""

    task_id: str
    cmd: str
    params: dict[str, Any]
    status: TaskStatus = "pending"
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    # 溯源三件套（None 不落盘，旧任务 meta.json 无这些键 → 读回即 None=user）
    source: TaskSource | None = None
    parent_task_id: str | None = None
    round_id: str | None = None
    intent_key: str | None = None
    # 列表态附带的人工决策（approved/rejected/未决=None）。仅 list_tasks 读 review.json
    # 后回填到内存对象，**不写入 meta.json**（_write_meta exclude_none 会丢弃 None）。
    decision: ReviewDecision | None = None
    # 展示标题/副题:命令完成后由 runner 从 result.task_title/task_subtitle 回填。
    # 任务卡用它显示作品信息(如沈括的 标题/话题),而不是原始参数里的分享链接。
    title: str | None = None
    subtitle: str | None = None


class ReviewRequest(BaseModel):
    """POST /tasks/{id}/review 请求体：移动端点同意/拒绝 + 可选备注。"""

    decision: ReviewDecision
    note: str | None = None
    # 缺省 user：移动端无需传。预筛(wolong)/自动归档(system)由后端自己写。
    reviewer: Reviewer = "user"
    # 备注来源。缺省按"有 note 即 user"推定；客户端模板生成的备注应显式传 machine。
    note_origin: NoteOrigin | None = None


class Review(BaseModel):
    """一条任务决策，落在 state/tasks/{id}/review.json。可重复改判（覆盖）。"""

    decision: ReviewDecision
    note: str | None = None
    reviewed_at: str
    # 旧 review.json 无此键 → pydantic 默认 user，向后兼容
    reviewer: Reviewer = "user"
    note_origin: NoteOrigin | None = None


class TaskEvent(BaseModel):
    """事件流条目。每行 JSON 写入 events.jsonl。"""

    # progress: command.run 通过 on_progress(text) 回调推送
    # done:     run 函数正常返回
    # error:    run 函数抛异常
    type: Literal["progress", "done", "error"]
    ts: int = Field(description="Unix timestamp in ms")
    text: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class TaskCreateResponse(BaseModel):
    task_id: str
    status: TaskStatus


class TaskDetailResponse(BaseModel):
    """GET /tasks/{task_id} 响应。"""

    task_id: str
    cmd: str
    params: dict[str, Any]
    status: TaskStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    # 溯源三件套（与列表一致:详情页角标/round 任务的重试策略都要读它）
    source: TaskSource | None = None
    parent_task_id: str | None = None
    round_id: str | None = None
    result: dict[str, Any] | None = None
    # 完成态时按命令从 result 提取的可审看产物清单（[{label, kind, url, path}]）；
    # 移动端据此读稿/听音/看片，见 server.artifacts.extract_artifacts。
    artifacts: list[dict[str, Any]] | None = None
    # 人工决策（移动端点同意/拒绝写入），未决为 None。见 POST /tasks/{id}/review。
    review: Review | None = None
    # 改判入口(P5):round 内任务的闸门决策是否已定案(decision 被续跑段消费,
    # 或 round 已终局——改判只影响案卷,不回卷流程)。无 round 的任务恒 False。
    decision_finalized: bool = False
