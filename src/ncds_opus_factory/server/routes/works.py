"""单作品解析（临时任务"智能解析"）。

POST /works/resolve：粘贴抖音/TikTok/YouTube 单作品分享链接/口令 →
解析出 platform+aweme_id → 先查本地缓存 → 没查过的用 `platform_aweme_id` 做锁。
抖音走 TikHub 取完整作品详情；TikTok/YouTube 用 yt-dlp 取单作品 metadata（失败降级轻量卡片）。

只读 + 缓存写：作品卡落作品仓库 state/works/{platform}/{aweme_id}/manifest.json 的
card 分区（与沈括采集的 products/status 分区共存于同一 manifest），不碰订阅/任务系统。
"""

from __future__ import annotations

import logging
import re
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


def _needs_non_douyin_metadata_refresh(platform: str, card: dict[str, Any]) -> bool:
    """旧缓存里的 TikTok/YouTube 轻量卡没有封面/互动数据，允许重新补 metadata。"""
    if platform == "douyin":
        return False
    if card.get("metadata_source") != "yt_dlp":
        return True
    author = card.get("author") if isinstance(card.get("author"), dict) else {}
    nickname = str(author.get("nickname") or "").strip()
    return platform == "tiktok" and bool(re.fullmatch(r"\d{8,}", nickname))


def _non_douyin_work_card(ref: tikhub_client.VideoRef) -> dict[str, Any]:
    try:
        meta = tikhub_client.fetch_video_ref_meta(ref)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[works] 非抖音作品 metadata 获取失败 %s:%s: %s", ref.platform, ref.video_id, exc)
        meta = tikhub_client.video_ref_meta(ref)
    return tikhub_client.video_ref_work_card(ref, meta=meta)


class ResolveWorkBody(BaseModel):
    text: str


class SaveDomainBody(BaseModel):
    domain: str  # 赛道 key，必须是 DOMAIN_PROFILES 里的已知领域


@router.patch("/works/{platform}/{aweme_id}/domain")
def set_work_domain(platform: str, aweme_id: str, body: SaveDomainBody) -> dict:
    """把用户手选的赛道 key 写回作品 manifest（task-2.3 前端临时作品选赛道）。

    只接受 DOMAIN_PROFILES 里已知的 key，未知 key 返回 422。
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
    """作品分享链接/口令 → 作品卡（封面/标题/话题/四项数据/作者档案）。

    查过的作品(平台+作品id)直接回缓存；没查过的用 key 做锁串行化，
    锁内 double-check 缓存后再解析详情并落盘(防并发重复打)。
    sync def（FastAPI 走线程池）—— 跟随短链重定向 + 拉详情是阻塞 IO。
    """
    ref = tikhub_client.resolve_video_ref(body.text)
    if not ref:
        raise HTTPException(422, "无法从内容里解析出作品，请粘贴抖音/TikTok/YouTube 单作品链接")
    platform = ref.platform
    aweme_id = ref.video_id
    key = f"{platform}_{aweme_id}"

    # 命中缓存：已补过 metadata 的直接回；旧轻量卡允许进锁内刷新一次。
    cached = works_repo.load_card(platform, aweme_id)
    if cached is not None and not _needs_non_douyin_metadata_refresh(platform, cached):
        # 顺带带上已存的 domain（继承来的或之前手选的），前端展示/预选用
        domain = works_repo.load_domain(platform, aweme_id)
        return {**cached, "cached": True, "domain": domain}

    # 未命中：用 key 锁串行化，锁内 double-check（等锁期间别人可能刚写好缓存）
    with _key_lock(key):
        cached = works_repo.load_card(platform, aweme_id)
        if cached is not None and not _needs_non_douyin_metadata_refresh(platform, cached):
            domain = works_repo.load_domain(platform, aweme_id)
            return {**cached, "cached": True, "domain": domain}
        was_cached = cached is not None
        if platform == "douyin":
            try:
                detail = tikhub_client.fetch_one_video_detail(aweme_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[works] 解析作品失败 %s:%s: %s", platform, aweme_id, exc)
                raise HTTPException(422, "解析作品失败，请确认链接有效或稍后重试") from exc
            if not detail:
                raise HTTPException(422, "无法获取该作品资料")
            card = tikhub_client.extract_work_card(detail)
        else:
            card = _non_douyin_work_card(ref)
        result: dict[str, Any] = {
            "platform": platform,
            "aweme_id": aweme_id,
            "share_url": ref.url,
            **card,
        }
        works_repo.save_card(platform, aweme_id, result)
        # 新作品首次解析：manifest 里还没 domain，返回 None（前端用 DEFAULT_DOMAIN 兜底显示）
        return {**result, "cached": was_cached, "domain": works_repo.load_domain(platform, aweme_id)}
