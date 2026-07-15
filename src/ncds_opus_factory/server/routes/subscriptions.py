"""订阅管理端点（订阅传感器的配置面）。

- GET /subscriptions          读当前订阅配置
- PUT /subscriptions          整体覆盖写（幂等;循环热读,改完即生效）
- POST /subscriptions/tick    手动触发一轮派发（调试/演示用,不等下个周期）
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ncds_opus_factory.common import authors_repo
from ncds_opus_factory.server.access import current_owner_id
from ncds_opus_factory.server.state import RUNNER, STATE_DIR, STORE
from ncds_opus_factory.server.subscriptions import (
    ensure_user_subscriptions,
    iter_subscription_paths,
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
    # 平台：douyin（默认）/ tiktok / youtube
    platform: str = "douyin"
    # 领域 profile（见 domain_profiles.py）；决定后续选题/撰稿提示词
    domain: str | None = None
    # 每账号更新频率(小时)；None=用全局 interval_hours
    interval_hours: float | None = None
    # 展示快照：新增时由 /accounts/resolve 结果带入，卡片直接显示 + 复用（避免重复打 TikHub）
    nickname: str | None = None
    avatar: str | None = None
    unique_id: str | None = None
    follower_count: int | None = None
    like_count: int | None = None
    works_count: int | None = None
    # 我方最后一次拉取该账号档案的 unix 秒（用于卡片"最近更新 X前"）
    refreshed_at: float | None = None


class SubscriptionsConfig(BaseModel):
    interval_hours: float = Field(default=3.0, gt=0)
    authors: list[SubscriptionAuthor] = Field(default_factory=list)


def _hydrate(cfg: dict[str, Any]) -> dict[str, Any]:
    """关注名单只存引用(sec_uid/platform/unique_id + 订阅设置)，展示时去作者库取名片
    (昵称/头像/粉丝数/refreshed_at)拼回老形状 -> 前端零改动。库里没有就只回引用本身。"""
    out: list[dict[str, Any]] = []
    for ref in cfg.get("authors", []):
        platform = ref.get("platform", "douyin")
        key = authors_repo.author_key(platform, ref.get("sec_uid", ""), ref.get("unique_id", ""))
        prof = authors_repo.load_profile(platform, key) or {}
        merged = dict(ref)
        for k in ("nickname", "avatar", "unique_id", "follower_count",
                  "like_count", "works_count", "refreshed_at"):
            v = prof.get(k)
            if v is not None:
                merged[k] = v
        out.append(merged)
    return {"interval_hours": cfg.get("interval_hours", 3.0), "authors": out}


def _upsert_author_snapshot(a: SubscriptionAuthor) -> None:
    """PUT 携带的展示快照 upsert 进作者库；仅当不比库里更旧时才写
    (防前端把 GET 来的旧快照回放、覆盖 worker 刚刷新的新值)。无快照则跳过。"""
    display: dict[str, Any] = {}
    if a.nickname:
        display["nickname"] = a.nickname
    if a.avatar:
        display["avatar"] = a.avatar
    for field, v in (("follower_count", a.follower_count), ("like_count", a.like_count),
                     ("works_count", a.works_count)):
        if v is not None:
            display[field] = int(v)
    if not display:
        return
    key = authors_repo.author_key(a.platform, a.sec_uid, a.unique_id or "")
    if not key:
        return
    existing = authors_repo.load_profile(a.platform, key)
    if existing is not None and float(a.refreshed_at or 0.0) < float(existing.get("refreshed_at") or 0.0):
        return  # 库里更新，别用前端回放的旧快照覆盖
    display["sec_uid"] = a.sec_uid
    if a.unique_id:
        display["unique_id"] = a.unique_id
    authors_repo.save_profile(a.platform, key, display, refreshed_at=a.refreshed_at)


def _subs_path_for_request(request: Request):
    from pathlib import Path

    owner_id = current_owner_id(request)
    if owner_id is not None:
        return ensure_user_subscriptions(STATE_DIR, owner_id)
    return subscriptions_path(STATE_DIR)


@router.get("/subscriptions", response_model=SubscriptionsConfig)
async def get_subscriptions(request: Request, domain: str | None = None) -> dict[str, Any]:
    cfg = _hydrate(load_subscriptions(_subs_path_for_request(request)))
    if domain:
        cfg["authors"] = [a for a in cfg["authors"] if a.get("domain") == domain]
    return cfg


@router.put("/subscriptions", response_model=SubscriptionsConfig)
async def put_subscriptions(body: SubscriptionsConfig, request: Request) -> dict[str, Any]:
    path = _subs_path_for_request(request)
    seen: set[str] = set()
    refs: list[dict[str, Any]] = []
    for a in body.authors:
        a.sec_uid = a.sec_uid.strip()
        a.platform = (a.platform or "douyin").strip().lower() or "douyin"
        if not a.sec_uid:
            raise HTTPException(status_code=422, detail="sec_uid 不能为空")
        key = f"{a.platform}:{authors_repo.author_key(a.platform, a.sec_uid, a.unique_id or '')}"
        if key in seen:
            raise HTTPException(status_code=422, detail=f"重复账号: {key}")
        seen.add(key)
        _upsert_author_snapshot(a)
        # 名单只存瘦引用：身份(sec_uid/platform/unique_id) + 订阅设置(note/enabled/频率/领域)
        ref: dict[str, Any] = {
            "sec_uid": a.sec_uid,
            "note": a.note,
            "enabled": a.enabled,
            "platform": a.platform,
            "interval_hours": a.interval_hours,
        }
        if a.domain:
            ref["domain"] = a.domain
        if a.unique_id:
            ref["unique_id"] = a.unique_id
        refs.append(ref)
    cfg = {"interval_hours": body.interval_hours, "authors": refs}
    save_subscriptions(path, cfg)
    logger.info("[subscriptions] 配置更新: %d 个作者 path=%s", len(refs), path)
    return _hydrate(load_subscriptions(path))


@router.post("/subscriptions/tick")
async def trigger_tick(request: Request) -> dict[str, int]:
    """手动触发：只 tick 当前用户的订阅（auth 关则 tick legacy 全局文件）。"""
    owner_id = current_owner_id(request)
    if owner_id is not None:
        path = ensure_user_subscriptions(STATE_DIR, owner_id)
        n = await run_subscription_tick(RUNNER, STORE, path, owner_id=owner_id)
        return {"submitted": n}
    n = await run_subscription_tick(RUNNER, STORE, subscriptions_path(STATE_DIR), owner_id=None)
    return {"submitted": n}
