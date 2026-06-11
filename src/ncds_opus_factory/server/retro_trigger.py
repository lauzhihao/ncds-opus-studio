"""复盘触发协程（docs/WOLONG-DESIGN.md §8.3 第 1 条）：每晚低峰投递 retro 段。

app startup 挂起（subscription_loop 同款）,周期检查四道闸,全过才投递
{cmd:"wolong", params:{mode:"retro"}, source:"retro"}：

1. 时间窗：每日 NOF_RETRO_HOUR（默认 4 点）±1h;
2. 防堆积：已有在途(pending/running) retro 任务则跳过;
3. 样本闸：水位(retro_state.json)之后新增 user 标签 ≥ NOF_RETRO_MIN_LABELS
   （与复盘段同一口径——复盘成功推进水位后,同窗口内不会重复触发;
   复盘失败水位不动,窗口内自动重试,_retro 配额桶是失控刹车）;
4. 配额：retro 桶有余量。

NOF_RETRO=0 整体停用。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from ncds_opus_factory.common import rubric_store
from ncds_opus_factory.commands.wolong_retro import MIN_LABELS
from ncds_opus_factory.server.task_runner import TaskRunner
from ncds_opus_factory.server.task_store import TaskStore

logger = logging.getLogger(__name__)

def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[retro] %s 非法(%r),使用默认 %.1f", name, raw, default)
        return default


RETRO_HOUR = _env_float("NOF_RETRO_HOUR", 4.0) % 24
_WINDOW_HOURS = 1.0
_CHECK_INTERVAL_S = 600
_ERROR_RETRY_S = 600


def in_window(now: datetime, hour: float = RETRO_HOUR, width: float = _WINDOW_HOURS) -> bool:
    """now 是否落在每日 hour 点 ±width 小时的窗口内（跨午夜环绕也成立）。"""
    now_h = now.hour + now.minute / 60.0
    diff = abs(now_h - hour)
    return min(diff, 24.0 - diff) <= width


def _has_inflight_retro(store: TaskStore) -> bool:
    for m in store.list_tasks():
        if m.cmd == "wolong" and m.source == "retro" and m.status in ("pending", "running"):
            return True
    return False


async def retro_tick(runner: TaskRunner, store: TaskStore) -> str | None:
    """检查四道闸,全过则投递一个复盘任务。返回 task_id(未投递返回 None)。"""
    if not in_window(datetime.now()):
        return None
    if _has_inflight_retro(store):
        logger.info("[retro] 已有在途复盘任务,本轮跳过")
        return None
    since = str(rubric_store.read_retro_state().get("last_reviewed_at") or "")
    fresh = rubric_store.count_new_user_labels(since)
    if fresh < MIN_LABELS:
        logger.info("[retro] 新标签不足(%d/%d),本轮跳过", fresh, MIN_LABELS)
        return None
    if runner.quota_remaining("wolong", source="retro") <= 0:
        logger.warning("[retro] retro 配额耗尽,今晚复盘暂缓")
        return None
    task_id = await runner.submit("wolong", {"mode": "retro"}, source="retro")
    logger.info("[retro] 复盘任务已投递: %s (新标签 %d 条)", task_id, fresh)
    return task_id


async def retro_loop(runner: TaskRunner, store: TaskStore) -> None:
    """常驻循环：每 10 分钟检查一次触发条件（窗口外的检查近乎零成本）。"""
    logger.info("[retro] 复盘触发器启动: 每日 %.1f 点 ±%.0fh, 样本闸 %d 条",
                RETRO_HOUR, _WINDOW_HOURS, MIN_LABELS)
    while True:
        try:
            await retro_tick(runner, store)
            await asyncio.sleep(_CHECK_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 循环必须活着
            logger.exception("[retro] tick failed")
            await asyncio.sleep(_ERROR_RETRY_S)
