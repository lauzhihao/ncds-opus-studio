"""server/maintenance.py — 周期性维护 loop，供 app.py 与 worker.py 共用。

搬出 app.py 的目的：worker.py 需要独立运行这些 loop，
如果留在 app.py，worker 就不得不 import 整个 FastAPI app，产生不必要的副作用。
worker / app 各自 import 此模块，不存在循环 import（本模块不 import app / routes）。
"""

from __future__ import annotations

import logging
import os

from ncds_opus_factory.server import rounds_gate

logger = logging.getLogger(__name__)


def _store():
    """延迟 import STORE，避免 reload(state) 后 maintenance 还持有旧引用。"""
    from ncds_opus_factory.server.state import STORE as _s
    return _s


def _labels():
    """延迟 import LABELS，避免 reload(state) 后 maintenance 还持有旧引用。"""
    from ncds_opus_factory.server.state import LABELS as _l
    return _l


# ---- 弃用素材定时清除 ----
# 沈括的决策语义是「通过/弃用」(没有打回重做):弃用=rejected 进已归档,
# 保留一段时间允许「拉回待验收」,超期由这里的协程整目录清掉(只删任务记录,
# 不动 state/benchmark 下共享的已下载素材)。
DISCARD_TTL_HOURS = float(os.environ.get("NOF_DISCARD_TTL_HOURS", "168"))   # 默认保留 7 天
# cron 刷新任务独立 TTL:订阅传感器每天产十几条任务记录,自动归档后无人再看,
# 比人工弃用件清得更快(默认 3 天)。案卷照例先于删除存在。
CRON_TTL_HOURS = float(os.environ.get("NOF_CRON_TTL_HOURS", "72"))
_DISCARD_SWEEP_INTERVAL_S = 3600

# round 对账周期(§4.5):比清扫快——续跑丢失要在分钟级被补上,不能等一小时
_ROUND_RECONCILE_INTERVAL_S = float(os.environ.get("NOF_ROUND_RECONCILE_S", "300"))


def backfill_labels_once() -> int:
    """案卷对账:凡有决策(review.json)而无案卷的任务,幂等补写一份。返回补写数。

    吃下两类缺口:P1 上线前的存量决策(现成的种子语料)、review 路由落卷失败
    被吞掉的样本。每小时随清扫跑一轮,复盘(P4)只读 labels/,缺卷即缺样本。
    """
    # 延迟取单例，确保 test_labels 的 reload(state) 后拿到的是新鲜 STORE/LABELS
    store = _store()
    labels = _labels()
    filled = 0
    for meta in store.list_tasks():
        if meta.decision is None or labels.exists(meta.task_id):
            continue
        review = store.get_review(meta.task_id)
        if review is None:
            continue
        try:
            labels.write(meta, review, store.get_result(meta.task_id))
            filled += 1
        except Exception:  # noqa: BLE001 — 单条失败不影响整轮
            logger.exception("[sweep] case backfill failed %s", meta.task_id)
    return filled


def sweep_discarded_once() -> int:
    """清一轮:沈括 rejected 且超过保留期的任务。返回删除数。"""
    import shutil
    from datetime import datetime, timedelta

    # 延迟取单例，确保 reload(state) 后拿到新鲜引用
    store = _store()
    labels = _labels()
    cutoff = datetime.now() - timedelta(hours=DISCARD_TTL_HOURS)
    removed = 0
    for meta in store.list_tasks():
        if meta.cmd != "shenkuo" or meta.decision != "rejected":
            continue
        review = store.get_review(meta.task_id)
        if review is None or not review.reviewed_at:
            continue
        try:
            ts = datetime.fromisoformat(review.reviewed_at)
        except ValueError:
            continue
        if ts < cutoff:
            # 删除前确认案卷已存在(决策=标注,负样本是卧龙复盘的训练数据,
            # 任务目录可以清,标签不能丢)。补写失败则本轮跳过,下轮再试。
            try:
                if not labels.exists(meta.task_id):
                    labels.write(meta, review, store.get_result(meta.task_id))
            except Exception:  # noqa: BLE001
                logger.exception("[sweep] label write failed, skip delete %s", meta.task_id)
                continue
            shutil.rmtree(store.task_dir(meta.task_id), ignore_errors=True)
            removed += 1
            logger.info("[sweep] removed discarded shenkuo task %s (reviewed_at=%s)", meta.task_id, review.reviewed_at)
    return removed


def sweep_cron_once() -> int:
    """清一轮:cron 刷新任务终态后超过 CRON_TTL_HOURS 的记录。返回删除数。

    订阅传感器的任务自动归档(reviewer=system),没有人工价值,只占列表;
    有决策的照例先确保案卷存在再删(自动归档的 system 案卷复盘本就不学)。
    """
    import shutil
    from datetime import datetime, timedelta

    # 延迟取单例，确保 reload(state) 后拿到新鲜引用
    store = _store()
    labels = _labels()
    cutoff = datetime.now() - timedelta(hours=CRON_TTL_HOURS)
    removed = 0
    for meta in store.list_tasks():
        # 只清订阅刷新任务:别让将来其他 cron 触发的任务静默继承 72h 删除策略
        if (
            meta.source != "cron"
            or meta.cmd != "shenkuo"
            or not meta.params.get("refresh_only")
            or meta.status not in ("completed", "failed", "cancelled")
        ):
            continue
        # 只清纯机器闭环的记录:被用户撤销(review=None,等重审)或人工改判
        # (reviewer=user)的任务移交人工清扫节奏(sweep_discarded_once 的 168h)
        review = store.get_review(meta.task_id)
        if review is None or review.reviewer != "system":
            continue
        ref = meta.finished_at or meta.created_at
        try:
            ts = datetime.fromisoformat(ref)
        except ValueError:
            continue
        if ts >= cutoff:
            continue
        try:
            if not labels.exists(meta.task_id):
                labels.write(meta, review, store.get_result(meta.task_id))
        except Exception:  # noqa: BLE001
            logger.exception("[sweep] cron label write failed, skip delete %s", meta.task_id)
            continue
        shutil.rmtree(store.task_dir(meta.task_id), ignore_errors=True)
        removed += 1
    return removed


async def _round_reconciler() -> None:
    import asyncio

    while True:
        try:
            n = await rounds_gate.reconcile_once()
            if n:
                logger.info("[rounds] reconcile processed %d rounds", n)
        except Exception:  # noqa: BLE001 — 对账失败不影响服务
            logger.exception("[rounds] reconcile failed")
        await asyncio.sleep(_ROUND_RECONCILE_INTERVAL_S)


async def _discard_sweeper() -> None:
    import asyncio

    while True:
        try:
            # 先对账补卷,再清扫——回填和守门双保险,标签先于删除存在
            filled = backfill_labels_once()
            if filled:
                logger.info("[sweep] backfilled %d label files", filled)
            n = sweep_discarded_once()
            if n:
                logger.info("[sweep] removed %d discarded tasks", n)
            c = sweep_cron_once()
            if c:
                logger.info("[sweep] removed %d cron refresh records", c)
        except Exception:  # noqa: BLE001 — 清扫失败不影响服务
            logger.exception("[sweep] discard sweep failed")
        await asyncio.sleep(_DISCARD_SWEEP_INTERVAL_S)
