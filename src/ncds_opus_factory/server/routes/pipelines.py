"""Pipeline / Jobs HTTP 端点。

端点
----
GET    /pipelines                                列出已注册 pipeline 定义（前端模板列表用）
GET    /pipelines/{pipeline_id}                  pipeline 详情（节点 schema、默认布局）
POST   /jobs                                     创建作品（body: pipeline_id / title / inputs）
GET    /jobs                                     列表
GET    /jobs/{job_id}                            作品详情（节点状态 + 用户位置）
DELETE /jobs/{job_id}                            删除作品（含工作目录）
POST   /jobs/{job_id}/nodes/{node}/run           跑某节点（会 reset 自身 + 下游）
PUT    /jobs/{job_id}/nodes/{node}/position      更新节点画布位置
GET    /jobs/{job_id}/episode                    读 rw 节点产物 episode.json
PUT    /jobs/{job_id}/episode                    写 episode.json（用户微调）
GET    /jobs/{job_id}/events                     SSE 事件流（节点状态变更）
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncGenerator
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ncds_opus_factory.pipelines import PIPELINE_REGISTRY
from ncds_opus_factory.server.state import PIPELINE_RUNNER

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreateJobRequest(BaseModel):
    pipeline_id: str
    title: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)


class NodePositionRequest(BaseModel):
    x: float
    y: float


# ---------------------------------------------------------------------------
# Pipelines（模板定义）
# ---------------------------------------------------------------------------

def _serialize_pipeline(pipeline_id: str) -> dict[str, Any]:
    p = PIPELINE_REGISTRY[pipeline_id]
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "nodes": [
            {
                "name": n.name,
                "label": n.label,
                "cmd": n.cmd,
                "deps": list(n.deps),
                "out_dir": n.out_dir,
                "description": n.description,
                "kind": n.kind,
                "position": {"x": n.position.x, "y": n.position.y},
            }
            for n in p.nodes
        ],
    }


@router.get("/pipelines")
async def list_pipelines() -> dict[str, Any]:
    return {"pipelines": [_serialize_pipeline(pid) for pid in PIPELINE_REGISTRY]}


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: str) -> dict[str, Any]:
    if pipeline_id not in PIPELINE_REGISTRY:
        raise HTTPException(404, f"pipeline not found: {pipeline_id}")
    return _serialize_pipeline(pipeline_id)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@router.post("/jobs")
async def create_job(body: CreateJobRequest) -> dict[str, Any]:
    if body.pipeline_id not in PIPELINE_REGISTRY:
        raise HTTPException(404, f"pipeline not found: {body.pipeline_id}")
    state = PIPELINE_RUNNER.create_job(body.pipeline_id, body.title, body.inputs)
    return _serialize_job(state)


@router.get("/jobs")
async def list_jobs() -> dict[str, Any]:
    return {"jobs": PIPELINE_RUNNER.list_jobs()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    try:
        state = PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")
    return _serialize_job(state)


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str) -> dict[str, Any]:
    try:
        PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")
    job_dir = PIPELINE_RUNNER.video_jobs_dir / job_id
    shutil.rmtree(job_dir, ignore_errors=True)
    return {"deleted": job_id}


@router.post("/jobs/{job_id}/nodes/{node}/run")
async def run_node(job_id: str, node: str) -> dict[str, Any]:
    try:
        await PIPELINE_RUNNER.run_node(job_id, node)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    state = PIPELINE_RUNNER.get_job(job_id)
    return _serialize_job(state)


@router.put("/jobs/{job_id}/nodes/{node}/position")
async def update_position(job_id: str, node: str, body: NodePositionRequest) -> dict[str, Any]:
    try:
        PIPELINE_RUNNER.update_node_position(job_id, node, body.x, body.y)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"job_id": job_id, "node": node, "position": {"x": body.x, "y": body.y}}


@router.get("/jobs/{job_id}/episode")
async def get_episode(job_id: str) -> dict[str, Any]:
    try:
        PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")
    ep = PIPELINE_RUNNER.get_episode(job_id)
    if ep is None:
        raise HTTPException(404, "episode.json not yet produced. Run rw node first.")
    return ep


@router.put("/jobs/{job_id}/episode")
async def put_episode(job_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")
    PIPELINE_RUNNER.write_episode(job_id, body)
    return {"ok": True, "beats": len(body.get("beats", [])), "scenes": len(body.get("scenes", {}))}


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}/events")
async def stream_events(job_id: str) -> EventSourceResponse:
    """SSE：订阅 job 的节点状态变更事件。

    协议
    ----
    每条事件 data 字段是一行 JSON：
        {"type": "node_status", "job_id": "...", "node": "asr", "state": {...}}
        {"type": "job_updated",  "job_id": "...", "state": {...}}
    无终止信号；前端按需 close 连接。
    """
    try:
        PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")

    queue = PIPELINE_RUNNER.bus.subscribe(job_id)

    async def gen() -> AsyncGenerator[dict, None]:
        # 首条：把全量 state 推一遍，让前端不需要再 GET /jobs/{id}
        snapshot = PIPELINE_RUNNER.get_job(job_id)
        yield {"data": _json_dumps({
            "type": "snapshot",
            "job_id": job_id,
            "state": _serialize_job(snapshot),
        })}
        try:
            while True:
                event = await queue.get()
                yield {"data": _json_dumps(event)}
        except asyncio.CancelledError:
            raise
        finally:
            PIPELINE_RUNNER.bus.unsubscribe(job_id, queue)

    return EventSourceResponse(gen())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_job(state: Any) -> dict[str, Any]:
    """JobState dataclass → dict（asdict）+ 字段加点 UI 关心的衍生信息。"""
    d = asdict(state)
    return d


def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
