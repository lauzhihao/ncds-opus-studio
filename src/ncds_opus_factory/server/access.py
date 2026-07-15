"""路由层资源访问：加载 job/task/instance 并做 owner 校验 / 无主 claim。"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ncds_opus_factory.server.engine.types import InstanceState
from ncds_opus_factory.server.pipeline_models import JobState
from ncds_opus_factory.server.schemas import TaskMeta
from ncds_opus_factory.server.state import (
    AUTH_CONFIG,
    INSTANCE_STORE,
    PIPELINE_RUNNER,
    STORE,
)
from ncds_opus_factory.server.tenancy import assert_owner, request_owner_id


def current_owner_id(request: Request) -> str | None:
    return request_owner_id(request, AUTH_CONFIG)


def maybe_claim_legacy(request: Request) -> str | None:
    """返回当前 owner_id；auth 关返回 None。

    仅当 auth_users 只有 1 人时，把无主 job/task/instance 全量 claim 给该用户
   （单租户上线迁移）。多用户时绝不抢无主资源，避免串户。
    """
    me = current_owner_id(request)
    if me is None:
        return None
    from ncds_opus_factory.server.state import AUTH_STORE

    if AUTH_STORE.count_users() != 1:
        return me
    try:
        PIPELINE_RUNNER.claim_unowned_jobs(me)
    except Exception:  # noqa: BLE001
        pass
    try:
        STORE.claim_unowned_tasks(me)
    except Exception:  # noqa: BLE001
        pass
    try:
        INSTANCE_STORE.claim_unowned(me)
    except Exception:  # noqa: BLE001
        pass
    return me


def require_job(job_id: str, request: Request) -> JobState:
    """加载 job；auth 开时校验归属，无主则 claim 给当前用户。"""
    me = current_owner_id(request)
    try:
        state = PIPELINE_RUNNER.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}") from exc
    claimed = assert_owner(state.owner_id, me=me, claimable=True)
    if me is not None and state.owner_id is None and claimed == me:
        state = PIPELINE_RUNNER.set_job_owner(job_id, me)
    return state


def require_task(task_id: str, request: Request) -> TaskMeta:
    me = current_owner_id(request)
    meta = STORE.get_meta(task_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    claimed = assert_owner(meta.owner_id, me=me, claimable=True)
    if me is not None and meta.owner_id is None and claimed == me:
        meta = STORE.set_task_owner(task_id, me)
    return meta


def require_instance(iid: str, request: Request) -> InstanceState:
    me = current_owner_id(request)
    state = INSTANCE_STORE.get_state(iid)
    if state is None:
        raise HTTPException(status_code=404, detail=f"instance not found: {iid}")
    claimed = assert_owner(state.meta.owner_id, me=me, claimable=True)
    if me is not None and state.meta.owner_id is None and claimed == me:
        INSTANCE_STORE.set_owner(iid, me)
        state = INSTANCE_STORE.get_state(iid)
        if state is None:
            raise HTTPException(status_code=404, detail=f"instance not found: {iid}")
    return state
