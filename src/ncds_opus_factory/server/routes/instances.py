"""生产实例（instance）= 一条 recipe 的一次执行。引擎实现见 server/engine/。

当前入口事实地图（task-3.1，2026-06-22）：

- web `/studio` 当前活路径仍是 `/jobs/*` + `/pipelines` + `/preview/*`；
  `PipelineRunner` 保存 `JobState`，默认把 lines/storyboard/tts/image/render 经 facade
  间接委托给 `InstanceRunner.run_step()`。web 前端并没有直接调用本 `/instances` 路由。
- web 的 asr 节点仍固定走 `PipelineRunner._execute_asr_collect` legacy 采集路径，因为它依赖
  步内增量 outputs（collected/item_progress）与 done 后后台 enrich。
- web 的 rw 节点仍固定走 `PipelineRunner._execute_rw` legacy 改写路径，因为柳永抽屉依赖逐模型
  实时 `model_progress/drafts` patch。
- app 当前活路径仍是 `/tasks/*` + `/commands` + `/artifacts/*`；
  `TaskRunner` 入队，`nof-worker` 唯一执行。app 前端尚未迁到 `/instances`。
- `/instances` 是已落地的 engine driver API，当前主要服务后端测试、内部迁移和未来 web/app
  统一入口；不要把它描述成已经接管 web/app 的前端主路径。

E1-b1：把引擎 driver API 暴露为 HTTP（纯增量，新增 `/instances` 路由，不动 /jobs /tasks /pipelines）。

- GET  /instances                              列实例（meta，倒序）+ owner/status/recipe 过滤
- POST /instances                              建实例（body: recipe_id/inputs/...）→ 201 + Location
- GET  /instances/{iid}                        全量状态（meta + 各步 + 输入）
- GET  /instances/{iid}/runnable               当前可跑步（idle 且 deps 全 done/skipped）
- GET  /instances/{iid}/events?level=meta,step SSE（订内存总线；分层）
- POST /instances/{iid}/steps/{sid}/run        跑一步（body: step_inputs/config）
- POST /instances/{iid}/steps/{sid}/approve    awaiting_review 出口（定稿/打回）
- POST /instances/{iid}/steps/{sid}/reset      硬重置该步+传递下游为 idle
- POST /instances/{iid}/finalize               据各步终态结算 meta

b1 范围取舍（b2 web 迁移时补）：
- ``/run`` **同步 await**：阻塞到该步落定（done/awaiting_review/failed）。贵步骤的后台派发
  + 进度流由 b2 接 web 画布时加。
- SSE 只推 **meta/step**（来自内存总线）；``detail``(progress) 仅落各步 events.jsonl、由工作线程写、
  不进 asyncio 总线（非线程安全），其 tail-merge 流随 web 画布在 b2 补。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ncds_opus_factory.server.access import current_owner_id, maybe_claim_legacy, require_instance
from ncds_opus_factory.server.engine.types import InstanceMeta, InstanceState, StepState
from ncds_opus_factory.server.state import INSTANCE_RUNNER, INSTANCE_STORE

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# 请求体（响应直接复用 engine/types 的 InstanceState/StepState/InstanceMeta）
# ---------------------------------------------------------------------------
class InstanceCreateRequest(BaseModel):
    recipe_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    title: str = ""
    owner_id: str | None = None        # 多租户预留（演示 None；生产由鉴权中间件填）
    source: str | None = None          # user/wolong/gate/cron/retro
    driver: Literal["manual", "wolong"] = "manual"
    round_id: str | None = None
    parent_instance_id: str | None = None
    intent_key: str | None = None


class InstanceCreateResponse(BaseModel):
    instance_id: str
    status: str


class StepRunRequest(BaseModel):
    step_inputs: dict[str, Any] = Field(default_factory=dict)   # driver 装配的上游产物/输入
    config: dict[str, Any] = Field(default_factory=dict)        # 运行期选择（模型/质量；溯源）


class StepApproveRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    edited_draft: dict[str, Any] | None = None                  # content_edit：人改的稿
    note: str | None = None
    reviewer: Literal["user", "wolong", "system"] = "user"


# ---------------------------------------------------------------------------
# 实例 CRUD
# ---------------------------------------------------------------------------
@router.get("/instances", response_model=list[InstanceMeta])
async def list_instances(
    request: Request,
    status: str | None = None,
    recipe_id: str | None = None,
) -> list[InstanceMeta]:
    """列实例 meta（最新在前）。强制按当前登录用户过滤；auth 关则不过滤。"""
    owner_id = maybe_claim_legacy(request)
    metas = INSTANCE_STORE.list_instances(owner_id=owner_id)
    # claim 后仍可能有其它用户的无主被本用户 claim 完；过滤严格可见
    if owner_id is not None:
        metas = [m for m in metas if m.owner_id == owner_id]
    if status:
        metas = [m for m in metas if m.status == status]
    if recipe_id:
        metas = [m for m in metas if m.recipe_id == recipe_id]
    return metas


@router.post("/instances", response_model=InstanceCreateResponse, status_code=201)
async def create_instance(
    body: InstanceCreateRequest,
    request: Request,
    response: Response,
) -> InstanceCreateResponse:
    """建一条生产实例（按 recipe 初始化各步为 idle），返回 instance_id。"""
    # 客户端不可伪造 owner：auth 开时强制当前用户
    owner_id = current_owner_id(request)
    if owner_id is None:
        owner_id = body.owner_id  # auth 关时允许演示传入
    try:
        state = INSTANCE_RUNNER.create_instance(
            body.recipe_id,
            body.inputs,
            owner_id=owner_id,
            source=body.source,
            driver=body.driver,
            round_id=body.round_id,
            parent_instance_id=body.parent_instance_id,
            intent_key=body.intent_key,
            title=body.title,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown recipe_id: {body.recipe_id}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid recipe: {exc}")
    iid = state.meta.instance_id
    response.headers["Location"] = f"/instances/{iid}"
    logger.info("[server] instance created: recipe=%s iid=%s driver=%s owner=%s",
                body.recipe_id, iid, body.driver, owner_id)
    return InstanceCreateResponse(instance_id=iid, status=state.meta.status)


@router.get("/instances/{iid}", response_model=InstanceState)
async def get_instance(iid: str, request: Request) -> InstanceState:
    """全量实例状态（meta + 各步 state + 输入）。"""
    return require_instance(iid, request)


@router.get("/instances/{iid}/runnable")
async def get_runnable(iid: str, request: Request) -> dict:
    """当前可跑步（idle 且 deps 全 done/skipped），拓扑序。driver 据此选下一步。"""
    require_instance(iid, request)
    try:
        return {"runnable": INSTANCE_RUNNER.get_runnable_steps(iid)}
    except (KeyError, FileNotFoundError):
        # KeyError：实例在盘但其 recipe_id 已不在注册表（版本漂移）→ 同 404，与 run/reset 一致
        raise HTTPException(status_code=404, detail=f"instance not found: {iid}")


# ---------------------------------------------------------------------------
# 步骤推进（driver 原语）
# ---------------------------------------------------------------------------
@router.post("/instances/{iid}/steps/{sid}/run", response_model=StepState)
async def run_step(
    iid: str,
    sid: str,
    request: Request,
    body: StepRunRequest | None = None,
) -> StepState:
    require_instance(iid, request)
    """跑一步（**同步 await**，阻塞到落定）。404=实例/步不存在；409=该步当前不可跑（须先 reset）。

    body 全可选（step_inputs/config 都缺省空）；无 body 的 POST 也合法（如直通步），按缺省跑。
    """
    body = body or StepRunRequest()
    try:
        return await INSTANCE_RUNNER.run_step(iid, sid, body.step_inputs, body.config)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/instances/{iid}/steps/{sid}/approve", response_model=StepState)
async def approve_step(iid: str, sid: str, request: Request, body: StepApproveRequest) -> StepState:
    require_instance(iid, request)
    """awaiting_review 出口：approved→定稿 / rejected→打回。409=该步未在等审。"""
    try:
        return await INSTANCE_RUNNER.approve_step(
            iid, sid, body.decision,
            edited_draft=body.edited_draft, note=body.note, reviewer=body.reviewer,
        )
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/instances/{iid}/steps/{sid}/reset")
async def reset_step(iid: str, sid: str, request: Request) -> dict:
    require_instance(iid, request)
    """硬重置该步 + 全部传递下游为 idle（改稿后下游失效）。409=步骤运行中不可重置。"""
    try:
        return {"reset": await INSTANCE_RUNNER.reset_step(iid, sid)}
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/instances/{iid}/finalize", response_model=InstanceMeta)
async def finalize_instance(iid: str, request: Request) -> InstanceMeta:
    require_instance(iid, request)
    """据各步终态结算 meta（failed/completed/pending/running）。"""
    try:
        return await INSTANCE_RUNNER.finalize_instance(iid)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------------------------
# 分层 SSE（b1：meta/step；detail 见模块 docstring）
# ---------------------------------------------------------------------------
def _parse_levels(level: str | None) -> set[str]:
    if not level:
        return {"meta", "step"}     # 默认决策级（app 视图）
    return {p.strip() for p in level.split(",") if p.strip()}


@router.get("/instances/{iid}/events")
async def stream_events(iid: str, request: Request, level: str | None = None) -> EventSourceResponse:
    require_instance(iid, request)
    """SSE：先推一条 snapshot（全量 state），再按 level 订阅内存总线增量推送。

    ``level=meta,step,detail`` 可指定关心的层级；b1 总线只承载 meta/step，detail 暂不流（见 docstring）。
    无终止信号；前端按需 close。
    """
    if not INSTANCE_STORE.exists(iid):
        raise HTTPException(status_code=404, detail=f"instance not found: {iid}")
    levels = _parse_levels(level)
    queue = INSTANCE_RUNNER.bus.subscribe(iid, levels)

    async def gen() -> AsyncGenerator[dict, None]:
        # snapshot 的 yield 必须在 try 内：客户端常在收完 snapshot、还没等到首个增量事件时就断开，
        # 若 yield 在 try 外，GeneratorExit 落在此处会跳过 finally → 漏 unsubscribe、总线订阅泄漏。
        try:
            snap = INSTANCE_STORE.get_state(iid)
            if snap is not None:
                yield {"data": json.dumps(
                    {"type": "snapshot", "instance_id": iid, "state": snap.model_dump(mode="json")},
                    ensure_ascii=False,
                )}
            while True:
                event = await queue.get()
                yield {"data": event.model_dump_json(exclude_none=True)}
        except asyncio.CancelledError:
            raise
        finally:
            INSTANCE_RUNNER.bus.unsubscribe(iid, queue)

    return EventSourceResponse(gen())
