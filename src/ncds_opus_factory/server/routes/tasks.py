"""任务 HTTP 端点。

- POST /tasks/{cmd}        提交任务，返回 task_id
- GET  /tasks/{task_id}    查任务详情（meta + 终态 result）
- GET  /tasks/{task_id}/events  SSE 拉进度（先回放 events.jsonl，再 tail 新增）
- GET  /tasks                列出已注册 commands

终止判断：meta.status in (completed, failed) 且 events.jsonl 已读完 → 发 [DONE]。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from ncds_opus_factory.server.schemas import (
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDetailResponse,
)
from ncds_opus_factory.server.state import RUNNER, STORE

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/tasks")
async def list_commands() -> dict[str, list[str]]:
    """列出当前注册的所有 commands。"""
    return {"commands": RUNNER.list_commands()}


@router.post("/tasks/{cmd}", response_model=TaskCreateResponse)
async def create_task(cmd: str, body: TaskCreateRequest) -> TaskCreateResponse:
    """提交一个任务，立即返回 task_id；任务后台异步执行。"""
    if cmd not in RUNNER.registry:
        raise HTTPException(
            status_code=404,
            detail=f"unknown command: {cmd}. available: {RUNNER.list_commands()}",
        )
    task_id = await RUNNER.submit(cmd, body.params)
    logger.info("[server] task submitted: cmd=%s task_id=%s", cmd, task_id)
    return TaskCreateResponse(task_id=task_id, status="pending")


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_task(task_id: str) -> TaskDetailResponse:
    """查询任务详情。终态时 result 字段会包含 run 返回值。"""
    meta = STORE.get_meta(task_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    result = STORE.get_result(task_id) if meta.status == "completed" else None
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
    )


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
