"""异步任务执行器：接收 → 入队（per-cmd 额度）→ 执行 → 投递 → 反馈。

每个 command.run 都是「同步阻塞函数 + on_progress(text) 回调」的统一形态，
执行仍在 asyncio.to_thread 的工作线程里跑。P2 调度化（docs/WOLONG-DESIGN.md §2）：

1. submit()/requeue() 统一登记 Redis task hash + 投 per-cmd Redis List 队列，
   server 只生产，worker 只消费。
2. worker 出队后通过 Redis WATCH/MULTI 做 pending→running claim；排队中取消、
   cancel→restore 双入队、重复 List 条目都不会双跑。
3. 启动恢复：Redis 是执行协调真相源。worker 不再 DEL 队列；只补登记旧磁盘任务，
   并把 Redis 中 pending/running 的未终态任务补投给队列。磁盘保留为 UI/SSE
   持久视图，不再作为清空 Redis 后重建队列的依据。
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

from ncds_opus_core.common import cancel
from ncds_opus_factory.server.label_store import LabelStore
from ncds_opus_factory.server.queue import RedisQueue, get_default_queue
from ncds_opus_factory.server.schemas import Review, TaskMeta
from ncds_opus_factory.server.task_store import TaskStore
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

RunFn = Callable[..., dict[str, Any]]


class EnqueueUnavailable(RuntimeError):
    """Redis 入队失败时抛出，携带已创建的 task_id 供调用方返回 503 + task_id。

    语义：meta 已置 failed，task_id 可追踪；调用方（routes/tasks.py）需将
    HTTP 状态码改为 503 并透出 task_id，让前端得知任务已创建但无法调度。
    """

    def __init__(self, task_id: str) -> None:
        super().__init__(f"enqueue failed: redis unavailable (task_id={task_id})")
        self.task_id = task_id


# ---------------------------------------------------------------------------
# 并发与配额配置。env 用 JSON 覆盖单项：NOF_CONCURRENCY='{"shenkuo":1}'
# ---------------------------------------------------------------------------
_DEFAULT_CONCURRENCY: dict[str, int] = {
    "shenkuo": 2,    # TikHub 限流
    "wolong": 1,     # sclaude 账号池;且 round 状态文件需要串行写(P3)
    "liuyong": 3,    # scodex 子进程
    "guiguzi": 4,
    "wst": 5,        # 文生图
    "tst": 5,        # 图生图
    "pipeline_node": 2,  # web 画布节点；image 内部自带并发，外层不要放太开
    "_default": 4,
}
_DEFAULT_DAILY_QUOTA: dict[str, int] = {
    "wolong": 8,
    "shenkuo": 40,
    "pipeline_node": 500,
    "_default": 100,
    # 独立配额桶(§4.5):cron 订阅刷新不与人发的深采抢额度;gate/retro 是
    # 流程控制任务,单独记账——不是无限豁免,这两个上限是失控链的最后刹车
    "_cron": 60,
    "_gate": 50,
    "_retro": 8,
}

# round 内不设人工闸的阶段(§4.6):完成即自动归档,机器反馈推动 round。
# 闸1 在柳永(脚本验收),闸2 在终验;吴道子/伯牙接入后加进来
_UNGATED_ROUND_CMDS = {"guiguzi"}

# 真实任务 id 形态（task_store._new_task_id 当前为 <cmd>_<ms><hex8>；兼容旧
# t_<ms>_<hex>）。种子数据（t_demo_*/t_mock_*）不匹配，启动恢复绝不能把演示任务拉起来真跑。
_RECOVERABLE_TASK_ID_RE = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9_-]*_\d{12,}[0-9a-f]{8}|t_\d{12,}_[0-9a-f]{6,})$"
)
_SEED_TASK_PREFIXES = ("t_demo_", "t_mock_")

# 重启恢复期限：积压超过此时长的 pending/running 直接判 failed（可重发），
# 而不是悄悄重跑一个用户早就不要的任务
RECOVER_MAX_AGE_HOURS = float(os.environ.get("NOF_RECOVER_MAX_AGE_HOURS", "48"))
WORKER_SHUTDOWN_GRACE_SEC = float(os.environ.get("NOF_WORKER_SHUTDOWN_GRACE_SEC", "20"))


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
        redis_queue: "RedisQueue | None" = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.labels = labels
        # 配额、队列、执行协调状态都走 Redis；磁盘 TaskStore 是展示/审计镜像。
        self._rq = redis_queue or get_default_queue()
        self._workers: list[asyncio.Task] = []
        self._started = False
        self._shutdown_requested = False
        # 终态钩子(round 事件接线,§4.2):app startup 注入 rounds_gate.handle_terminal,
        # 不在此 import——避免 server 子模块循环依赖
        self.on_terminal: Callable[..., Any] | None = None
        # 在途集合和任务状态均在 Redis 中跨进程可见，见 queue.py。

    def list_commands(self) -> list[str]:
        return sorted(self.registry.keys())

    # ------------------------------------------------------------
    # 配置访问
    # ------------------------------------------------------------
    @staticmethod
    def concurrency_for(cmd: str) -> int:
        if cmd == "wolong":
            # 硬钳 1,env 也不放行:rounds_gate 的"查在途段→submit"原子性和
            # round 文件的串行写都建立在卧龙段不真并发之上
            return 1
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

    async def quota_remaining(self, cmd: str, source: str | None = None) -> int:
        """当日剩余配额软预查（精确闸在 _run 的 incr_quota 原子判，此处仅供 loop 提前感知）。"""
        key, limit = self._quota_key_and_limit(cmd, source)
        used = await self._rq.get_quota(key, _today())
        return max(0, limit - used)

    async def is_inflight(self, task_id: str) -> bool:
        """该任务是否仍有工作线程在执行（restore 据此拒绝过早恢复）。

        S3 步7：改用 Redis SISMEMBER（跨进程可见）替代原内存 set，
        8810 restore 路由和 nof-worker 执行进程各读各写同一 Redis Key。
        """
        return await self._rq.is_inflight(task_id)

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
        intent_key: str | None = None,
    ) -> str:
        if cmd not in self.registry:
            raise KeyError(f"unknown command: {cmd}")
        # 派发幂等(§4.1):同 (round_id, intent_key) 已有任务直接返回既有 id——
        # 卧龙段崩在"派发后、回填前"时,重发不产生重复任务
        if round_id and intent_key:
            for m in self.store.list_tasks():
                if m.round_id == round_id and m.intent_key == intent_key:
                    logger.info("[TaskRunner] intent 命中既有任务: %s/%s -> %s",
                                round_id, intent_key, m.task_id)
                    return m.task_id
        meta = self.store.create(
            cmd, params, source=source, parent_task_id=parent_task_id,
            round_id=round_id, intent_key=intent_key,
        )
        # 配额判定移到 _run 出队侧（Redis incr_quota 原子判，跨进程不翻倍）。
        # 顺序约束：dedup 扫 store(同步) → store.create(同步) → Redis 登记 → 最后才 lpush。
        # 严禁把 await 挪到 create 之前——planner/rounds_gate「检查→submit」
        # 两次并发 maybe_resume 在同一 event loop 上仍协作式串行，
        # 第一次 create 落盘后第二次扫 store 能看到，从而防卧龙双投。
        try:
            await self._rq.register_task(
                meta.task_id,
                meta.cmd,
                status="pending",
                source=meta.source,
                created_at=meta.created_at,
                round_id=meta.round_id,
                parent_task_id=meta.parent_task_id,
                intent_key=meta.intent_key,
            )
            await self._enqueue(cmd, meta.task_id)
        except (RedisConnectionError, RedisError) as exc:
            # lpush 连不上 Redis：决策 D——将 meta 标为 failed，避免留下幽灵 pending 任务。
            # 不静默吞异常：抛 EnqueueUnavailable 让路由层返回 503+task_id（前端可追踪）。
            err_msg = f"enqueue failed: redis unavailable ({exc})"
            self.store.update_status(meta.task_id, "failed", error=err_msg)
            try:
                await self._rq.mark_task_status(meta.task_id, "failed", error=err_msg)
            except Exception:  # noqa: BLE001
                pass
            logger.error("[TaskRunner] submit failed, task marked failed: %s (%s)", meta.task_id, exc)
            raise EnqueueUnavailable(meta.task_id) from exc
        return meta.task_id

    async def requeue(self, task_id: str, cmd: str) -> None:
        """已取消任务恢复后重新入队（沿用原 task_id，事件流续写）。

        与 submit 同走队列：恢复的任务同样受并发额度约束（不再绕过调度）。
        """
        try:
            meta = self.store.get_meta(task_id)
            if meta is not None:
                await self._rq.register_task(
                    task_id,
                    cmd,
                    status="pending",
                    source=meta.source,
                    created_at=meta.created_at,
                    round_id=meta.round_id,
                    parent_task_id=meta.parent_task_id,
                    intent_key=meta.intent_key,
                )
            else:
                await self._rq.register_task(task_id, cmd, status="pending")
            await self._enqueue(cmd, task_id)
        except (RedisConnectionError, RedisError) as exc:
            # restore 时 redis 挂：同样置 failed 防幽灵，并向上抛告知调用方失败原因。
            err_msg = f"requeue failed: redis unavailable ({exc})"
            self.store.update_status(task_id, "failed", error=err_msg)
            try:
                await self._rq.mark_task_status(task_id, "failed", error=err_msg)
            except Exception:  # noqa: BLE001
                pass
            logger.error("[TaskRunner] requeue failed, task marked failed: %s (%s)", task_id, exc)
            raise EnqueueUnavailable(task_id) from exc

    async def cancel_task(self, task_id: str) -> None:
        """把取消写入 Redis 协调状态，阻止 queued duplicate 后续被 worker claim。"""
        await self._rq.mark_task_status(task_id, "cancelled")

    async def _enqueue(self, cmd: str, task_id: str) -> None:
        """LPUSH 入 Redis List（左进 BRPOP 右出 = FIFO，旧的先跑）。

        重要顺序约束（S3 决策 E 死约束）：
        submit 体内必须先同步完成 dedup 扫 store + store.create，
        最后才 await _enqueue(lpush)。
        绝不把 await 挪到 create 之前——planner/rounds_gate 的「检查→submit」
        之间无 await 的协作式原子性（防卧龙双投）依赖 create 先于 lpush 落盘。
        """
        if not self._started:
            # nof-server 是 producer-only：本地 runner 不启动也会正常 LPUSH 给 nof-worker。
            logger.info("[TaskRunner] producer-only enqueue: %s", task_id)
        await self._rq.lpush(cmd, task_id)

    # ------------------------------------------------------------
    # 启动：恢复积压 + 拉起 worker（必须在运行中的 event loop 上调用）
    # ------------------------------------------------------------
    async def recover_and_start(self) -> int:
        """恢复未完成任务并拉起 worker。返回补投任务数。

        Redis 是执行协调真相源，worker 启动绝不能清空队列：
        1. 扫描磁盘 TaskStore 只做兼容补登记、终态镜像和旧任务救援。
        2. Redis 中 pending/running 视为未完成任务；running 是上个 worker 的孤儿，
           复位为 pending 后补投队列。
        3. 队列里可能已有旧条目，补投导致的重复条目由 claim_task 原子跳过。
        4. 最后起 per-cmd BRPOP worker。
        """
        if self._started:
            return 0
        self._started = True
        self._shutdown_requested = False

        cutoff = datetime.now() - timedelta(hours=RECOVER_MAX_AGE_HOURS)
        backlog: list[TaskMeta] = []
        for meta in self.store.list_tasks():
            try:
                if meta.cmd not in self.registry:
                    continue
                # 配额重建段已删除（S3 步2）：配额真相源改 Redis，重启时 Redis 计数天然保留，
                # 无需从 store 重扫重建内存计数。
                # 系统任务自动归档的崩溃窗口兜底:终态却没 review 的补写 system 归档
                # (适用范围由 _maybe_auto_archive 自己判定:cron/卧龙段/无闸阶段)
                if (
                    meta.status in ("completed", "failed")
                    and self.store.get_review(meta.task_id) is None
                ):
                    result = self.store.get_result(meta.task_id)
                    # 派单段崩在 set_round 回填前的窄窗口:先补回填再归档
                    if (
                        meta.cmd == "wolong"
                        and meta.round_id is None
                        and isinstance(result, dict)
                        and result.get("round_id")
                    ):
                        refreshed = self.store.set_round(meta.task_id, str(result["round_id"]))
                        if refreshed is not None:
                            meta = refreshed
                    self._maybe_auto_archive(meta, result)
                if meta.status in ("completed", "failed", "cancelled"):
                    await self._rq.register_task(
                        meta.task_id,
                        meta.cmd,
                        status=meta.status,
                        source=meta.source,
                        created_at=meta.created_at,
                        round_id=meta.round_id,
                        parent_task_id=meta.parent_task_id,
                        intent_key=meta.intent_key,
                    )
                    continue
                # 种子/演示任务（t_demo_*/t_mock_*）不参与恢复——绝不能重启时真跑起来
                if (
                    meta.task_id.startswith(_SEED_TASK_PREFIXES)
                    or not _RECOVERABLE_TASK_ID_RE.match(meta.task_id)
                ):
                    continue
                redis_status = await self._rq.task_status(meta.task_id)
                if redis_status in ("completed", "failed", "cancelled"):
                    # Redis 已有终态时不允许旧磁盘状态把任务重新投出去。
                    continue
                created = datetime.fromisoformat(meta.created_at)
                if created < cutoff:
                    msg = f"服务重启恢复:积压超过 {RECOVER_MAX_AGE_HOURS:.0f}h,自动判失败(可重新发起)"
                    self.store.append_error(meta.task_id, msg)
                    self.store.update_status(meta.task_id, "failed", error=msg)
                    await self._rq.register_task(
                        meta.task_id,
                        meta.cmd,
                        status="failed",
                        source=meta.source,
                        created_at=meta.created_at,
                        round_id=meta.round_id,
                        parent_task_id=meta.parent_task_id,
                        intent_key=meta.intent_key,
                    )
                    continue
                if meta.status == "running":
                    # 上次进程死在执行中：复位回 pending 重新入队
                    self.store.reset_for_requeue(meta.task_id)
                    self.store.append_progress(meta.task_id, "服务重启:上次执行中断,已重新入队")
                    await self._rq.remove_inflight(meta.task_id)
                elif redis_status == "running":
                    self.store.append_progress(meta.task_id, "服务重启:上次领取中断,已重新入队")
                    await self._rq.remove_inflight(meta.task_id)
                await self._rq.register_task(
                    meta.task_id,
                    meta.cmd,
                    status="pending",
                    source=meta.source,
                    created_at=meta.created_at,
                    round_id=meta.round_id,
                    parent_task_id=meta.parent_task_id,
                    intent_key=meta.intent_key,
                )
                backlog.append(meta)
            except Exception:  # noqa: BLE001 — 单条坏数据跳过,恢复不能成为启动单点
                logger.exception("[TaskRunner] recover 跳过 %s", meta.task_id)

        # Redis index 里存在、但磁盘扫描没覆盖的未完成任务也要补投。正常路径下
        # 它们应该都有磁盘 meta；这里主要防止 worker 重启时只靠 List 残留而无人补投。
        seen = {m.task_id for m in backlog}
        for task_id in await self._rq.list_task_ids():
            if task_id in seen:
                continue
            info = await self._rq.task_info(task_id)
            cmd = info.get("cmd")
            status = info.get("status")
            if cmd not in self.registry or status not in ("pending", "running"):
                continue
            meta = self.store.get_meta(task_id)
            if meta is None:
                await self._rq.mark_task_status(task_id, "failed", error="task meta missing on worker recover")
                continue
            if status == "running":
                if meta.status == "running":
                    self.store.reset_for_requeue(task_id)
                    self.store.append_progress(task_id, "服务重启:上次执行中断,已重新入队")
                await self._rq.mark_task_status(task_id, "pending")
                await self._rq.remove_inflight(task_id)
            backlog.append(meta)
            seen.add(task_id)

        # 旧的先跑（list_tasks 最新在前,反转即按 created_at 升序）。
        # 不清旧 Redis List；重复条目由 claim_task 防双跑。
        for meta in reversed(backlog):
            await self._enqueue(meta.cmd, meta.task_id)

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

    async def shutdown(self, grace_seconds: float = WORKER_SHUTDOWN_GRACE_SEC) -> None:
        """请求 worker 优雅停机，并等待当前任务通过 cancel checker 协作式自停。

        不把任务标成 ``cancelled``：这是进程停机，不是用户撤销。若任务在停机期间中止，
        meta 会保持 ``running``，下次 ``recover_and_start()`` 把孤儿 running 复位后重投。
        """
        self._shutdown_requested = True
        if self._workers:
            done, pending = await asyncio.wait(self._workers, timeout=max(0.0, grace_seconds))
            if pending:
                logger.warning(
                    "[TaskRunner] shutdown grace %.1fs expired; cancelling %d worker tasks",
                    grace_seconds, len(pending),
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    logger.exception("[TaskRunner] worker exited with error during shutdown")
        self._workers.clear()
        self._started = False
        await self._rq.aclose()

    async def _worker(self, cmd: str) -> None:
        """从 Redis List BRPOP 取任务执行。

        BRPOP timeout=5 超时返回 None，循环重试（不阻塞 event loop，
        每次 brpop 等待期间 event loop 可以处理其他协程）。
        ConnectionError 由 brpop 内部退避重连，此处只需 continue。
        """
        while not self._shutdown_requested:
            task_id = await self._rq.brpop(cmd, timeout=5)
            if task_id is None:
                # 超时或退避期间返回 None，继续轮询
                continue
            try:
                await self._run(task_id, cmd)
            except Exception:  # noqa: BLE001 — 单任务异常不能杀 worker
                logger.exception("[TaskRunner] worker error: cmd=%s task=%s", cmd, task_id)

    def _is_cancelled(self, task_id: str) -> bool:
        meta = self.store.get_meta(task_id)
        return bool(meta and meta.status == "cancelled")

    async def _run(self, task_id: str, cmd: str) -> None:
        # 出队 CAS：只有 Redis status=pending 才接手。磁盘状态只作为展示镜像和
        # cancel checker 的输入，不再承担跨进程领取原子性。
        meta = self.store.get_meta(task_id)
        if meta is None or meta.status != "pending":
            if meta is not None and meta.status in ("completed", "failed", "cancelled"):
                await self._rq.mark_task_status(task_id, meta.status, error=meta.error)
            logger.info("[TaskRunner] task %s skipped at dequeue (status=%s)",
                        task_id, meta.status if meta else "missing")
            return
        if not await self._rq.claim_task(task_id):
            redis_status = await self._rq.task_status(task_id)
            logger.info("[TaskRunner] task %s skipped by redis claim (status=%s)",
                        task_id, redis_status or "missing")
            return
        # Redis 配额原子判定（S3 步2 决策 B）：判定收口到 worker 出队侧，跨进程不翻倍。
        # 位置：出队 CAS 之后、置 running 之前；语义等价原 submit 闸门，但时机变成「出队时」。
        run_fn = self.registry[cmd]
        key, limit = self._quota_key_and_limit(cmd, meta.source)
        _n, ok = await self._rq.incr_quota(key, _today(), limit)
        if not ok:
            msg = (
                f"今日配额已用完(桶 {key}: {limit}/天),任务未执行;"
                f"明天自动恢复或调大 NOF_DAILY_QUOTA"
            )
            self.store.append_error(task_id, msg)
            self.store.update_status(task_id, "failed", error=msg)
            await self._rq.mark_task_status(task_id, "failed", error=msg)
            # 系统任务被配额拒绝也自动归档，不准在收件箱点红灯
            self._maybe_auto_archive(meta, None)
            logger.warning("[TaskRunner] quota exceeded at dequeue: bucket=%s task=%s", key, task_id)
            return
        self.store.update_status(task_id, "running")
        params = meta.params
        # 卧龙派单段:注入任务 id 让 round_id 确定化(round_<task_id>)——
        # 重启重跑同一任务收敛到同一个 round,绝不双倍生产
        if cmd == "wolong" and not params.get("resume") and not params.get("mode"):
            params = {**params, "_dispatch_task_id": task_id}

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
                lambda: self._is_cancelled(task_id) or self._shutdown_requested,
            )
            # 执行中被取消:工作线程无法强杀,跑完后结果作废,保持 cancelled
            if self._is_cancelled(task_id):
                logger.info("[TaskRunner] task %s finished after cancel, result discarded", task_id)
                await self._rq.mark_task_status(task_id, "cancelled")
                return
            self.store.write_result(task_id, result)
            self.store.append_done(task_id, result)
            self.store.update_status(task_id, "completed")
            await self._rq.mark_task_status(task_id, "completed")
            # 命令可选回传展示标题/副题(如沈括用作品标题替代任务卡上的分享链接)
            if isinstance(result, dict) and (result.get("task_title") or result.get("task_subtitle")):
                self.store.set_display(task_id, result.get("task_title"), result.get("task_subtitle"))
            # 卧龙派单段:round 在 run() 内部才生成,完成时回填 round_id 到 meta,
            # 让自动归档判定生效(开盘卡不该在收件箱点红灯)
            if (
                isinstance(result, dict)
                and result.get("round_id")
                and meta.round_id is None
                and meta.cmd == "wolong"
            ):
                refreshed = self.store.set_round(task_id, str(result["round_id"]))
                if refreshed is not None:
                    meta = refreshed
            self._maybe_auto_archive(meta, result)
            await self._fire_terminal(meta, "completed", result)
            logger.info("[TaskRunner] task %s (%s) completed", task_id, cmd)
        except asyncio.CancelledError:
            # 优雅停服:worker 被 cancel。任务保持 running,交给下次启动的孤儿恢复;
            # 必须重抛——吞掉它 worker 会回到 q.get() 永久阻塞,进程退不出去
            try:
                self.store.append_progress(task_id, "服务停机,任务将在重启后自动恢复")
            except Exception:  # noqa: BLE001
                pass
            raise
        except cancel.TaskCancelled:
            if self._is_cancelled(task_id):
                logger.info("[TaskRunner] task %s stopped after user cancel", task_id)
                await self._rq.mark_task_status(task_id, "cancelled")
                return
            if self._shutdown_requested:
                try:
                    self.store.append_progress(task_id, "服务停机,任务将在重启后自动恢复")
                except Exception:  # noqa: BLE001
                    pass
                logger.info("[TaskRunner] task %s (%s) stopped for worker shutdown", task_id, cmd)
                return
            raise
        except BaseException as exc:  # noqa: BLE001 - 任何异常都需要记录
            if self._is_cancelled(task_id):
                logger.info("[TaskRunner] task %s errored after cancel, kept cancelled", task_id)
                await self._rq.mark_task_status(task_id, "cancelled")
                return
            err_text = f"{type(exc).__name__}: {exc}"
            self.store.append_error(task_id, err_text)
            self.store.update_status(task_id, "failed", error=err_text)
            await self._rq.mark_task_status(task_id, "failed", error=err_text)
            # cron/round 系统任务失败也自动归档:机器自闭环,失败行不准点红灯
            self._maybe_auto_archive(meta, None)
            await self._fire_terminal(meta, "failed", None)
            logger.exception("[TaskRunner] task %s (%s) failed", task_id, cmd)
        finally:
            # S3 步7：改 Redis SREM，对称 add_inflight
            await self._rq.remove_inflight(task_id)

    async def _fire_terminal(self, meta: TaskMeta, status: str, result: dict[str, Any] | None) -> None:
        """机器反馈(§4.2):带 round_id 的任务终态通知编排层。失败不影响任务本身。"""
        if self.on_terminal is None or not meta.round_id:
            return
        try:
            await self.on_terminal(meta, status, result)
        except Exception:  # noqa: BLE001
            logger.exception("[TaskRunner] on_terminal hook failed: task=%s", meta.task_id)

    def _maybe_auto_archive(self, meta: TaskMeta, result: dict[str, Any] | None) -> None:
        """系统任务不准打扰 Leader（§4.7）：机器自闭环的任务完成/失败即自动归档。

        适用:cron 订阅刷新、复盘段(source=retro,每晚自动投递,§8.3)、round 内的
        卧龙段(派单/续跑)、round 内无闸阶段任务(鬼谷子选题——闸1 在柳永)。
        reviewer=system 的 review 让 iOS 现有逻辑直接归档(不进待验收、不点红灯),
        案卷照写但复盘只学 reviewer=user 的样本。
        """
        ungated_round = bool(meta.round_id) and (
            meta.cmd == "wolong" or meta.cmd in _UNGATED_ROUND_CMDS
        )
        if meta.source not in ("cron", "retro") and not ungated_round:
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
