"""对标账号作品列表（只读）。

- GET /accounts/{sec_uid}/posts   读 state/benchmark/author_{sec_uid}/all_posts.json,
  合并 collected.json 的采集状态, 返回作品卡列表。文件不存在 -> 空列表(不报错)。

只读端点: 复用沈括已落盘的 benchmark 数据, 不做采集、不新建持久化。监控账号列表
仍由 /subscriptions 提供; 本端点负责"点进某账号后看它的作品"。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ncds_opus_factory.common import authors_repo, tikhub_client
from ncds_opus_factory.server.state import RUNNER, STATE_DIR, STORE
from ncds_opus_factory.server.subscriptions import load_subscriptions, subscriptions_path

logger = logging.getLogger(__name__)

router = APIRouter()


class ResolveBody(BaseModel):
    text: str


def _resolve_identity(text: str) -> tuple[str | None, str | None]:
    """从分享链接/口令解析 (platform, author_id)。先判 TikTok(handle),否则抖音(sec_uid)。

    阻塞 IO(可能跟随短链重定向),由调用方丢线程池。都解析不出返回 (None, None)。
    """
    try:
        handle = tikhub_client.resolve_tiktok_handle(text)
    except Exception:  # noqa: BLE001
        handle = None
    if handle:
        return "tiktok", handle
    try:
        sec_uid = tikhub_client.resolve_sec_uid(text)
    except Exception:  # noqa: BLE001
        sec_uid = None
    if sec_uid:
        return "douyin", sec_uid
    return None, None


def _fetch_and_store(platform: str, author_id: str) -> dict[str, Any] | None:
    """缓存未命中:per-key 锁串行化,锁内 double-check 后实拉 TikHub 落库。

    等锁期间别人刚落库(save 必盖新 refreshed_at,故新鲜)就直接复用,避免并发重复打。
    解析失败抛 HTTPException(422)经 to_thread 透传;无资料返回 None(调用方转 422)。
    """
    with authors_repo.key_lock(platform, author_id):
        cached = authors_repo.load_profile(platform, author_id)
        if cached is not None:
            return cached
        fetch = (tikhub_client.fetch_tiktok_profile if platform == "tiktok"
                 else tikhub_client.fetch_douyin_profile)
        try:
            prof = fetch(author_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[accounts] %s resolve 失败: %s", platform, exc)
            raise HTTPException(422, "解析账号失败，请确认链接有效或稍后重试") from exc
        if not prof:
            return None
        return authors_repo.save_profile(platform, author_id, prof)


def _should_dispatch_refresh(platform: str, author_id: str) -> bool:
    """过期作者是否值得派 worker 刷新:仅已关注的抖音号,且当前无在途刷新(防堆积)。

    未关注者不派——免得给随手解析的号建 benchmark 目录,留待被关注后由订阅环刷。
    """
    if platform != "douyin":
        return False
    authors = load_subscriptions(subscriptions_path(STATE_DIR)).get("authors", [])
    if not any(a.get("sec_uid") == author_id and a.get("enabled", True) for a in authors):
        return False
    for meta in STORE.list_tasks():
        if (meta.cmd == "shenkuo"
                and str(meta.params.get("author") or "") == author_id
                and meta.status in ("pending", "running")):
            return False
    return True


async def _dispatch_refresh(platform: str, author_id: str) -> None:
    """把过期作者的刷新交给 worker(沈括 refresh_only,顺带写作者库)。失败不阻塞 resolve。"""
    if not await asyncio.to_thread(_should_dispatch_refresh, platform, author_id):
        return
    try:
        # source=cron:与订阅环共享配额/去重/自动归档,不进待验收桶
        await RUNNER.submit("shenkuo", {"author": author_id, "refresh_only": True}, source="cron")
    except Exception:  # noqa: BLE001
        logger.exception("[accounts] 过期刷新派发失败: %s", author_id[:16])


@router.post("/accounts/resolve")
async def resolve_account(body: ResolveBody) -> dict[str, Any]:
    """抖音/TikTok 主页分享链接/口令/完整 user URL -> 账号档案(走作者库缓存)。

    先查作者库:命中且新鲜(<NOF_AUTHOR_CACHE_TTL_H,默认2h) -> 秒回,不打 TikHub;
    命中但过期 -> 先回旧档案 + 把刷新派给 worker(已关注的抖音号);
    未命中 -> per-key 锁实拉 TikHub,落库再回。
    async:阻塞 IO(短链重定向 + 拉档案)丢 to_thread,不卡事件循环。
    """
    platform, author_id = await asyncio.to_thread(_resolve_identity, body.text)
    if not author_id or not platform:
        raise HTTPException(422, "无法从内容里解析出账号，请粘贴抖音/TikTok 主页分享链接或口令")

    cached = authors_repo.load_profile(platform, author_id)
    if authors_repo.is_fresh(cached):
        return {**cached, "cached": True}
    if cached is not None:
        await _dispatch_refresh(platform, author_id)
        return {**cached, "cached": True, "stale": True}

    saved = await asyncio.to_thread(_fetch_and_store, platform, author_id)
    if saved is None:
        raise HTTPException(422, "无法获取该账号资料")
    return {**saved, "cached": False}

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
