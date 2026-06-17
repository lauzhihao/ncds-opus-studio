"""单作品解析（临时任务"智能解析"）。

POST /works/resolve：粘贴抖音作品分享链接/口令 → 解析出 platform+aweme_id →
先查本地缓存(查过就不打 TikHub) → 没查过的用 `platform_aweme_id` 做锁打 TikHub
取作品详情。返回作品卡：封面/标题/话题/四项互动数据 + 作者档案(「关注ta」加入对标用)。

只读 + 缓存写：作品卡落作品仓库 state/works/{platform}/{aweme_id}/manifest.json 的
card 分区（与沈括采集的 products/status 分区共存于同一 manifest），不碰订阅/任务系统。
TikTok 作品解析暂未接入(与订阅采集边界一致)，统一按抖音处理。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ncds_opus_factory.common import tikhub_client, works_repo
from ncds_opus_factory.server.domain_profiles import DOMAIN_PROFILES

logger = logging.getLogger(__name__)

router = APIRouter()

# per-key 锁：同一作品(platform_aweme_id)并发解析只打一次 TikHub。
# 模块级 dict + 一把守护锁保护 dict 自身的读改写(端点是 sync def,走线程池,用 threading)。
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def _key_lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = _LOCKS[key] = threading.Lock()
        return lk


class ResolveWorkBody(BaseModel):
    text: str


class SaveDomainBody(BaseModel):
    domain: str  # 赛道 key，必须是已知领域（finance/emotion）


@router.patch("/works/{platform}/{aweme_id}/domain")
def set_work_domain(platform: str, aweme_id: str, body: SaveDomainBody) -> dict:
    """把用户手选的赛道 key 写回作品 manifest（task-2.3 前端临时作品选赛道）。

    只接受 DOMAIN_PROFILES 里已知的 key（finance/emotion），未知 key 返回 422。
    未知 platform 不做防御性拒绝（沿用 works_repo 自动建目录的容错策略）。
    空串的情况由 Pydantic str 类型本身不接受空触发 422；save_domain 内部也做了双重保护。
    """
    domain = body.domain.strip()
    if domain not in DOMAIN_PROFILES:
        known = ", ".join(sorted(DOMAIN_PROFILES.keys()))
        raise HTTPException(422, f"未知赛道 '{domain}'，已知赛道：{known}")
    works_repo.save_domain(platform, aweme_id, domain)
    return {"ok": True, "platform": platform, "aweme_id": aweme_id, "domain": domain}


@router.post("/works/resolve")
def resolve_work(body: ResolveWorkBody) -> dict[str, Any]:
    """抖音作品分享链接/口令 → 作品卡（封面/标题/话题/四项数据/作者档案）。

    查过的作品(平台+作品id)直接回缓存、不打 TikHub；没查过的用 key 做锁串行化，
    锁内 double-check 缓存后再调 TikHub 取详情并落盘(防并发重复打)。
    sync def（FastAPI 走线程池）—— 跟随短链重定向 + 拉详情是阻塞 IO。
    """
    aweme_id = tikhub_client.resolve_aweme_id(body.text)
    if not aweme_id:
        raise HTTPException(422, "无法从内容里解析出作品，请粘贴抖音作品分享链接或口令")
    platform = "douyin"  # TikTok 作品解析暂未接入(与订阅采集边界一致)
    key = f"{platform}_{aweme_id}"

    # 命中缓存：查过就不打 TikHub（读 manifest 的 card 分区）
    cached = works_repo.load_card(platform, aweme_id)
    if cached is not None:
        # 顺带带上已存的 domain（继承来的或之前手选的），前端展示/预选用
        domain = works_repo.load_domain(platform, aweme_id)
        return {**cached, "cached": True, "domain": domain}

    # 未命中：用 key 锁串行化，锁内 double-check（等锁期间别人可能刚写好缓存）
    with _key_lock(key):
        cached = works_repo.load_card(platform, aweme_id)
        if cached is not None:
            domain = works_repo.load_domain(platform, aweme_id)
            return {**cached, "cached": True, "domain": domain}
        try:
            detail = tikhub_client.fetch_one_video_detail(aweme_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[works] 解析作品失败 %s: %s", aweme_id, exc)
            raise HTTPException(422, "解析作品失败，请确认链接有效或稍后重试") from exc
        if not detail:
            raise HTTPException(422, "无法获取该作品资料")
        result: dict[str, Any] = {
            "platform": platform,
            "aweme_id": aweme_id,
            "share_url": f"https://www.douyin.com/video/{aweme_id}",
            **tikhub_client.extract_work_card(detail),
        }
        works_repo.save_card(platform, aweme_id, result)
        # 新作品首次解析：manifest 里还没 domain，返回 None（前端用 DEFAULT_DOMAIN 兜底显示）
        return {**result, "cached": False, "domain": None}
