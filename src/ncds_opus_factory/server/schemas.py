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


class TaskCreateRequest(BaseModel):
    """POST /tasks/{cmd} 请求体。

    params 直接 spread 给 command.run(**params)，每个命令字段不同：
        wst: {"prompt": "...", "timeout_seconds": 600}
        vid: {"prompt": "...", "ref_image_urls": [...], "duration": 5}
        ...
    """

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
