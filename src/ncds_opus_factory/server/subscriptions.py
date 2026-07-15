"""订阅传感器（docs/WOLONG-DESIGN.md §6.1）：周期给订阅的对标作者派沈括刷新任务。

订阅文件 state/shenkuo/subscriptions.json（手编或经 GET/PUT /subscriptions 维护）：

    {
      "interval_hours": 2,
      "authors": [
        {"sec_uid": "MS4wLjABAAAA...", "note": "对标号A", "enabled": true}
      ]
    }

行为：
- 每 interval_hours 一轮，逐启用作者提交 {cmd: shenkuo, refresh_only: true, source: cron}
  ——走任务系统（受额度/配额管控），不裸调 CLI。
- 同作者已有 pending/running 的 cron 刷新任务则本轮跳过（防堆积）。
- 任务完成由 TaskRunner 自动归档（reviewer=system），不进待验收桶、不点红灯。
- 文件不存在 = 无订阅，循环空转；NOF_SUBSCRIPTIONS=0 整体停用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from ncds_opus_factory.common import authors_repo
from ncds_opus_factory.server.queue import get_default_queue
from ncds_opus_factory.server.task_runner import TaskRunner
from ncds_opus_factory.server.task_store import TaskStore

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_HOURS = 3.0
# 配置异常时的重试间隔，别让坏文件把循环打成忙等
_ERROR_RETRY_S = 600
_SUPPORTED_PLATFORMS = {"douyin", "tiktok", "youtube"}


def _platform(v: Any) -> str:
    p = str(v or "douyin").strip().lower() or "douyin"
    return p if p in _SUPPORTED_PLATFORMS else "douyin"


def _author_task_key(author: dict[str, Any]) -> str:
    platform = _platform(author.get("platform"))
    return authors_repo.author_key(platform, str(author.get("sec_uid") or ""), str(author.get("unique_id") or ""))


def _task_key_from_params(params: dict[str, Any]) -> str:
    author = str(params.get("author") or "")
    if not author:
        return ""
    platform = _platform(params.get("platform"))
    return f"{platform}:{author}"


def subscriptions_path(state_dir: Path, owner_id: str | None = None) -> Path:
    """state_dir 是任务目录(state/tasks)，订阅文件在兄弟目录 state/shenkuo/ 下。

    - owner_id 给定：``state/shenkuo/users/{owner_id}/subscriptions.json``（用户隔离）
    - owner_id 空：兼容旧路径 ``state/shenkuo/subscriptions.json``（auth 关闭 / 迁移源）
    """
    base = state_dir.parent / "shenkuo"
    if owner_id:
        return base / "users" / str(owner_id) / "subscriptions.json"
    return base / "subscriptions.json"


def legacy_subscriptions_path(state_dir: Path) -> Path:
    return state_dir.parent / "shenkuo" / "subscriptions.json"


def iter_subscription_paths(state_dir: Path) -> list[tuple[str | None, Path]]:
    """cron 用：枚举 (owner_id, path)。含 legacy 全局文件 + 各用户文件。"""
    out: list[tuple[str | None, Path]] = []
    legacy = legacy_subscriptions_path(state_dir)
    if legacy.is_file():
        out.append((None, legacy))
    users_root = state_dir.parent / "shenkuo" / "users"
    if users_root.is_dir():
        for d in sorted(users_root.iterdir()):
            if not d.is_dir():
                continue
            p = d / "subscriptions.json"
            if p.is_file():
                out.append((d.name, p))
    if not out:
        out.append((None, legacy))
    return out


def ensure_user_subscriptions(state_dir: Path, owner_id: str) -> Path:
    """确保用户订阅文件存在：无则从 legacy 全局文件复制一次（单租户迁移）。"""
    path = subscriptions_path(state_dir, owner_id)
    if path.is_file():
        return path
    legacy = legacy_subscriptions_path(state_dir)
    if legacy.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info("[subscriptions] 从 legacy 复制订阅到 user=%s", owner_id)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_subscriptions(path: Path) -> dict[str, Any]:
    """读取并归一化:文件支持手编,坏条目丢弃而非让 GET 路由 500、tick 行为漂移。"""
    if not path.exists():
        return {"interval_hours": _DEFAULT_INTERVAL_HOURS, "authors": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("[subscriptions] 配置不可读: %s", path)
        return {"interval_hours": _DEFAULT_INTERVAL_HOURS, "authors": []}
    try:
        interval = float(raw.get("interval_hours", _DEFAULT_INTERVAL_HOURS))
        if interval <= 0:
            raise ValueError
    except (TypeError, ValueError):
        logger.warning("[subscriptions] interval_hours 非法,回退默认: %r", raw.get("interval_hours"))
        interval = _DEFAULT_INTERVAL_HOURS
    authors: list[dict[str, Any]] = []
    for a in raw.get("authors") or []:
        if not isinstance(a, dict):
            logger.warning("[subscriptions] 丢弃非法 author 条目: %r", a)
            continue
        sec_uid = str(a.get("sec_uid") or "").strip()
        if not sec_uid:
            continue
        note = a.get("note")
        platform = _platform(a.get("platform"))
        # 每账号更新频率(小时)；None=用全局 interval_hours。非法/<=0 回退 None。
        ih_raw = a.get("interval_hours")
        try:
            interval_hours = float(ih_raw) if ih_raw is not None else None
            if interval_hours is not None and interval_hours <= 0:
                interval_hours = None
        except (TypeError, ValueError):
            interval_hours = None
        unique_id = a.get("unique_id")
        unique_id = unique_id.strip() if isinstance(unique_id, str) and unique_id.strip() else None
        author = {
            "sec_uid": sec_uid,
            "note": note if isinstance(note, str) else None,
            "enabled": bool(a.get("enabled", True)),
            "platform": platform,
            "interval_hours": interval_hours,
        }
        # 领域 profile（见 web config/domains.ts + domain_profiles.py）。
        # present-only：仅有非空字符串时写入，保手编文件干净 + 老 author 不被注入 null。
        domain = a.get("domain")
        if isinstance(domain, str) and domain.strip():
            author["domain"] = domain.strip()
        # unique_id 是 tiktok 的作者库寻址键(身份)，保留进瘦引用；
        # 展示快照(昵称/头像/粉丝数/refreshed_at)不再内联，统一去作者库取(见 authors_repo)。
        if unique_id:
            author["unique_id"] = unique_id
        _migrate_inline_snapshot(platform, sec_uid, unique_id, a)
        authors.append(author)
    return {"interval_hours": interval, "authors": authors}


def _migrate_inline_snapshot(
    platform: str, sec_uid: str, unique_id: str | None, raw: dict[str, Any]
) -> None:
    """老订阅文件把作者名片内联在条目里；一次性搬进作者库(关注名单从此只存引用)。

    仅当作者库尚无该作者时迁移(库里可能已被 worker 刷新成更新的，别用内联旧值覆盖)，
    并保留内联原 refreshed_at(无则置 0 -> 视为过期，尽快被刷新)。无内联快照则跳过。
    幂等且自限：迁移后 load_profile 命中即提前返回，热路径(GET/tick)退化为纯读。
    """
    display: dict[str, Any] = {}
    for k in ("nickname", "avatar"):
        v = raw.get(k)
        if isinstance(v, str) and v:
            display[k] = v
    for k in ("follower_count", "like_count", "works_count"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            display[k] = int(v)
    if not display:
        return
    key = authors_repo.author_key(platform, sec_uid, unique_id or "")
    if not key or authors_repo.load_profile(platform, key) is not None:
        return
    ra = raw.get("refreshed_at")
    ra = float(ra) if isinstance(ra, (int, float)) and not isinstance(ra, bool) else 0.0
    display["sec_uid"] = sec_uid
    if unique_id:
        display["unique_id"] = unique_id
    try:
        authors_repo.save_profile(platform, key, display, refreshed_at=ra)
    except OSError:
        logger.warning("[subscriptions] 作者库迁移落盘失败: %s", key[:16])


def save_subscriptions(path: Path, cfg: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


async def run_subscription_tick(
    runner: TaskRunner,
    store: TaskStore,
    path: Path,
    *,
    owner_id: str | None = None,
) -> int:
    """跑一轮订阅派发，返回本轮提交的任务数。独立出来便于测试与手动触发。

    三道闸防废任务:
    - 同作者已有在途刷新 -> 跳过(防堆积);
    - 同作者上次刷新距今不足一个周期 -> 跳过(重启即跑会烧配额);
    - cron 配额桶耗尽 -> 整轮停止(别制造注定 failed 的任务行去打扰 Leader)。

    ``owner_id`` 写入新建 cron 任务的 meta，便于按用户隔离收件箱。
    """
    cfg = load_subscriptions(path)
    authors = [a for a in cfg["authors"] if a["enabled"] and _author_task_key(a)]
    if not authors:
        return 0
    global_interval_h = cfg["interval_hours"]
    # 一次扫描建索引,不要每作者全表扫(O(N作者×M任务×2次文件读)纯浪费)
    active: set[str] = set()
    last_created: dict[str, str] = {}
    for meta in store.list_tasks():
        if meta.cmd != "shenkuo" or meta.source != "cron":
            continue
        key = _task_key_from_params(meta.params)
        if not key:
            continue
        if meta.status in ("pending", "running"):
            active.add(key)
        if key and meta.created_at > last_created.get(key, ""):
            last_created[key] = meta.created_at

    now = datetime.now()
    submitted = 0
    for author in authors:
        platform = _platform(author.get("platform"))
        author_key = _author_task_key(author)
        if not author_key:
            continue
        task_key = f"{platform}:{author_key}"
        if task_key in active:
            logger.info("[subscriptions] %s 已有在途刷新,本轮跳过", task_key[:32])
            continue
        last = last_created.get(task_key)
        if last:
            try:
                # per-account 频率优先,回退全局；0.9 容差:循环调度抖动不该把整轮顺延
                interval_s = float(author.get("interval_hours") or global_interval_h) * 3600
                if (now - datetime.fromisoformat(last)).total_seconds() < interval_s * 0.9:
                    continue
            except ValueError:
                pass
        if hasattr(runner, "quota_remaining") and await runner.quota_remaining("shenkuo", source="cron") <= 0:
            logger.warning("[subscriptions] cron 配额耗尽,本轮订阅刷新停止")
            break
        try:
            task_id = await runner.submit(
                "shenkuo",
                {"author": author_key, "platform": platform, "refresh_only": True},
                source="cron",
                owner_id=owner_id,
            )
            submitted += 1
            logger.info("[subscriptions] 刷新派发: %s -> %s", task_key[:32], task_id)
        except Exception:  # noqa: BLE001 — 单作者失败不影响其余
            logger.exception("[subscriptions] 派发失败: %s", task_key[:32])
    return submitted


async def subscription_loop(runner: TaskRunner, store: TaskStore, path: Path) -> None:
    """常驻循环：每 interval_hours 一轮。配置热读——改文件即生效，无需重启。

    ``path`` 参数保留兼容（旧调用方传入 legacy 路径）；实际每轮枚举 legacy + 各用户订阅。
    """
    state_dir = store.base_dir
    logger.info("[subscriptions] 订阅传感器启动: state_dir=%s legacy=%s", state_dir, path)
    while True:
        try:
            # Redis 探活：down 时静默跳过本 tick，不创建垃圾 failed meta（决策 D）。
            if not await get_default_queue().ping():
                await asyncio.sleep(_ERROR_RETRY_S)
                continue
            total = 0
            candidates = [float(_DEFAULT_INTERVAL_HOURS)]
            for owner_id, sub_path in iter_subscription_paths(state_dir):
                n = await run_subscription_tick(runner, store, sub_path, owner_id=owner_id)
                total += n
                cfg = load_subscriptions(sub_path)
                candidates.append(float(cfg.get("interval_hours", _DEFAULT_INTERVAL_HOURS)))
                candidates += [
                    a["interval_hours"] for a in cfg["authors"]
                    if a.get("enabled") and _author_task_key(a) and a.get("interval_hours")
                ]
            if total:
                logger.info("[subscriptions] 本轮派发 %d 个刷新任务", total)
            interval = max(0.25, min(candidates))
            await asyncio.sleep(interval * 3600)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 循环必须活着
            logger.exception("[subscriptions] tick failed")
            await asyncio.sleep(_ERROR_RETRY_S)
