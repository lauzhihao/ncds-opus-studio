"""作者库 —— 按 (platform, author_id) 内容寻址的作者档案存储 + 缓存。

把"同一作者的名片(昵称/头像/粉丝数)只拉一次"做成全局事实:任何入口
(关注弹窗解析 /accounts/resolve、订阅环刷新、PUT /subscriptions 携带的快照)
落到同一 (platform, id) 就命中同一份档案。关注名单只存作者 ID 引用,
展示时来这里 hydrate。这里既是数据源、又是 resolve 的 2h 缓存。

    state/authors/{platform}/{id}/profile.json
      {platform, sec_uid, unique_id, nickname, avatar,
       follower_count, like_count, works_count, refreshed_at}

寻址键(author_key):douyin = sec_uid;tiktok/youtube = unique_id(handle/channel,resolve 阶段
就拿得到,sec_uid 要拉档案后才知道)。refreshed_at = 我方最后一次写入该档案的
unix 秒,既给卡片"最近更新 X前",也做 TTL 新鲜判定。

根目录口径与 works_repo 一致:**运行时**解析 NOF_STATE_DIR 的父目录(免得测试
setenv 后还要 reload),也刻意不反向 import server.state,保持 common 独立可测。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]

# 默认缓存 TTL(小时):2h 内有人解析过同一作者就复用,不重打 TikHub。env 可覆盖。
_DEFAULT_TTL_HOURS = 2.0

# 展示快照字段(hydrate / 迁移 / PUT upsert 共用一份口径)。身份字段(sec_uid/unique_id)另算。
DISPLAY_FIELDS = ("nickname", "avatar", "follower_count", "like_count", "works_count")

# per-key 锁:同一作者并发解析只落一次盘(配合 routes 层实拉串行化)。
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def _state_root() -> Path:
    env = os.environ.get("NOF_STATE_DIR")
    return Path(env).parent if env else _ROOT / "state"


def authors_root() -> Path:
    return _state_root() / "authors"


def ttl_seconds() -> float:
    """缓存新鲜窗口(秒)。env NOF_AUTHOR_CACHE_TTL_H 覆盖,非法/<=0 回退默认 2h。"""
    raw = os.environ.get("NOF_AUTHOR_CACHE_TTL_H")
    try:
        hours = float(raw) if raw is not None else _DEFAULT_TTL_HOURS
        if hours <= 0:
            raise ValueError
    except (TypeError, ValueError):
        hours = _DEFAULT_TTL_HOURS
    return hours * 3600.0


def author_key(platform: str, sec_uid: str = "", unique_id: str = "") -> str:
    """库寻址键:tiktok/youtube 用 handle/channel(unique_id),douyin 用 sec_uid。"""
    sec_uid = (sec_uid or "").strip()
    unique_id = (unique_id or "").strip()
    if platform in {"tiktok", "youtube"}:
        return unique_id or sec_uid
    return sec_uid or unique_id


def _safe_id(author_id: str) -> str:
    """落文件名前消毒:sec_uid/handle 理论安全,仍挡掉 / 和 .. 防越权写。"""
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", (author_id or "").strip())


def key_lock(platform: str, author_id: str) -> threading.Lock:
    key = f"{platform}_{author_id}"
    with _LOCKS_GUARD:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = _LOCKS[key] = threading.Lock()
        return lk


def profile_path(platform: str, author_id: str) -> Path:
    return authors_root() / platform / _safe_id(author_id) / "profile.json"


def load_profile(platform: str, author_id: str) -> dict[str, Any] | None:
    """读作者档案;缺文件/坏文件返回 None(回退实拉),不抛。"""
    if not author_id:
        return None
    try:
        return json.loads(profile_path(platform, author_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_profile(
    platform: str,
    author_id: str,
    profile: dict[str, Any],
    *,
    refreshed_at: float | None = None,
) -> dict[str, Any]:
    """落盘作者档案;refreshed_at=None 盖当前时刻,否则用给定值(迁移/PUT 回放时保真)。

    返回落盘后的完整档案。id 以寻址键为准回填,不信调用方传进来的身份字段。
    """
    path = profile_path(platform, author_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(profile)
    doc["platform"] = platform
    if platform in {"tiktok", "youtube"}:
        doc.setdefault("unique_id", author_id)
        doc.setdefault("sec_uid", profile.get("sec_uid") or "")
    else:
        doc["sec_uid"] = doc.get("sec_uid") or author_id
    doc["refreshed_at"] = float(time.time()) if refreshed_at is None else float(refreshed_at)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # 原子替换
    return doc


def is_fresh(profile: dict[str, Any] | None, ttl: float | None = None) -> bool:
    """档案是否在新鲜窗口内。无档案/无时间戳 -> False(当作过期/未命中)。"""
    if not profile:
        return False
    ra = profile.get("refreshed_at")
    if not isinstance(ra, (int, float)) or isinstance(ra, bool):
        return False
    window = ttl_seconds() if ttl is None else ttl
    return (time.time() - float(ra)) < window
