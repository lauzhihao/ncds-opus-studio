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


class UpdateJobTitleRequest(BaseModel):
    title: str


class UpdateInputsRequest(BaseModel):
    """input 节点抽屉里 PUT 过来的字段。

    - url       : 单条链接（向后兼容）
    - urls      : 多条链接
    - raw_text  : 用户在 textarea 里粘贴的整段抖音原始分享文本
    - shares    : 前端从 raw_text 里解析出的结构化数组（每条含 url/author/tags）
    服务端只做持久化，不再解析。
    """
    url: str | None = None
    urls: list[str] | None = None
    raw_text: str | None = None
    shares: list[dict[str, Any]] | None = None


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
async def run_node(
    job_id: str,
    node: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    params = (body or {}).get("params") if isinstance(body, dict) else None
    try:
        await PIPELINE_RUNNER.run_node(job_id, node, params)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    state = PIPELINE_RUNNER.get_job(job_id)
    return _serialize_job(state)


@router.post("/jobs/{job_id}/nodes/{node}/cancel")
async def cancel_node(job_id: str, node: str) -> dict[str, Any]:
    try:
        cancelled = await PIPELINE_RUNNER.cancel_node(job_id, node)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"cancelled": cancelled, "job_id": job_id, "node": node}


class SelectModelBody(BaseModel):
    model_id: str


@router.post("/jobs/{job_id}/nodes/rw/rewrite/{model_id}")
async def rewrite_rw_model(job_id: str, model_id: str) -> dict[str, Any]:
    try:
        await PIPELINE_RUNNER.rewrite_rw_model(job_id, model_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "job_id": job_id, "model_id": model_id}


@router.put("/jobs/{job_id}/nodes/rw/select")
async def select_rw_model(job_id: str, body: SelectModelBody) -> dict[str, Any]:
    try:
        PIPELINE_RUNNER.select_rw_model(job_id, body.model_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "job_id": job_id, "selected_model_id": body.model_id}


@router.post("/jobs/{job_id}/nodes/image/regen/{scene_id}")
async def regen_image_scene(job_id: str, scene_id: str) -> dict[str, Any]:
    """重生 image 节点下某个 scene 的图片，不影响其他场景和下游节点。"""
    try:
        await PIPELINE_RUNNER.regen_image_scene(job_id, scene_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "job_id": job_id, "scene_id": scene_id}


@router.post("/jobs/{job_id}/scenes/{scene_id}/regen-image")
async def regen_scene_image_from_preview(job_id: str, scene_id: str) -> dict[str, Any]:
    """preview 抽屉里点「生成图片」用：不要求 image 节点 done，直出图片。"""
    try:
        rel = await PIPELINE_RUNNER.regen_scene_image_from_preview(job_id, scene_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"image_relpath": rel}


@router.post("/jobs/{job_id}/nodes/tts/regen/{index}")
async def regen_tts_beat(job_id: str, index: int) -> dict[str, Any]:
    """重生 tts 节点下某条字幕的音频（014 逐句），不影响其他句和下游节点。"""
    try:
        await PIPELINE_RUNNER.regen_tts_beat(job_id, index)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "job_id": job_id, "index": index}


@router.post("/jobs/{job_id}/nodes/tts/regen-scene/{scene_id}")
async def regen_tts_scene(job_id: str, scene_id: str) -> dict[str, Any]:
    """015：重生指定 scene 的整段音频，不影响其他 scene 和下游节点。"""
    try:
        await PIPELINE_RUNNER.regen_tts_scene(job_id, scene_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "job_id": job_id, "scene_id": scene_id}


@router.put("/jobs/{job_id}/inputs")
async def update_inputs(job_id: str, body: UpdateInputsRequest) -> dict[str, Any]:
    """更新 input 节点：urls / raw_text / shares 任一组合都接受。

    服务端纯持久化，不解析。前端的正则在 textarea onChange 时实时跑。
    """
    try:
        PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")
    inputs: dict[str, Any] = {}
    if body.urls is not None:
        cleaned = [u.strip() for u in body.urls if u and u.strip()]
        inputs["urls"] = cleaned
        inputs["url"] = cleaned[0] if cleaned else ""
    elif body.url is not None:
        inputs["url"] = body.url.strip()
        inputs["urls"] = [body.url.strip()] if body.url.strip() else []
    if body.raw_text is not None:
        inputs["raw_text"] = body.raw_text
    if body.shares is not None:
        inputs["shares"] = body.shares
    if not inputs:
        raise HTTPException(400, "no inputs provided")
    PIPELINE_RUNNER.update_inputs(job_id, inputs)
    return {"ok": True, "inputs": inputs}


@router.put("/jobs/{job_id}/title")
async def update_title(job_id: str, body: UpdateJobTitleRequest) -> dict[str, Any]:
    try:
        PIPELINE_RUNNER.update_title(job_id, body.title.strip())
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")
    state = PIPELINE_RUNNER.get_job(job_id)
    return {"job_id": state.job_id, "title": state.title}


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
