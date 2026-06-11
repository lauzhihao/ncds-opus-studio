"""Server 端 pydantic 模型。

任务模型设计：
- TaskMeta 写在 state/{task_id}/meta.json，记录命令名、参数、状态、时间戳
- TaskEvent 逐行写到 state/{task_id}/events.jsonl，SSE tail 这个文件
- 终态 result 写到 state/{task_id}/result.json
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["pending", "running", "completed", "failed"]

# 移动端「点同意/拒绝」的决策值。决策落在 state/tasks/{id}/review.json，
# 与 meta/events/result 并列，不污染 agent 代码，也不改任务执行流程。
ReviewDecision = Literal["approved", "rejected"]


class TaskCreateRequest(BaseModel):
    """POST /tasks 请求体。

    cmd   = 要执行的命令名（见 GET /commands）。
    params = 直接 spread 给 command.run(**params)，每个命令字段不同（见 GET /commands/{cmd}/schema）：
        {"cmd": "wst", "params": {"prompt": "...", "timeout_seconds": 600}}
        {"cmd": "vid", "params": {"prompt": "...", "ref_image_urls": [...], "duration": 5}}
    """

    cmd: str
    params: dict[str, Any] = Field(default_factory=dict)


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


class Review(BaseModel):
    """一条任务决策，落在 state/tasks/{id}/review.json。可重复改判（覆盖）。"""

    decision: ReviewDecision
    note: str | None = None
    reviewed_at: str


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
    result: dict[str, Any] | None = None
    # 完成态时按命令从 result 提取的可审看产物清单（[{label, kind, url, path}]）；
    # 移动端据此读稿/听音/看片，见 server.artifacts.extract_artifacts。
    artifacts: list[dict[str, Any]] | None = None
    # 人工决策（移动端点同意/拒绝写入），未决为 None。见 POST /tasks/{id}/review。
    review: Review | None = None
