"""任务（task）= 命令的一次执行实例。命令定义(catalog)在 routes/commands.py。

- GET  /tasks                   列出任务实例（meta，倒序）
- POST /tasks                   提交任务（body: {cmd, params}）→ 201 + Location: /tasks/{id}
- GET  /tasks/{task_id}         查任务详情（meta + 终态 result + artifacts）
- GET  /tasks/{task_id}/events  SSE 拉进度（先回放 events.jsonl，再 tail 新增）

终止判断：meta.status in (completed, failed) 且 events.jsonl 已读完 → 发 [DONE]。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import datetime

from fastapi import APIRouter, HTTPException, Response
from sse_starlette.sse import EventSourceResponse

from ncds_opus_factory.server.artifacts import extract_artifacts
from ncds_opus_factory.server.schemas import (
    Review,
    ReviewRequest,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDetailResponse,
    TaskMeta,
)
from ncds_opus_factory.server.state import RUNNER, STORE

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/tasks", response_model=list[TaskMeta])
async def list_tasks() -> list[TaskMeta]:
    """列出所有任务实例（meta，最新在前）。命令清单见 GET /commands。"""
    return STORE.list_tasks()


@router.post("/tasks", response_model=TaskCreateResponse, status_code=201)
async def create_task(body: TaskCreateRequest, response: Response) -> TaskCreateResponse:
    """提交一个任务，立即返回 task_id（任务后台异步执行）。

    REST：201 Created + Location 头指向新建任务资源。
    """
    if body.cmd not in RUNNER.registry:
        raise HTTPException(
            status_code=404,
            detail=f"unknown command: {body.cmd}. available: {RUNNER.list_commands()}",
        )
    task_id = await RUNNER.submit(body.cmd, body.params)
    response.headers["Location"] = f"/tasks/{task_id}"
    logger.info("[server] task submitted: cmd=%s task_id=%s", body.cmd, task_id)
    return TaskCreateResponse(task_id=task_id, status="pending")


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_task(task_id: str) -> TaskDetailResponse:
    """查询任务详情。终态时 result 字段会包含 run 返回值。"""
    meta = STORE.get_meta(task_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    result = STORE.get_result(task_id) if meta.status == "completed" else None
    artifacts = extract_artifacts(meta.cmd, result) if result else None
    return TaskDetailResponse(
        task_id=meta.task_id,
        cmd=meta.cmd,
        params=meta.params,
        status=meta.status,
        created_at=meta.created_at,
        started_at=meta.started_at,
        finished_at=meta.finished_at,
        error=meta.error,
        result=result,
        artifacts=artifacts,
        review=STORE.get_review(task_id),
    )


@router.post("/tasks/{task_id}/review", response_model=Review)
async def review_task(task_id: str, body: ReviewRequest) -> Review:
    """记录一次人工决策（移动端点同意/拒绝 + 可选备注）。

    幂等覆盖：再次提交即改判。决策落 state/tasks/{id}/review.json，不影响任务执行。
    任务不存在 -> 404。
    """
    if not STORE.exists(task_id):
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    review = Review(
        decision=body.decision,
        note=body.note,
        reviewed_at=datetime.now().isoformat(),
    )
    STORE.write_review(task_id, review)
    logger.info("[server] task reviewed: task_id=%s decision=%s", task_id, body.decision)
    return review


# SSE polling 周期：500ms 既能近实时推进度，也不会把 CPU 烧穿
_TAIL_POLL_INTERVAL = 0.5


@router.get("/tasks/{task_id}/events")
async def stream_events(task_id: str) -> EventSourceResponse:
    """SSE 推送任务事件：先回放已有事件，再 tail 新增直到终态。"""
    if not STORE.exists(task_id):
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    events_path = STORE.events_path(task_id)

    async def gen() -> AsyncGenerator[dict, None]:
        # 1) 回放历史
        last_pos = 0
        with events_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line:
                    yield {"data": line}
            last_pos = f.tell()

        # 2) tail 新增 + 等待终态
        while True:
            await asyncio.sleep(_TAIL_POLL_INTERVAL)
            try:
                size = events_path.stat().st_size
            except FileNotFoundError:
                break
            if size > last_pos:
                with events_path.open("r", encoding="utf-8") as f:
                    f.seek(last_pos)
                    for line in f:
                        line = line.rstrip("\n")
                        if line:
                            yield {"data": line}
                    last_pos = f.tell()
            meta = STORE.get_meta(task_id)
            if meta and meta.status in ("completed", "failed"):
                # 终态后再读一轮，确保 done/error 事件被吐完
                try:
                    size = events_path.stat().st_size
                except FileNotFoundError:
                    size = last_pos
                if size > last_pos:
                    with events_path.open("r", encoding="utf-8") as f:
                        f.seek(last_pos)
                        for line in f:
                            line = line.rstrip("\n")
                            if line:
                                yield {"data": line}
                yield {"data": "[DONE]"}
                return

    return EventSourceResponse(gen())
