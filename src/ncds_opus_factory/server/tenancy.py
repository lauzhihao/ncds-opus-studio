"""多租户：owner_id 约定 + 路由层隔离（对齐 PRODUCTION-ENGINE-DESIGN §6）。

owner_id 形态：``str(auth_users.id)``（稳定整数串）。Auth 关闭时 owner_id=None，不隔离。
隔离在路由校验，store 只负责按 owner_id 过滤/落盘。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from ncds_opus_factory.server.auth import AuthConfig
from ncds_opus_factory.server.auth_store import AuthUserRecord


def owner_id_of(user: AuthUserRecord) -> str:
    return str(user.id)


def request_auth_user(request: Request) -> AuthUserRecord | None:
    return getattr(request.state, "auth_user", None)


def request_owner_id(request: Request, config: AuthConfig) -> str | None:
    """Auth 关 → None（不隔离）。Auth 开 → 当前用户 owner_id（中间件已 401 未登录）。"""
    if not config.enabled:
        return None
    user = request_auth_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return owner_id_of(user)


def assert_owner(
    resource_owner_id: str | None,
    *,
    me: str | None,
    claimable: bool = False,
) -> str | None:
    """校验资源归属。

    - me is None（auth 关）：放行，返回 resource_owner_id
    - resource 属于 me：放行
    - resource_owner_id is None 且 claimable：调用方应写入 me（迁移无主数据）
    - 否则：404（不暴露存在性）
    """
    if me is None:
        return resource_owner_id
    if resource_owner_id == me:
        return resource_owner_id
    if resource_owner_id is None and claimable:
        return me
    raise HTTPException(status_code=404, detail="not found")


def owner_matches(resource_owner_id: str | None, me: str | None) -> bool:
    """列表过滤：auth 关全放；auth 开只放自己的（含尚未 claim 的无主，由 claim 流程处理）。"""
    if me is None:
        return True
    return resource_owner_id is None or resource_owner_id == me


def resource_visible(resource_owner_id: str | None, me: str | None) -> bool:
    """严格可见：auth 关全可见；auth 开仅 owner==me（无主不算可见，需先 claim）。"""
    if me is None:
        return True
    return resource_owner_id == me


def as_owner_meta(owner_id: str | None) -> dict[str, Any]:
    return {"owner_id": owner_id}
