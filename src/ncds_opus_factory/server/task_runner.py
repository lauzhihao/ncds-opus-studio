"""异步任务执行器。

每个 command.run 都是「同步阻塞函数 + on_progress(text) 回调」的统一形态
（包括 vid 内部的 submit/poll 也封装在 run 里）。这里：

1. submit() 在 store 创建 task_id 并立刻返回，把真正执行放进 asyncio 任务。
2. _run() 在 asyncio.to_thread 里调 run（subprocess 阻塞放工作线程，不堵事件循环）。
3. on_progress 回调里同步写 events.jsonl（短事务，从工作线程写文件是安全的）。
4. 终态 success 写 done + result；异常写 error 并把异常文本带回 meta。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ncds_opus_factory.server.task_store import TaskStore

logger = logging.getLogger(__name__)

RunFn = Callable[..., dict[str, Any]]


class TaskRunner:
    """提交命令为后台 asyncio 任务，进度写文件。"""

    def __init__(
        self,
        store: TaskStore,
        registry: dict[str, RunFn],
    ) -> None:
        self.store = store
        self.registry = registry

    def list_commands(self) -> list[str]:
        return sorted(self.registry.keys())

    async def submit(self, cmd: str, params: dict[str, Any]) -> str:
        if cmd not in self.registry:
            raise KeyError(f"unknown command: {cmd}")
        meta = self.store.create(cmd, params)
        # fire-and-forget；不持引用是因为完成态全部通过文件读取
        asyncio.create_task(self._run(meta.task_id, cmd, params))
        return meta.task_id

    async def _run(self, task_id: str, cmd: str, params: dict[str, Any]) -> None:
        run_fn = self.registry[cmd]
        self.store.update_status(task_id, "running")

        # on_progress 在工作线程里被同步调用；写文件 + flush 即可让 SSE tail 看到
        def on_progress(text: str) -> None:
            try:
                self.store.append_progress(task_id, text)
            except Exception as exc:  # 写文件失败不要影响主任务
                logger.warning("[TaskRunner] append_progress failed: %s", exc)

        try:
            # 把同步 run 推到默认线程池；不能让 subprocess 堵 event loop
            result = await asyncio.to_thread(
                _invoke,
                run_fn,
                params,
                on_progress,
            )
            self.store.write_result(task_id, result)
            self.store.append_done(task_id, result)
            self.store.update_status(task_id, "completed")
            logger.info("[TaskRunner] task %s (%s) completed", task_id, cmd)
        except BaseException as exc:  # noqa: BLE001 - 任何异常都需要记录
            err_text = f"{type(exc).__name__}: {exc}"
            self.store.append_error(task_id, err_text)
            self.store.update_status(task_id, "failed", error=err_text)
            logger.exception("[TaskRunner] task %s (%s) failed", task_id, cmd)


def _invoke(
    run_fn: RunFn,
    params: dict[str, Any],
    on_progress: Callable[[str], None],
) -> dict[str, Any]:
    """同步调用 run_fn(**params, on_progress=...)。

    抽成独立函数是为了 asyncio.to_thread 接受 callable + args 的形态；
    也方便测试时直接调用验证。
    """
    return run_fn(on_progress=on_progress, **params)
