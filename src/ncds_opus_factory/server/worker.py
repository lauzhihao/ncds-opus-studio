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
两个 recover_and_start 会各 DEL 队列 + 各起 BRPOP consumer，同 cmd 两消费者踩 CAS。
步6 把 8810 的 consumer/loop 删掉后才是稳态(8810 纯入队，worker 唯一消费)。
"""

from __future__ import annotations

import asyncio
import logging
import os

from ncds_opus_factory.server import rounds_gate
from ncds_opus_factory.server.maintenance import _discard_sweeper, _round_reconciler
from ncds_opus_factory.server.queue import get_default_queue
from ncds_opus_factory.server.state import RUNNER, STATE_DIR, STORE
from ncds_opus_factory.server.subscriptions import subscription_loop, subscriptions_path

logger = logging.getLogger(__name__)


async def _amain() -> None:
    # 连 Redis 探活(决策 D：不静默吞任务)。startup ping 失败=fail-fast 退出，
    # 让 launchd/操作者立刻看到(运行期 Redis blip 由 BRPOP 指数退避兜，不在这退)。
    ok = await get_default_queue().ping()
    if not ok:
        raise RuntimeError("[nof-worker] Redis ping failed; exiting (not silently dropping tasks)")
    logger.info("[nof-worker] ready. redis OK state_dir=%s commands=%s",
                STATE_DIR, RUNNER.list_commands())

    # 先挂 on_terminal 钩子再 recover(别漏积压任务的终态事件)，与 app.py startup 一致。
    RUNNER.on_terminal = rounds_gate.handle_terminal
    # DEL 旧 List + disk-scan 全量重投 + 起 per-cmd BRPOP consumer
    recovered = await RUNNER.recover_and_start()
    if recovered:
        logger.info("[nof-worker] restart recovered: %d backlog tasks re-enqueued", recovered)

    asyncio.create_task(_discard_sweeper())
    asyncio.create_task(_round_reconciler())
    # 订阅传感器(NOF_SUBSCRIPTIONS=0 停用;无订阅文件时空转)
    if os.environ.get("NOF_SUBSCRIPTIONS", "1") != "0":
        asyncio.create_task(subscription_loop(RUNNER, STORE, subscriptions_path(STATE_DIR)))
    # 复盘触发器(P4,§8.3):每晚低峰投递 retro 段。NOF_RETRO=0 停用;样本不足时空转
    if os.environ.get("NOF_RETRO", "1") != "0":
        from ncds_opus_factory.server.retro_trigger import retro_loop
        asyncio.create_task(retro_loop(RUNNER, STORE))
    # 排产策略(P5,§8.4):信号事件→深采→选题补货。NOF_PLANNER=0 停用;冷启动空转
    if os.environ.get("NOF_PLANNER", "1") != "0":
        from ncds_opus_factory.server.planner import planner_loop
        asyncio.create_task(planner_loop(RUNNER, STORE))

    await asyncio.Event().wait()  # 持续运行，直到进程被终止


def main() -> None:
    """`nof-worker` console_scripts 入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
