"""nof-worker：S3 决策 G 的唯一任务消费进程。

8810 退化为纯 producer+serve(步6)，本进程承载：唯一的 per-cmd BRPOP consumer
(recover_and_start 内部起) + 全部后台 loop + on_terminal 编排钩子。两进程各 import
state.py 各建一套指同一磁盘 + 各连同一 Redis。

启动命令（S3 步5 起可用，步6 前不要与 8810 同时跑）：
    nof-worker

环境变量开关（与 8810 一致）：
    NOF_SUBSCRIPTIONS=0   停用订阅传感器 loop
    NOF_RETRO=0           停用复盘触发器 loop
    NOF_PLANNER=0         停用排产策略 loop

⚠️ blocker#1(同 cmd 单 worker)：步5→步6 切换窗口内，**不要同时跑 8810 和 nof-worker**——
两个 recover_and_start 会各起 BRPOP consumer；Redis claim 能防同 task 双跑，但
wolong 等业务仍按单 worker 运行假设设计。步6 把 8810 的 consumer/loop 删掉后
才是稳态(8810 纯入队，worker 唯一消费)。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path

# launchd（S4）跑 worker 时不继承 shell env：必须在 import 任何读 os.environ 的模块之前
# 先加载仓库根 .env —— state.py 顶层读 REDIS_URL/NOF_STATE_DIR、commands/* 读各 API key。
# 与 app.py 同款（放最早处确保下游 import 全拿得到）；shell 已 export 时 override=False 不覆盖。
_REPO_ROOT = Path(__file__).resolve().parents[3]
try:
    from dotenv import load_dotenv  # python-dotenv 已装
    load_dotenv(_REPO_ROOT / ".env", override=False)
except ImportError:
    pass

from ncds_opus_factory.server import rounds_gate
from ncds_opus_factory.server.douk_sidecar import ensure_douk_sidecar, stop_douk_sidecar
from ncds_opus_factory.server.maintenance import _discard_sweeper, _round_reconciler
from ncds_opus_factory.server.queue import get_default_queue
from ncds_opus_factory.server.state import PIPELINE_RUNNER, RUNNER, STATE_DIR, STORE
from ncds_opus_factory.server.subscriptions import subscription_loop, subscriptions_path

logger = logging.getLogger(__name__)


async def _handle_terminal(meta, status, result) -> None:
    await rounds_gate.handle_terminal(meta, status, result)
    await PIPELINE_RUNNER.handle_pipeline_terminal(meta, status, result)


def _install_signal_handlers(stop_event: asyncio.Event) -> list[signal.Signals]:
    """把 SIGTERM/SIGINT 接到 stop_event。返回成功注册到 event loop 的信号列表。"""
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []

    def request_stop(sig: signal.Signals) -> None:
        if stop_event.is_set():
            return
        logger.info("[nof-worker] received %s, shutting down", sig.name)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_stop, sig)
            installed.append(sig)
        except (NotImplementedError, RuntimeError):
            # 非 Unix event loop 兜底；macOS/Linux launchd/systemd 走上面的 add_signal_handler。
            signal.signal(sig, lambda _n, _f, s=sig: loop.call_soon_threadsafe(request_stop, s))
    return installed


def _remove_signal_handlers(signals: list[signal.Signals]) -> None:
    loop = asyncio.get_running_loop()
    for sig in signals:
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.remove_signal_handler(sig)


async def _cancel_background_tasks(tasks: list[asyncio.Task]) -> None:
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _amain() -> None:
    stop_event = asyncio.Event()
    installed_signals = _install_signal_handlers(stop_event)
    background_tasks: list[asyncio.Task] = []
    douk_sidecar = None

    # 连 Redis 探活(决策 D：不静默吞任务)。startup ping 失败=fail-fast 退出，
    # 让 launchd/操作者立刻看到(运行期 Redis blip 由 BRPOP 指数退避兜，不在这退)。
    try:
        douk_sidecar = await ensure_douk_sidecar()
        ok = await get_default_queue().ping()
        if not ok:
            raise RuntimeError("[nof-worker] Redis ping failed; exiting (not silently dropping tasks)")
        logger.info("[nof-worker] ready. redis OK state_dir=%s commands=%s",
                    STATE_DIR, RUNNER.list_commands())

        # 先挂 on_terminal 钩子再 recover(别漏积压任务的终态事件)，与 app.py startup 一致。
        RUNNER.on_terminal = _handle_terminal
        # Redis 协调恢复 + 起 per-cmd BRPOP consumer（不清空 Redis 队列）
        recovered = await RUNNER.recover_and_start()
        if recovered:
            logger.info("[nof-worker] restart recovered: %d backlog tasks re-enqueued", recovered)

        background_tasks.append(asyncio.create_task(_discard_sweeper(), name="discard-sweeper"))
        background_tasks.append(asyncio.create_task(_round_reconciler(), name="round-reconciler"))
        # 订阅传感器(NOF_SUBSCRIPTIONS=0 停用;无订阅文件时空转)
        if os.environ.get("NOF_SUBSCRIPTIONS", "1") != "0":
            background_tasks.append(asyncio.create_task(
                subscription_loop(RUNNER, STORE, subscriptions_path(STATE_DIR)),
                name="subscription-loop",
            ))
        # 复盘触发器(P4,§8.3):每晚低峰投递 retro 段。NOF_RETRO=0 停用;样本不足时空转
        if os.environ.get("NOF_RETRO", "1") != "0":
            from ncds_opus_factory.server.retro_trigger import retro_loop
            background_tasks.append(asyncio.create_task(retro_loop(RUNNER, STORE), name="retro-loop"))
        # 排产策略(P5,§8.4):信号事件→深采→选题补货。NOF_PLANNER=0 停用;冷启动空转
        if os.environ.get("NOF_PLANNER", "1") != "0":
            from ncds_opus_factory.server.planner import planner_loop
            background_tasks.append(asyncio.create_task(planner_loop(RUNNER, STORE), name="planner-loop"))

        await stop_event.wait()
    finally:
        _remove_signal_handlers(installed_signals)
        logger.info("[nof-worker] stopping background loops")
        await _cancel_background_tasks(background_tasks)
        logger.info("[nof-worker] stopping task runner")
        try:
            await RUNNER.shutdown()
        finally:
            await stop_douk_sidecar(douk_sidecar)
        logger.info("[nof-worker] stopped")


def main() -> None:
    """`nof-worker` console_scripts 入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
