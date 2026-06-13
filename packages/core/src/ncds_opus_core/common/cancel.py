"""协作式任务取消。

Python 线程无法强杀(没有 kill API;异步注入异常打不断阻塞中的 C 调用,
还可能留下锁/半写文件的脏状态)。本模块提供替代方案:
  - runner 在工作线程里 install() 一个 checker(读任务 meta 是否 cancelled)
  - 命令实现取 current() 后闭包传给自己的子线程,在步骤边界 checkpoint()
  - 长耗时子进程(Demucs/ffmpeg)用轮询式 Popen,取消时直接 SIGTERM 子进程
被取消的步骤抛 TaskCancelled;已产出的文件保留(幂等缓存,恢复后续跑)。
"""
from __future__ import annotations

import threading
from typing import Callable

_local = threading.local()

CheckFn = Callable[[], bool]


class TaskCancelled(RuntimeError):
    """任务被用户取消(协作式检查点抛出)。"""


def install(checker: CheckFn) -> None:
    _local.checker = checker


def uninstall() -> None:
    _local.checker = None


def current() -> CheckFn:
    """当前线程安装的 checker;没装则恒 False(CLI 直跑命令时不受影响)。

    注意 thread-local 不会传进命令自己开的线程池——在主线程取一次,
    再闭包传给支线。"""
    return getattr(_local, "checker", None) or (lambda: False)


def checkpoint(check: CheckFn | None = None) -> None:
    """检查点:已取消则抛 TaskCancelled。"""
    fn = check or current()
    if fn():
        raise TaskCancelled("cancelled by user")
