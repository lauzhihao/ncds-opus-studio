"""Pipeline / Jobs HTTP 端点。

注意：本文件除 /pipelines 外，承载了 /jobs 的绝大部分逻辑（创建/列表/详情/删除、
所有 nodes/* 节点操作、episode、cover、SSE events）。唯一例外是产物文件读写
GET/PUT /jobs/{job_id}/files/{relpath}，在 routes/jobs.py。按文件名找 /jobs 端点时留意。

端点
----
GET    /pipelines                                列出已注册 pipeline 定义（前端模板列表用）
GET    /pipelines/{pipeline_id}                  pipeline 详情（节点 schema、默认布局）
GET    /pipelines/{pipeline_id}/cover            模板封面（模板自带 episode 首场景图）
POST   /jobs                                     创建作品（body: pipeline_id / title / inputs）
GET    /jobs                                     列表
GET    /jobs/{job_id}                            作品详情（节点状态 + 用户位置）
DELETE /jobs/{job_id}                            删除作品（含工作目录）
GET    /jobs/{job_id}/cover                      作品封面（成片首帧 → 首场景容器图 → 404）
POST   /jobs/{job_id}/nodes/{node}/run           跑某节点（会 reset 自身 + 下游）
POST   /jobs/{job_id}/nodes/{node}/cancel        取消正在跑的节点
PUT    /jobs/{job_id}/nodes/{node}/position      更新节点画布位置
POST   /jobs/{job_id}/nodes/rw/rewrite/{model}   单模型重写 rw draft
PUT    /jobs/{job_id}/nodes/rw/select            选某模型 draft 为定稿
POST   /jobs/{job_id}/nodes/image/regen/{scene}  重生某 scene 容器图
POST   /jobs/{job_id}/nodes/tts/regen-scene/{s}  重生某 scene 音频
PUT    /jobs/{job_id}/inputs                     更新 input 节点（urls/raw_text/shares）
PUT    /jobs/{job_id}/title                      改作品标题
GET    /jobs/{job_id}/episode                    读 rw 节点产物 episode.json
PUT    /jobs/{job_id}/episode                    写 episode.json（用户微调）
GET    /jobs/{job_id}/events                     SSE 事件流（节点状态变更）
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import AsyncGenerator
from dataclasses import asdict
from typing import Any

from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ncds_opus_core.pipelines import PIPELINE_REGISTRY
from ncds_opus_factory.server.state import PIPELINE_RUNNER

logger = logging.getLogger(__name__)

router = APIRouter()

# ncds_opus_factory/templates（封面取模板自带 episode + pictures 的首场景图）
_TEMPLATES_ROOT = Path(__file__).resolve().parents[2] / "templates"


def _exc_msg(e: Exception) -> str:
    """KeyError 的 str() 会给消息套一层引号（str(KeyError("x")) == "'x'"）；
    取 args[0] 得到干净文案，与先 get_job + 自定义 HTTPException 的端点保持一致。"""
    return str(e.args[0]) if e.args else str(e)


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


@router.get("/pipelines/{pipeline_id}/cover")
async def pipeline_cover(pipeline_id: str) -> FileResponse:
    """模板封面：取模板自带 episode.json 第一个非章节场景的样例图。
    无现成图则 404，前端回退到数字 marker。"""
    if pipeline_id not in PIPELINE_REGISTRY:
        raise HTTPException(404, f"pipeline not found: {pipeline_id}")
    assets = _TEMPLATES_ROOT / pipeline_id / ".015-draft-assets"
    ep_path = assets / "episode.json"
    if not ep_path.is_file():
        raise HTTPException(404, "no template episode")
    try:
        ep = json.loads(ep_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(404, "template episode unreadable")
    for b in ep.get("beats") or []:
        sid = b.get("scene")
        if sid and not str(sid).startswith("ch"):
            pic = assets / "pictures" / f"{sid}.webp"
            if pic.is_file():
                return FileResponse(str(pic))
            break
    raise HTTPException(404, "no template cover image")


@router.get("/jobs/{job_id}/cover")
async def job_cover(job_id: str) -> FileResponse:
    """作品封面：成片首帧优先，回退首场景容器图；都没有 404（前端回退 marker）。"""
    try:
        PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")
    path = await PIPELINE_RUNNER.job_cover_path(job_id)
    if path is None:
        raise HTTPException(404, "no cover yet")
    return FileResponse(str(path))


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
    force = bool((body or {}).get("force")) if isinstance(body, dict) else False
    try:
        await PIPELINE_RUNNER.run_node(job_id, node, params, force=force)
    except KeyError as e:
        raise HTTPException(404, _exc_msg(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    state = PIPELINE_RUNNER.get_job(job_id)
    return _serialize_job(state)


@router.post("/jobs/{job_id}/nodes/{node}/cancel")
async def cancel_node(job_id: str, node: str) -> dict[str, Any]:
    # 先校验 job 存在：cancel_node 只查内存 _running_nodes、从不 raise KeyError，
    # 不显式校验的话不存在的 job 会静默返回 200 {"cancelled": false}，与同组其它端点不一致。
    try:
        PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")
    cancelled = await PIPELINE_RUNNER.cancel_node(job_id, node)
    return {"cancelled": cancelled, "job_id": job_id, "node": node}


class SelectModelBody(BaseModel):
    model_id: str


@router.post("/jobs/{job_id}/nodes/rw/rewrite/{model_id}")
async def rewrite_rw_model(job_id: str, model_id: str) -> dict[str, Any]:
    try:
        await PIPELINE_RUNNER.rewrite_rw_model(job_id, model_id)
    except KeyError as e:
        raise HTTPException(404, _exc_msg(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "job_id": job_id, "model_id": model_id}


@router.put("/jobs/{job_id}/nodes/rw/select")
async def select_rw_model(job_id: str, body: SelectModelBody) -> dict[str, Any]:
    try:
        PIPELINE_RUNNER.select_rw_model(job_id, body.model_id)
    except KeyError as e:
        raise HTTPException(404, _exc_msg(e))
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "job_id": job_id, "selected_model_id": body.model_id}


@router.post("/jobs/{job_id}/nodes/image/regen/{scene_id}")
async def regen_image_scene(job_id: str, scene_id: str) -> dict[str, Any]:
    """重生 image 节点下某个 scene 的图片，不影响其他场景和下游节点。"""
    try:
        await PIPELINE_RUNNER.regen_image_scene(job_id, scene_id)
    except KeyError as e:
        raise HTTPException(404, _exc_msg(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "job_id": job_id, "scene_id": scene_id}


@router.post("/jobs/{job_id}/nodes/image/regen-sketch/{scene_id}/{n}")
async def regen_image_sketch(job_id: str, scene_id: str, n: int) -> dict[str, Any]:
    """重生 image 节点下某个 scene 的第 n 幅简笔画（1-based），不影响容器图和其他简笔画。"""
    try:
        rel = await PIPELINE_RUNNER.regen_image_sketch(job_id, scene_id, n)
    except KeyError as e:
        raise HTTPException(404, _exc_msg(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "job_id": job_id, "scene_id": scene_id, "n": n, "image_relpath": rel}


@router.post("/jobs/{job_id}/scenes/{scene_id}/regen-image")
async def regen_scene_image_from_preview(job_id: str, scene_id: str) -> dict[str, Any]:
    """preview 抽屉里点「生成图片」用：不要求 image 节点 done，直出图片。"""
    try:
        rel = await PIPELINE_RUNNER.regen_scene_image_from_preview(job_id, scene_id)
    except KeyError as e:
        raise HTTPException(404, _exc_msg(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"image_relpath": rel}


@router.post("/jobs/{job_id}/nodes/tts/regen-scene/{scene_id}")
async def regen_tts_scene(job_id: str, scene_id: str) -> dict[str, Any]:
    """015：重生指定 scene 的整段音频，不影响其他 scene 和下游节点。"""
    try:
        await PIPELINE_RUNNER.regen_tts_scene(job_id, scene_id)
    except KeyError as e:
        raise HTTPException(404, _exc_msg(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "job_id": job_id, "scene_id": scene_id}


@router.post("/jobs/{job_id}/shenkuo/refresh")
async def refresh_shenkuo(job_id: str) -> dict[str, Any]:
    """进画布触发：后台刷新沈括已采作品的播放数据 + 评论（仅这两项，其余产物不动）。

    立即返回（fire-and-forget）；逐条作品 1 小时内只采一次（Redis 节流锁）省 API 成本。
    refreshing=False 表示没起刷新（未采过 / 正在采 / 已有刷新在跑），属正常 no-op。
    """
    try:
        refreshing = PIPELINE_RUNNER.refresh_shenkuo(job_id)
    except KeyError as e:
        raise HTTPException(404, _exc_msg(e))
    return {"ok": True, "job_id": job_id, "refreshing": refreshing}


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
        raise HTTPException(404, _exc_msg(e))
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
# 鬼谷子选题（评论驱动双模型）—— virtual agent，结果落 per-job guiguzi.json，前端轮询
# ---------------------------------------------------------------------------
@router.post("/jobs/{job_id}/guiguzi/analyze")
async def analyze_guiguzi(job_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """第一步：双模型反推爆款原因。立即返回 analyzing，后台跑，前端轮询取 analyzed。

    body: {"items": [{text, comment}, ...](≤5)}。
    """
    items = body.get("items") if isinstance(body, dict) else None
    try:
        doc = PIPELINE_RUNNER.analyze_guiguzi(job_id, items or [])
    except KeyError as e:
        raise HTTPException(404, _exc_msg(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return doc


@router.post("/jobs/{job_id}/guiguzi/topics")
async def generate_guiguzi(job_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """第二步：以(用户选定/编辑的)分析为指导出选题。立即返回 generating，后台跑。

    body: {"items": [{text, comment}, ...], "analysis": {...}, "prompt": str?, "force": bool}。
    prompt 传入(用户编辑后的提示词,含 $source 占位)则用它且全量重出;否则按 analysis 拼默认模板。
    force=False（默认·增量）：只为新评论补题，保留已出题评论；force=True（重新选题）：全部重出。
    """
    items = body.get("items") if isinstance(body, dict) else None
    analysis = body.get("analysis") if isinstance(body, dict) else None
    prompt = body.get("prompt") if isinstance(body, dict) else None
    force = bool(body.get("force")) if isinstance(body, dict) else False
    try:
        doc = PIPELINE_RUNNER.generate_guiguzi(job_id, items or [], analysis, prompt=prompt, force=force)
    except KeyError as e:
        raise HTTPException(404, _exc_msg(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return doc


@router.get("/jobs/{job_id}/guiguzi")
async def get_guiguzi(job_id: str) -> dict[str, Any]:
    """读 per-job 选题结果（running/done/failed）。前端轮询取双栏 candidates。"""
    try:
        PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")
    doc = PIPELINE_RUNNER.get_guiguzi(job_id)
    if doc is None:
        raise HTTPException(404, "guiguzi not run yet")
    return doc


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

# tail 轮询间隔（秒）：与 tasks.py 保持一致
_TAIL_POLL_INTERVAL = 0.5


@router.get("/jobs/{job_id}/events")
async def stream_events(job_id: str, since_seq: int | None = None) -> EventSourceResponse:
    """SSE：订阅 job 的节点状态变更事件。

    协议
    ----
    每条事件 data 字段是一行 JSON：
        {"type": "snapshot",    "job_id": "...", "state": {...}}  -- 首帧全量
        {"type": "node_status", "job_id": "...", "node": "asr", "state": {...}}
        {"type": "job_updated", "job_id": "...", "state": {...}}
    无终止信号；前端按需 close 连接。

    断线重连
    --------
    带 ?since_seq=N 时：发完 snapshot 后，从 events.jsonl 重放 seq>N 的历史事件，
    再继续 tail 新增——保证断线期间不丢事件。
    不带时（默认）：snapshot 全量 + 之后的增量，不重放历史，等价于旧行为。
    """
    try:
        PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")

    events_path = PIPELINE_RUNNER._events_file(job_id)

    async def gen() -> AsyncGenerator[dict, None]:
        # 修复竞态（缺口1）：必须先锁定 tail_start_pos（= 读 snapshot 之前文件的末位）
        # 再 yield snapshot，再 yield replay，才能保证竞态窗口内的事件：
        #   - 要么已被 snapshot 覆盖（snapshot 的 _load 在 tail_start_pos 对应的时刻之后）
        #   - 要么被 tail 重发（tail 从 tail_start_pos 开始）
        # 旧顺序（snapshot → 记录 offset）有真丢窗口：事件在 get_job 之后、tell() 之前被 _emit
        # → 不在 snapshot 里、又因 offset 记在它之后不被 tail 读到。

        # 1) 先锁定 tail_start_pos + 收集 replay_lines（since_seq 模式）
        replay_lines: list[str] = []
        tail_start_pos = 0

        if events_path.is_file():
            try:
                with events_path.open("r", encoding="utf-8") as fh:
                    if since_seq is not None:
                        # 重放 seq > since_seq 的历史事件
                        for raw in fh:
                            raw = raw.rstrip("\n")
                            if not raw:
                                continue
                            try:
                                obj = json.loads(raw)
                                if int(obj.get("seq") or 0) > since_seq:
                                    replay_lines.append(raw)
                            except (json.JSONDecodeError, ValueError):
                                pass
                    tail_start_pos = fh.tell()
            except OSError:
                tail_start_pos = 0
        else:
            # 文件不存在时：用 stat().st_size 等价 0，保持安全
            tail_start_pos = 0

        # 2) 首帧：全量 snapshot（保持前端契约）
        #    get_job 在 tail_start_pos 锁定之后读取，反映的状态 >= 对应时刻
        snapshot = PIPELINE_RUNNER.get_job(job_id)
        snapshot_ts = time.time()
        yield {"data": _json_dumps({
            "type": "snapshot",
            "job_id": job_id,
            "state": _serialize_job(snapshot),
            "ts": snapshot_ts,
        })}

        # 3) 重放断线历史（since_seq 模式）
        for raw in replay_lines:
            yield {"data": raw}

        # 4) 持续 tail events.jsonl 新增内容，直到客户端断开
        last_pos = tail_start_pos
        try:
            while True:
                await asyncio.sleep(_TAIL_POLL_INTERVAL)
                if not events_path.is_file():
                    continue
                try:
                    size = events_path.stat().st_size
                except OSError:
                    continue
                if size > last_pos:
                    with events_path.open("r", encoding="utf-8") as fh:
                        fh.seek(last_pos)
                        for raw in fh:
                            raw = raw.rstrip("\n")
                            if raw:
                                yield {"data": raw}
                        last_pos = fh.tell()
        except asyncio.CancelledError:
            # 客户端断开，正常退出；无需发 [DONE]（pipeline 无终态信号，前端按需 close）
            raise

    return EventSourceResponse(gen())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_job(state: Any) -> dict[str, Any]:
    """JobState dataclass → dict（asdict）+ 字段加点 UI 关心的衍生信息。"""
    d = asdict(state)
    return d


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
