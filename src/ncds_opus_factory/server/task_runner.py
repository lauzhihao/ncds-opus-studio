"""异步任务执行器：接收 → 入队（per-cmd 额度）→ 执行 → 投递 → 反馈。

每个 command.run 都是「同步阻塞函数 + on_progress(text) 回调」的统一形态，
执行仍在 asyncio.to_thread 的工作线程里跑。P2 调度化（docs/WOLONG-DESIGN.md §2）：

1. submit()/requeue() 统一投 per-cmd 等待队列，不再 fire-and-forget——多实例
   执行必须收口：沈括撞 TikHub 限流、卧龙耗 sclaude 账号池、柳永吃 scodex 子进程。
2. worker 在 app startup 钩子里拉起（RUNNER 在 import 期构建，当时无 event loop）；
   出队做 pending→running 的 CAS：排队中被取消、cancel→restore 双入队都不会双跑
   （检查与置位之间无 await，单事件循环上原子）。
3. 启动恢复：队列在进程内存，重启即蒸发。startup 扫 store 把 pending 积压重新入队、
   孤儿 running 复位；超过恢复期限的直接判 failed（可见可重发，不留僵尸）。
4. 配额：派单类（user/wolong/cron）受每日配额，超额直接 failed 并注明原因；
   续跑/复盘类（gate/retro）豁免——闸门任务不能因配额死掉（§4.5）。
5. 投递：source=cron 的任务完成即自动归档（reviewer=system 写 review）——
   订阅刷新不进待验收桶、不点红灯，也不准污染训练集（§4.7）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from ncds_opus_factory.common import cancel
from ncds_opus_factory.server.label_store import LabelStore
from ncds_opus_factory.server.schemas import Review, TaskMeta
from ncds_opus_factory.server.task_store import TaskStore

logger = logging.getLogger(__name__)

RunFn = Callable[..., dict[str, Any]]

# ---------------------------------------------------------------------------
# 并发与配额配置。env 用 JSON 覆盖单项：NOF_CONCURRENCY='{"shenkuo":1}'
# ---------------------------------------------------------------------------
_DEFAULT_CONCURRENCY: dict[str, int] = {
    "shenkuo": 2,    # TikHub 限流
    "wolong": 1,     # sclaude 账号池;且 round 状态文件需要串行写(P3)
    "liuyong": 3,    # scodex 子进程
    "guiguzi": 4,
    "_default": 4,
}
_DEFAULT_DAILY_QUOTA: dict[str, int] = {
    "wolong": 8,
    "shenkuo": 40,
    "_default": 100,
    # 独立配额桶(§4.5):cron 订阅刷新不与人发的深采抢额度;gate/retro 是
    # 流程控制任务,单独记账——不是无限豁免,这两个上限是失控链的最后刹车
    "_cron": 60,
    "_gate": 50,
    "_retro": 8,
}

# 真实任务 id 形态（task_store._new_task_id）；种子数据（t_demo_*/t_mock_*）
# 不匹配——启动恢复绝不能把演示任务拉起来真跑
_REAL_TASK_ID_RE = re.compile(r"^t_\d{12,}_[0-9a-f]{6,}$")

# 重启恢复期限：积压超过此时长的 pending/running 直接判 failed（可重发），
# 而不是悄悄重跑一个用户早就不要的任务
RECOVER_MAX_AGE_HOURS = float(os.environ.get("NOF_RECOVER_MAX_AGE_HOURS", "48"))


def _env_overrides(name: str, base: dict[str, int]) -> dict[str, int]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return dict(base)
    try:
        merged = dict(base)
        merged.update({str(k): int(v) for k, v in json.loads(raw).items()})
        return merged
    except (ValueError, TypeError):
        logger.warning("[TaskRunner] %s 解析失败,使用默认值: %r", name, raw)
        return dict(base)


CONCURRENCY = _env_overrides("NOF_CONCURRENCY", _DEFAULT_CONCURRENCY)
DAILY_QUOTA = _env_overrides("NOF_DAILY_QUOTA", _DEFAULT_DAILY_QUOTA)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class TaskRunner:
    """提交命令为后台任务：per-cmd 队列 + 并发额度 + 每日配额。"""

    def __init__(
        self,
        store: TaskStore,
        registry: dict[str, RunFn],
        labels: LabelStore | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.labels = labels
        self._queues: dict[str, asyncio.Queue[str]] = {}
        self._workers: list[asyncio.Task] = []
        self._started = False
        # 配额记账 {(YYYY-MM-DD, 桶key): count}；只留当天,翻天即弃
        self._quota_used: dict[tuple[str, str], int] = {}
        # 在途集合:出队 CAS 看不见还活着的旧工作线程,restore 必须问这里——
        # 「执行中取消→立刻恢复」否则会双线程跑同一任务
        self._inflight: set[str] = set()

    def list_commands(self) -> list[str]:
        return sorted(self.registry.keys())

    # ------------------------------------------------------------
    # 配置访问
    # ------------------------------------------------------------
    @staticmethod
    def concurrency_for(cmd: str) -> int:
        return max(1, CONCURRENCY.get(cmd, CONCURRENCY["_default"]))

    @staticmethod
    def _quota_key_and_limit(cmd: str, source: str | None) -> tuple[str, int]:
        """配额桶:派单类按 cmd 记账;cron/gate/retro 各有独立桶,互不抢。"""
        if source == "cron":
            return f"cron:{cmd}", max(0, DAILY_QUOTA["_cron"])
        if source == "gate":
            return f"gate:{cmd}", max(0, DAILY_QUOTA["_gate"])
        if source == "retro":
            return f"retro:{cmd}", max(0, DAILY_QUOTA["_retro"])
        return cmd, max(0, DAILY_QUOTA.get(cmd, DAILY_QUOTA["_default"]))

    def quota_remaining(self, cmd: str, source: str | None = None) -> int:
        """当日剩余配额（订阅 tick 等生产者在提交前自查,别制造注定失败的任务）。"""
        key, limit = self._quota_key_and_limit(cmd, source)
        return max(0, limit - self._quota_used.get((_today(), key), 0))

    def is_inflight(self, task_id: str) -> bool:
        """该任务是否仍有工作线程在执行（restore 据此拒绝过早恢复）。"""
        return task_id in self._inflight

    def _quota_take(self, key: str) -> None:
        full = (_today(), key)
        self._quota_used = {k: v for k, v in self._quota_used.items() if k[0] == full[0]}
        self._quota_used[full] = self._quota_used.get(full, 0) + 1

    # ------------------------------------------------------------
    # 提交 / 恢复入队
    # ------------------------------------------------------------
    async def submit(
        self,
        cmd: str,
        params: dict[str, Any],
        source: str | None = None,
        parent_task_id: str | None = None,
        round_id: str | None = None,
    ) -> str:
        if cmd not in self.registry:
            raise KeyError(f"unknown command: {cmd}")
        meta = self.store.create(
            cmd, params, source=source, parent_task_id=parent_task_id, round_id=round_id
        )
        # 配额闸门：超额 fail-fast（任务可见、可改天重发），不入队
        key, quota = self._quota_key_and_limit(cmd, source)
        if self._quota_used.get((_today(), key), 0) >= quota:
            msg = f"今日配额已用完(桶 {key}: {quota}/天),任务未入队;明天自动恢复或调大 NOF_DAILY_QUOTA"
            self.store.append_error(meta.task_id, msg)
            self.store.update_status(meta.task_id, "failed", error=msg)
            logger.warning("[TaskRunner] quota exceeded: bucket=%s task=%s", key, meta.task_id)
            return meta.task_id
        self._quota_take(key)
        self._enqueue(cmd, meta.task_id)
        return meta.task_id

    async def requeue(self, task_id: str, cmd: str) -> None:
        """已取消任务恢复后重新入队（沿用原 task_id，事件流续写）。

        与 submit 同走队列：恢复的任务同样受并发额度约束（不再绕过调度）。
        """
        self._enqueue(cmd, task_id)

    def _enqueue(self, cmd: str, task_id: str) -> None:
        if not self._started:
            # 嵌入方/测试没跑 recover_and_start 时别静默吞任务
            logger.warning("[TaskRunner] runner 未启动,任务 %s 将滞留队列直到 recover_and_start", task_id)
        self._queue(cmd).put_nowait(task_id)

    def _queue(self, cmd: str) -> asyncio.Queue[str]:
        if cmd not in self._queues:
            self._queues[cmd] = asyncio.Queue()
        return self._queues[cmd]

    # ------------------------------------------------------------
    # 启动：恢复积压 + 拉起 worker（必须在运行中的 event loop 上调用）
    # ------------------------------------------------------------
    def recover_and_start(self) -> int:
        """扫描 store 恢复积压，按额度拉起 worker。返回重新入队的任务数。"""
        if self._started:
            return 0
        self._started = True

        cutoff = datetime.now() - timedelta(hours=RECOVER_MAX_AGE_HOURS)
        today = _today()
        backlog: list[TaskMeta] = []
        for meta in self.store.list_tasks():
            try:
                if meta.cmd not in self.registry:
                    continue
                # 重建当天配额记账。配额拒绝的秒 failed 行(从未入队)不计——
                # 否则「调大配额+重启」这条逃生通道会被昨天的拒绝记录啃掉
                quota_rejected = (
                    meta.status == "failed"
                    and meta.started_at is None
                    and "配额" in (meta.error or "")
                )
                if meta.created_at.startswith(today) and not quota_rejected:
                    key, _ = self._quota_key_and_limit(meta.cmd, meta.source)
                    self._quota_used[(today, key)] = (
                        self._quota_used.get((today, key), 0) + 1
                    )
                # cron 任务自动归档的崩溃窗口兜底:终态却没 review 的补写 system 归档
                if (
                    meta.source == "cron"
                    and meta.status in ("completed", "failed")
                    and self.store.get_review(meta.task_id) is None
                ):
                    self._maybe_auto_archive(meta, self.store.get_result(meta.task_id))
                if meta.status not in ("pending", "running"):
                    continue
                # 种子/演示任务（t_demo_*/t_mock_*）不参与恢复——绝不能重启时真跑起来
                if not _REAL_TASK_ID_RE.match(meta.task_id):
                    continue
                created = datetime.fromisoformat(meta.created_at)
                if created < cutoff:
                    msg = f"服务重启恢复:积压超过 {RECOVER_MAX_AGE_HOURS:.0f}h,自动判失败(可重新发起)"
                    self.store.append_error(meta.task_id, msg)
                    self.store.update_status(meta.task_id, "failed", error=msg)
                    continue
                if meta.status == "running":
                    # 上次进程死在执行中：复位回 pending 重新入队
                    self.store.reset_for_requeue(meta.task_id)
                    self.store.append_progress(meta.task_id, "服务重启:上次执行中断,已重新入队")
                backlog.append(meta)
            except Exception:  # noqa: BLE001 — 单条坏数据跳过,恢复不能成为启动单点
                logger.exception("[TaskRunner] recover 跳过 %s", meta.task_id)

        # 旧的先跑（list_tasks 最新在前,反转即按 created_at 升序）
        for meta in reversed(backlog):
            self._enqueue(meta.cmd, meta.task_id)

        for cmd in self.registry:
            for i in range(self.concurrency_for(cmd)):
                self._workers.append(
                    asyncio.create_task(self._worker(cmd), name=f"worker-{cmd}-{i}")
                )
        logger.info(
            "[TaskRunner] started: %d workers, %d tasks recovered",
            len(self._workers), len(backlog),
        )
        return len(backlog)

    async def _worker(self, cmd: str) -> None:
        q = self._queue(cmd)
        while True:
            task_id = await q.get()
            try:
                await self._run(task_id, cmd)
            except Exception:  # noqa: BLE001 — 单任务异常不能杀 worker
                logger.exception("[TaskRunner] worker error: cmd=%s task=%s", cmd, task_id)
            finally:
                q.task_done()

    def _is_cancelled(self, task_id: str) -> bool:
        meta = self.store.get_meta(task_id)
        return bool(meta and meta.status == "cancelled")

    async def _run(self, task_id: str, cmd: str) -> None:
        # 出队 CAS：只有 pending 才接手（排队中被取消/restore 双入队的旧条目在此丢弃）。
        # get_meta 与 update_status 之间无 await,单事件循环上等效原子。
        meta = self.store.get_meta(task_id)
        if meta is None or meta.status != "pending":
            logger.info("[TaskRunner] task %s skipped at dequeue (status=%s)",
                        task_id, meta.status if meta else "missing")
            return
        run_fn = self.registry[cmd]
        self.store.update_status(task_id, "running")
        self._inflight.add(task_id)
        params = meta.params

        # on_progress 在工作线程里被同步调用；写文件 + flush 即可让 SSE tail 看到
        def on_progress(text: str) -> None:
            try:
                self.store.append_progress(task_id, text)
            except Exception as exc:  # 写文件失败不要影响主任务
                logger.warning("[TaskRunner] append_progress failed: %s", exc)

        try:
            # 把同步 run 推到默认线程池；不能让 subprocess 堵 event loop。
            # 同时安装协作式取消 checker:命令在步骤边界检查,长子进程被直接 SIGTERM。
            result = await asyncio.to_thread(
                _invoke,
                run_fn,
                params,
                on_progress,
                lambda: self._is_cancelled(task_id),
            )
            # 执行中被取消:工作线程无法强杀,跑完后结果作废,保持 cancelled
            if self._is_cancelled(task_id):
                logger.info("[TaskRunner] task %s finished after cancel, result discarded", task_id)
                return
            self.store.write_result(task_id, result)
            self.store.append_done(task_id, result)
            self.store.update_status(task_id, "completed")
            # 命令可选回传展示标题/副题(如沈括用作品标题替代任务卡上的分享链接)
            if isinstance(result, dict) and (result.get("task_title") or result.get("task_subtitle")):
                self.store.set_display(task_id, result.get("task_title"), result.get("task_subtitle"))
            self._maybe_auto_archive(meta, result)
            logger.info("[TaskRunner] task %s (%s) completed", task_id, cmd)
        except asyncio.CancelledError:
            # 优雅停服:worker 被 cancel。任务保持 running,交给下次启动的孤儿恢复;
            # 必须重抛——吞掉它 worker 会回到 q.get() 永久阻塞,进程退不出去
            try:
                self.store.append_progress(task_id, "服务停机,任务将在重启后自动恢复")
            except Exception:  # noqa: BLE001
                pass
            raise
        except BaseException as exc:  # noqa: BLE001 - 任何异常都需要记录
            if self._is_cancelled(task_id):
                logger.info("[TaskRunner] task %s errored after cancel, kept cancelled", task_id)
                return
            err_text = f"{type(exc).__name__}: {exc}"
            self.store.append_error(task_id, err_text)
            self.store.update_status(task_id, "failed", error=err_text)
            if meta.source == "cron":
                # cron 任务失败也自动归档:机器自闭环,失败行不准在收件箱点红灯
                self._maybe_auto_archive(meta, None)
            logger.exception("[TaskRunner] task %s (%s) failed", task_id, cmd)
        finally:
            self._inflight.discard(task_id)

    def _maybe_auto_archive(self, meta: TaskMeta, result: dict[str, Any] | None) -> None:
        """系统任务不准打扰 Leader（§4.7）：cron 任务完成即自动归档。

        reviewer=system 的 review 让 iOS 现有逻辑直接归档（不进待验收、不点红灯），
        案卷照写但复盘只学 reviewer=user 的样本——自动归档不污染训练集。
        """
        if meta.source != "cron":
            return
        review = Review(
            decision="approved",
            reviewed_at=datetime.now().isoformat(),
            reviewer="system",
        )
        try:
            self.store.write_review(meta.task_id, review)
            if self.labels is not None:
                fresh = self.store.get_meta(meta.task_id)
                if fresh is not None:
                    self.labels.write(fresh, review, result)
        except Exception:  # noqa: BLE001 — 自动归档失败不影响任务结果
            logger.exception("[TaskRunner] auto-archive failed: task=%s", meta.task_id)


def _invoke(
    run_fn: RunFn,
    params: dict[str, Any],
    on_progress: Callable[[str], None],
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """同步调用 run_fn(**params, on_progress=...)。

    抽成独立函数是为了 asyncio.to_thread 接受 callable + args 的形态；
    也方便测试时直接调用验证。cancel_check 装进线程局部,命令自行取用。
    """
    if cancel_check is not None:
        cancel.install(cancel_check)
    try:
        return run_fn(on_progress=on_progress, **params)
    finally:
        cancel.uninstall()
