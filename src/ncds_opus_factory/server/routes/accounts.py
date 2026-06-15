"""对标账号作品列表（只读）。

- GET /accounts/{sec_uid}/posts   读 state/benchmark/author_{sec_uid}/all_posts.json,
  合并 collected.json 的采集状态, 返回作品卡列表。文件不存在 -> 空列表(不报错)。

只读端点: 复用沈括已落盘的 benchmark 数据, 不做采集、不新建持久化。监控账号列表
仍由 /subscriptions 提供; 本端点负责"点进某账号后看它的作品"。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ncds_opus_factory.common import tikhub_client
from ncds_opus_factory.server.state import STATE_DIR
from ncds_opus_factory.server.subscriptions import load_subscriptions, subscriptions_path

logger = logging.getLogger(__name__)

router = APIRouter()


class ResolveBody(BaseModel):
    text: str


def _cached_profile(author: dict[str, Any]) -> dict[str, Any] | None:
    """订阅里已存的展示快照 -> resolve 结果形状；缺关键字段(无昵称且无头像)则 None(回退实拉)。"""
    if not author.get("nickname") and not author.get("avatar"):
        return None
    return {
        "platform": author.get("platform", "douyin"),
        "sec_uid": author.get("sec_uid", ""),
        "nickname": author.get("nickname") or author.get("note") or "",
        "unique_id": author.get("unique_id") or "",
        "avatar": author.get("avatar") or "",
        "follower_count": int(author.get("follower_count") or 0),
        "like_count": int(author.get("like_count") or 0),
        "works_count": int(author.get("works_count") or 0),
        "cached": True,
    }


@router.post("/accounts/resolve")
def resolve_account(body: ResolveBody) -> dict[str, Any]:
    """把抖音/TikTok 主页分享链接 / 口令 / 完整 user URL 解析成账号档案。

    先在服务端全局订阅里按身份(douyin=sec_uid / tiktok=handle)查：已被监控且有快照 -> 直接复用，
    不再打 TikHub（省额度/更快）。否则实拉 TikHub 主页档案并归一化返回。
    sync def（FastAPI 走线程池）—— 跟随短链重定向 + 拉档案是阻塞 IO。
    """
    authors = load_subscriptions(subscriptions_path(STATE_DIR)).get("authors", [])

    # TikTok：按 @handle(unique_id) 找
    try:
        handle = tikhub_client.resolve_tiktok_handle(body.text)
    except Exception:  # noqa: BLE001
        handle = None
    if handle:
        for a in authors:
            if a.get("platform") == "tiktok" and a.get("unique_id") == handle:
                cached = _cached_profile(a)
                if cached:
                    return cached
        try:
            prof = tikhub_client.fetch_tiktok_profile(handle)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[accounts] tiktok resolve 失败: %s", exc)
            raise HTTPException(422, "解析账号失败，请确认链接有效或稍后重试") from exc
        if not prof:
            raise HTTPException(422, "无法获取该 TikTok 账号资料")
        return prof

    # 抖音：按 sec_uid 找
    try:
        sec_uid = tikhub_client.resolve_sec_uid(body.text)
    except Exception:  # noqa: BLE001
        sec_uid = None
    if sec_uid:
        for a in authors:
            if a.get("platform", "douyin") == "douyin" and a.get("sec_uid") == sec_uid:
                cached = _cached_profile(a)
                if cached:
                    return cached
        try:
            prof = tikhub_client.fetch_douyin_profile(sec_uid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[accounts] douyin resolve 失败: %s", exc)
            raise HTTPException(422, "解析账号失败，请确认链接有效或稍后重试") from exc
        if not prof:
            raise HTTPException(422, "无法获取该抖音账号资料")
        return prof

    raise HTTPException(422, "无法从内容里解析出账号，请粘贴抖音/TikTok 主页分享链接或口令")

# 沈括落盘根: state/benchmark/author_{sec_uid}/ (STATE_DIR=state/tasks, parent=state)
_BENCH_DIR = STATE_DIR.parent / "benchmark"


def _load_json(path: Path) -> Any:
    """读 JSON; 缺文件/坏文件一律返回 None(调用方兜底), 不抛。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@router.get("/accounts/{sec_uid}/posts")
def get_account_posts(sec_uid: str) -> dict[str, Any]:
    """返回某对标号的作品列表(高赞优先), 每条带封面/四项数据/share_url/是否已采集。

    sync def（走线程池）—— 老数据缺封面时会重拉一次列表（带封面）并回写，自愈一次。
    """
    author_dir = _BENCH_DIR / f"author_{sec_uid}"
    posts = _load_json(author_dir / "all_posts.json")
    if not isinstance(posts, list):
        # 还没采集过这个账号 -> 空列表, 前端展示"暂无作品, 待采集"。
        return {"sec_uid": sec_uid, "posts": []}

    # 老数据缺封面或缺时长（cover_url/duration 都是后加的）-> 重拉一次补齐并回写，之后命中缓存。
    if posts and isinstance(posts[0], dict) and (not posts[0].get("cover_url") or "duration" not in posts[0]):
        try:
            fresh = tikhub_client.fetch_user_posts(sec_uid, max_items=max(len(posts), 30))
            if fresh:
                (author_dir / "all_posts.json").write_text(
                    json.dumps(fresh, ensure_ascii=False, indent=2), encoding="utf-8")
                posts = fresh
        except Exception as exc:  # noqa: BLE001 — 重拉失败就用老数据(无封面), 不阻塞
            logger.warning("[accounts] 封面回填重拉失败 %s: %s", sec_uid[:16], exc)

    # collected.json: {"generated_at", "items": [entry...]} —— 取已深采的 aweme_id 集合
    collected_doc = _load_json(author_dir / "collected.json")
    collected_ids: set[str] = set()
    if isinstance(collected_doc, dict):
        for it in collected_doc.get("items") or []:
            aid = str((it or {}).get("aweme_id") or "")
            if aid:
                collected_ids.add(aid)

    out: list[dict[str, Any]] = []
    for p in posts:
        if not isinstance(p, dict):
            continue
        aid = str(p.get("aweme_id") or "")
        if not aid:
            continue
        out.append(
            {
                "aweme_id": aid,
                "desc": p.get("desc") or "",
                "digg": p.get("digg", 0),
                "comment": p.get("comment", 0),
                "share": p.get("share", 0),
                "collect": p.get("collect", 0),
                "create": p.get("create", 0),
                "cover_url": p.get("cover_url") or "",
                "duration": p.get("duration", 0),  # 秒；0=未知，前端不渲染时长徽标
                # 构造抖音作品页链接: 衍生作品画布据此 seed 源(沈括单链/asr 都能解析)。
                "share_url": f"https://www.douyin.com/video/{aid}",
                "collected": aid in collected_ids,
            }
        )
    out.sort(key=lambda x: x.get("digg", 0), reverse=True)
    return {"sec_uid": sec_uid, "posts": out}
