"""TikHub API 项目内封装：token / 按作者拉作品 / 取播放地址 / 下载。

douyin-downloader skill(`skills/douyin-downloader/scripts/douyin_download.py`)只覆盖单视频;
沈括(采集 agent)需要「按作者 sec_user_id 分页拉作品列表」—— 仓库没有,这里补上,
并把作品列表整理成 `all_posts.json`(鬼谷子 guiguzi 吃的精简格式:aweme_id/desc/digg/comment/share/collect/create)。

token 读 ~/.openclaw/config.json 的 tikhub_api_token(同 douyin_download)。本模块不碰飞书、不依赖项目其它部分。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests

CHUNK_SIZE = 1024 * 1024
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 60
DOWNLOAD_RETRIES = 3

API_BASE = "https://api.tikhub.io/api/v1/douyin/web"
ONE_VIDEO_URL = f"{API_BASE}/fetch_one_video"
USER_POSTS_URL = f"{API_BASE}/fetch_user_post_videos"
# 评论接口走 app/v3(与上面的 web base 不同);实测每页硬上限 20 条,count 调大无效
COMMENTS_URL = "https://api.tikhub.io/api/v1/douyin/app/v3/fetch_video_comments"
COMMENTS_PAGE_SIZE = 20

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

ProgressFn = Callable[[str], None]


def get_token(token: str | None = None) -> str:
    """读 TikHub token。优先入参,其次 ~/.openclaw/config.json。"""
    if token:
        return token
    cfg = Path(os.path.expanduser("~/.openclaw/config.json"))
    if cfg.exists():
        token = json.loads(cfg.read_text(encoding="utf-8")).get("tikhub_api_token")
    if not token:
        raise ValueError("缺少 tikhub_api_token(~/.openclaw/config.json)")
    return token


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# 按作者拉作品(新实现)
# --------------------------------------------------------------------------- #
def fetch_user_posts_page(
    sec_user_id: str, max_cursor: int = 0, count: int = 20, token: str | None = None
) -> tuple[list[dict], int, bool]:
    """拉一页作者作品。返回 (aweme_list 原始, next_cursor, has_more)。"""
    token = get_token(token)
    url = f"{USER_POSTS_URL}?sec_user_id={sec_user_id}&max_cursor={max_cursor}&count={count}"
    resp = requests.get(url, headers=_headers(token), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    resp.raise_for_status()
    payload = resp.json()
    # TikHub 通常包一层 data;容错直接返回顶层
    d = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    d = d or {}
    aweme_list = d.get("aweme_list") or d.get("aweme_lists") or []
    next_cursor = int(d.get("max_cursor") or d.get("cursor") or 0)
    has_more = bool(d.get("has_more"))
    return aweme_list, next_cursor, has_more


def simplify_aweme(aweme: dict) -> dict[str, Any]:
    """TikHub aweme -> all_posts.json 精简条目(鬼谷子格式)。"""
    st = aweme.get("statistics") or {}
    return {
        "aweme_id": str(aweme.get("aweme_id") or ""),
        "desc": aweme.get("desc") or "",
        "digg": st.get("digg_count", 0),
        "comment": st.get("comment_count", 0),
        "share": st.get("share_count", 0),
        "collect": st.get("collect_count", 0),
        "create": aweme.get("create_time", 0),
    }


def fetch_user_posts(
    sec_user_id: str, max_items: int = 60, token: str | None = None, on_progress: ProgressFn | None = None
) -> list[dict[str, Any]]:
    """分页拉作者作品,返回精简条目列表(鬼谷子 all_posts.json 格式)。"""
    token = get_token(token)
    out: list[dict[str, Any]] = []
    cursor, page = 0, 0
    seen: set[str] = set()
    while len(out) < max_items:
        aweme_list, cursor, has_more = fetch_user_posts_page(sec_user_id, cursor, 20, token)
        if not aweme_list:
            break
        for a in aweme_list:
            item = simplify_aweme(a)
            if item["aweme_id"] and item["aweme_id"] not in seen:
                seen.add(item["aweme_id"])
                out.append(item)
        page += 1
        if on_progress:
            on_progress(f"拉第 {page} 页,累计 {len(out)} 条作品")
        if not has_more:
            break
        time.sleep(0.3)
    return out[:max_items]


# --------------------------------------------------------------------------- #
# 单视频:取播放地址 + 下载(逻辑同 douyin_download,搬到项目内)
# --------------------------------------------------------------------------- #
def fetch_video_url(aweme_id: str, token: str | None = None) -> str | None:
    """aweme_id -> 无水印播放地址。"""
    token = get_token(token)
    url = f"{ONE_VIDEO_URL}?aweme_id={aweme_id}&need_anchor_info=false"
    resp = requests.get(url, headers=_headers(token), timeout=30)
    resp.raise_for_status()
    normalized = resp.text.replace("\\/", "/")
    m = re.search(r'(https://www\.douyin\.com/aweme/v1/play/[^\s"<>\\]+)', normalized)
    return m.group(1) if m else None


def download_video(url: str, output_path: str | Path, max_retries: int = DOWNLOAD_RETRIES) -> str:
    """streaming 下载 + 有限重试。写 .part 临时文件成功后原子替换。"""
    headers = {"User-Agent": _UA, "Referer": "https://www.douyin.com/"}
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with requests.get(url, headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
            tmp.replace(out)
            return str(out)
        except Exception as error:  # noqa: BLE001 — 网络异常统一重试
            last_error = error
            if tmp.exists():
                tmp.unlink()
            if attempt >= max_retries:
                break
            time.sleep(min(attempt, 3))
    raise last_error  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# 评论:拉 TOP 赞评论(热门序 + 早停,避免在爆款上翻全量)
# --------------------------------------------------------------------------- #
def fetch_comments_page(
    aweme_id: str, cursor: int = 0, count: int = COMMENTS_PAGE_SIZE, token: str | None = None
) -> tuple[list[dict], int, bool, int]:
    """拉一页评论。返回 (comments 原始, next_cursor, has_more, total)。"""
    token = get_token(token)
    params = {"aweme_id": aweme_id, "cursor": cursor, "count": count}
    resp = requests.get(
        COMMENTS_URL, headers=_headers(token), params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
    )
    resp.raise_for_status()
    payload = resp.json()
    d = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    d = d or {}
    comments = d.get("comments") or []
    next_cursor = int(d.get("cursor") or 0)
    has_more = bool(d.get("has_more"))
    total = int(d.get("total") or 0)
    return comments, next_cursor, has_more, total


def simplify_comment(c: dict) -> dict[str, Any]:
    """TikHub 评论 -> 精简条目。"""
    u = c.get("user") or {}
    return {
        "cid": str(c.get("cid") or ""),
        "nickname": u.get("nickname") or "",
        "text": c.get("text") or "",
        "digg": c.get("digg_count", 0),
        "reply": c.get("reply_comment_total", 0),
        "ip": c.get("ip_label") or "",
        "create": c.get("create_time", 0),
    }


def fetch_top_comments(
    aweme_id: str,
    top_n: int = 5,
    max_pages: int = 5,
    token: str | None = None,
    on_progress: ProgressFn | None = None,
) -> list[dict[str, Any]]:
    """拉点赞 TOP N 评论。

    抖音评论接口默认就是「热门序」(点赞+回复加权),高赞评论天然堆在最前面;
    所以不必在爆款(可能几十万条)上翻全量 —— 翻几页 + 早停即可锁定 TOP N。

    早停条件:已收集 >= top_n 条,且本页最高赞低于当前 TOP N 门槛 —— 后面的页
    不可能再翻盘进榜。该策略是启发式(非严格全局排序),对「找高赞评论」足够。
    """
    token = get_token(token)
    collected: list[dict[str, Any]] = []
    cursor, page = 0, 0
    while page < max_pages:
        comments, cursor, has_more, total = fetch_comments_page(aweme_id, cursor, COMMENTS_PAGE_SIZE, token)
        page += 1
        if not comments:
            break
        page_max = max((c.get("digg_count", 0) for c in comments), default=0)
        collected.extend(simplify_comment(c) for c in comments)
        if on_progress:
            on_progress(f"第 {page} 页,累计 {len(collected)} 条(评论区共约 {total} 条),本页最高赞 {page_max}")
        if not has_more:
            break
        # 早停:已凑满 top_n 且本页最高赞低于当前 TOP N 门槛
        threshold = sorted((c["digg"] for c in collected), reverse=True)[:top_n]
        if len(threshold) == top_n and page_max < threshold[-1]:
            if on_progress:
                on_progress(f"早停:本页最高赞 {page_max} 低于 TOP{top_n} 门槛 {threshold[-1]}")
            break
        time.sleep(0.3)
    collected.sort(key=lambda c: c["digg"], reverse=True)
    return collected[:top_n]
