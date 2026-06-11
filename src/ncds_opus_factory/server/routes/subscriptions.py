"""订阅管理端点（订阅传感器的配置面）。

- GET /subscriptions          读当前订阅配置
- PUT /subscriptions          整体覆盖写（幂等;循环热读,改完即生效）
- POST /subscriptions/tick    手动触发一轮派发（调试/演示用,不等下个周期）
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ncds_opus_factory.server.state import RUNNER, STATE_DIR, STORE
from ncds_opus_factory.server.subscriptions import (
    load_subscriptions,
    run_subscription_tick,
    save_subscriptions,
    subscriptions_path,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class SubscriptionAuthor(BaseModel):
    sec_uid: str
    note: str | None = None
    enabled: bool = True


class SubscriptionsConfig(BaseModel):
    interval_hours: float = Field(default=2.0, gt=0)
    authors: list[SubscriptionAuthor] = Field(default_factory=list)


@router.get("/subscriptions", response_model=SubscriptionsConfig)
async def get_subscriptions() -> dict[str, Any]:
    return load_subscriptions(subscriptions_path(STATE_DIR))


@router.put("/subscriptions", response_model=SubscriptionsConfig)
async def put_subscriptions(body: SubscriptionsConfig) -> dict[str, Any]:
    seen: set[str] = set()
    for a in body.authors:
        a.sec_uid = a.sec_uid.strip()
        if not a.sec_uid:
            raise HTTPException(status_code=422, detail="sec_uid 不能为空")
        if a.sec_uid in seen:
            raise HTTPException(status_code=422, detail=f"重复 sec_uid: {a.sec_uid}")
        seen.add(a.sec_uid)
    cfg = body.model_dump()
    save_subscriptions(subscriptions_path(STATE_DIR), cfg)
    logger.info("[subscriptions] 配置更新: %d 个作者", len(body.authors))
    return cfg


@router.post("/subscriptions/tick")
async def trigger_tick() -> dict[str, int]:
    n = await run_subscription_tick(RUNNER, STORE, subscriptions_path(STATE_DIR))
    return {"submitted": n}
