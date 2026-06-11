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
from ncds_opus_factory.server.state import LABELS, RUNNER, STORE

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
    task_id = await RUNNER.submit(
        body.cmd,
        body.params,
        source=body.source,
        parent_task_id=body.parent_task_id,
        round_id=body.round_id,
    )
    response.headers["Location"] = f"/tasks/{task_id}"
    logger.info(
        "[server] task submitted: cmd=%s task_id=%s source=%s",
        body.cmd, task_id, body.source or "user",
    )
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
        source=meta.source,
        parent_task_id=meta.parent_task_id,
        round_id=meta.round_id,
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
        reviewer=body.reviewer,
        note_origin=body.note_origin,
    )
    STORE.write_review(task_id, review)
    # 同步落案卷（决策=标注,复盘的训练数据真源）。失败不挡验收:review.json 已写,
    # 沈括弃用件清扫前还有补写兜底(app.sweep);其余 cmd 改判时也会重写。
    try:
        meta = STORE.get_meta(task_id)
        if meta is not None:
            LABELS.write(meta, review, STORE.get_result(task_id))
    except Exception:  # noqa: BLE001
        logger.exception("[server] label write failed: task_id=%s", task_id)
    logger.info("[server] task reviewed: task_id=%s decision=%s", task_id, body.decision)
    return review


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    """取消未开始/进行中的任务 -> cancelled(App 归入已归档,可恢复重新入队)。

    工作线程无法强杀:运行中的任务标记取消后后台跑完,结果作废、状态保持 cancelled。
    """
    if not STORE.exists(task_id):
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    meta = STORE.get_meta(task_id)
    if meta.status not in ("pending", "running"):
        raise HTTPException(status_code=409, detail=f"cannot cancel a {meta.status} task")
    STORE.update_status(task_id, "cancelled")
    STORE.append_progress(task_id, "任务已被用户取消")
    logger.info("[server] task cancelled: task_id=%s (was %s)", task_id, meta.status)
    return {"ok": True, "status": "cancelled"}


@router.post("/tasks/{task_id}/restore")
async def restore_task(task_id: str) -> dict:
    """已取消任务恢复并重新入队(沿用原 task_id 与参数,事件流续写)。"""
    if not STORE.exists(task_id):
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    meta = STORE.get_meta(task_id)
    if meta.status != "cancelled":
        raise HTTPException(status_code=409, detail=f"cannot restore a {meta.status} task")
    STORE.reset_for_requeue(task_id)
    STORE.append_progress(task_id, "已恢复,重新入队")
    await RUNNER.requeue(task_id, meta.cmd, meta.params)
    logger.info("[server] task restored: task_id=%s cmd=%s", task_id, meta.cmd)
    return {"ok": True, "status": "pending"}


@router.delete("/tasks/{task_id}/review")
async def revoke_review(task_id: str) -> dict:
    """撤销人工决策:已归档拉回「待验收」(删 review.json)。

    幂等:本来就没有决策也返回 ok(removed=false)。任务不存在 -> 404。
    """
    if not STORE.exists(task_id):
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    removed = STORE.delete_review(task_id)
    # 案卷不删,标 revoked(被撤回的判断不能留在训练集里冒充有效标注)。
    # 自愈式:即使本次 removed=False,只要案卷还在就补标——覆盖上一次打标失败
    # 后的重试(review.json 已删而案卷未标的半完成态)。失败不挡撤销,下次再试。
    if removed or LABELS.get(task_id) is not None:
        try:
            LABELS.mark_revoked(task_id)
        except Exception:  # noqa: BLE001
            logger.exception("[server] label revoke mark failed: task_id=%s", task_id)
    logger.info("[server] task review revoked: task_id=%s removed=%s", task_id, removed)
    return {"ok": True, "removed": removed}


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
            if meta and meta.status in ("completed", "failed", "cancelled"):
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
