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

from ncds_opus_factory.server.task_runner import TaskRunner
from ncds_opus_factory.server.task_store import TaskStore

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_HOURS = 2.0
# 配置异常时的重试间隔，别让坏文件把循环打成忙等
_ERROR_RETRY_S = 600


def subscriptions_path(state_dir: Path) -> Path:
    """state_dir 是任务目录(state/tasks)，订阅文件在兄弟目录 state/shenkuo/ 下。"""
    return state_dir.parent / "shenkuo" / "subscriptions.json"


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
        platform = str(a.get("platform") or "douyin").strip().lower() or "douyin"
        # 每账号更新频率(小时)；None=用全局 interval_hours。非法/<=0 回退 None。
        ih_raw = a.get("interval_hours")
        try:
            interval_hours = float(ih_raw) if ih_raw is not None else None
            if interval_hours is not None and interval_hours <= 0:
                interval_hours = None
        except (TypeError, ValueError):
            interval_hours = None
        author = {
            "sec_uid": sec_uid,
            "note": note if isinstance(note, str) else None,
            "enabled": bool(a.get("enabled", True)),
            "platform": platform,
            "interval_hours": interval_hours,
        }
        # 展示快照（present-only：保持手编文件干净、老 author 不被注入 null）
        for k in ("nickname", "avatar", "unique_id"):
            v = a.get(k)
            if isinstance(v, str) and v:
                author[k] = v
        for k in ("follower_count", "like_count", "works_count"):
            v = a.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                author[k] = int(v)
        ra = a.get("refreshed_at")
        if isinstance(ra, (int, float)) and not isinstance(ra, bool):
            author["refreshed_at"] = float(ra)
        authors.append(author)
    return {"interval_hours": interval, "authors": authors}


def save_subscriptions(path: Path, cfg: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


async def run_subscription_tick(runner: TaskRunner, store: TaskStore, path: Path) -> int:
    """跑一轮订阅派发，返回本轮提交的任务数。独立出来便于测试与手动触发。

    三道闸防废任务:
    - 同作者已有在途刷新 -> 跳过(防堆积);
    - 同作者上次刷新距今不足一个周期 -> 跳过(重启即跑会烧配额);
    - cron 配额桶耗尽 -> 整轮停止(别制造注定 failed 的任务行去打扰 Leader)。
    """
    cfg = load_subscriptions(path)
    # 只对抖音作者派沈括刷新；TikTok 等其它平台采集暂未接入，跳过以免堆失败 cron 任务。
    authors = [a for a in cfg["authors"] if a["enabled"] and a.get("platform", "douyin") == "douyin"]
    if not authors:
        return 0
    global_interval_h = cfg["interval_hours"]
    # 一次扫描建索引,不要每作者全表扫(O(N作者×M任务×2次文件读)纯浪费)
    active: set[str] = set()
    last_created: dict[str, str] = {}
    for meta in store.list_tasks():
        if meta.cmd != "shenkuo" or meta.source != "cron":
            continue
        author = str(meta.params.get("author") or "")
        if meta.status in ("pending", "running"):
            active.add(author)
        if author and meta.created_at > last_created.get(author, ""):
            last_created[author] = meta.created_at

    now = datetime.now()
    submitted = 0
    for author in authors:
        sec_uid = author["sec_uid"]
        if sec_uid in active:
            logger.info("[subscriptions] %s 已有在途刷新,本轮跳过", sec_uid[:16])
            continue
        last = last_created.get(sec_uid)
        if last:
            try:
                # per-account 频率优先,回退全局；0.9 容差:循环调度抖动不该把整轮顺延
                interval_s = float(author.get("interval_hours") or global_interval_h) * 3600
                if (now - datetime.fromisoformat(last)).total_seconds() < interval_s * 0.9:
                    continue
            except ValueError:
                pass
        if hasattr(runner, "quota_remaining") and runner.quota_remaining("shenkuo", source="cron") <= 0:
            logger.warning("[subscriptions] cron 配额耗尽,本轮订阅刷新停止")
            break
        try:
            task_id = await runner.submit(
                "shenkuo", {"author": sec_uid, "refresh_only": True}, source="cron"
            )
            submitted += 1
            logger.info("[subscriptions] 刷新派发: %s -> %s", sec_uid[:16], task_id)
        except Exception:  # noqa: BLE001 — 单作者失败不影响其余
            logger.exception("[subscriptions] 派发失败: %s", sec_uid[:16])
    return submitted


async def subscription_loop(runner: TaskRunner, store: TaskStore, path: Path) -> None:
    """常驻循环：每 interval_hours 一轮。配置热读——改文件即生效，无需重启。"""
    logger.info("[subscriptions] 订阅传感器启动: %s", path)
    while True:
        try:
            n = await run_subscription_tick(runner, store, path)
            if n:
                logger.info("[subscriptions] 本轮派发 %d 个刷新任务", n)
            # tick 间隔取「全局 + 各账号 per-account 频率」的最小值，保证最快的账号也能按时刷新
            cfg = load_subscriptions(path)
            candidates = [float(cfg.get("interval_hours", _DEFAULT_INTERVAL_HOURS))]
            candidates += [
                a["interval_hours"] for a in cfg["authors"]
                if a.get("enabled") and a.get("platform", "douyin") == "douyin" and a.get("interval_hours")
            ]
            interval = max(0.25, min(candidates))
            await asyncio.sleep(interval * 3600)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 循环必须活着
            logger.exception("[subscriptions] tick failed")
            await asyncio.sleep(_ERROR_RETRY_S)
