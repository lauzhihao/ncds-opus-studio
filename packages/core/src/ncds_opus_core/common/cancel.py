"""协作式任务取消。

Python 线程无法强杀(没有 kill API;异步注入异常打不断阻塞中的 C 调用,
还可能留下锁/半写文件的脏状态)。本模块提供替代方案:
  - runner 在工作线程里 install() 一个 checker(读任务 meta 是否 cancelled)
  - 命令实现取 current() 后闭包传给自己的子线程,在步骤边界 checkpoint()
  - 长耗时子进程(Demucs/ffmpeg)用轮询式 Popen,取消时直接 SIGTERM 子进程
被取消的步骤抛 TaskCancelled;已产出的文件保留(幂等缓存,恢复后续跑)。

跨进程文件标记(S2)
------------------
文件标记 API 让取消信号可以跨进程/跨重启传递，不依赖内存：
  - set_flag(path)     写空文件(touch)，mkdir parent
  - is_flagged(path)   文件存在即 True
  - clear_flag(path)   unlink(missing_ok=True)，清除后重跑不受影响

调用方自行拼好 Path（core 不知道 video-jobs / job / node 概念）。
"""
from __future__ import annotations

import threading
from pathlib import Path
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


# ---------------------------------------------------------------------------
# 跨进程文件标记 API（S2）
# ---------------------------------------------------------------------------

def set_flag(flag_path: Path) -> None:
    """写取消标记文件（touch）。父目录不存在则自动创建。

    这是跨进程取消信号的"写端"——调用方（PipelineRunner.cancel_node）写,
    执行侧（watcher / checker）读。Core 不耦合 video-jobs 目录结构，
    调用方负责拼好绝对路径再传入。
    """
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.touch()


def is_flagged(flag_path: Path) -> bool:
    """文件存在即返回 True。无任何内存状态依赖，可在任意进程读。"""
    return flag_path.exists()


def clear_flag(flag_path: Path) -> None:
    """清除取消标记文件（幂等；文件不存在不报错）。

    节点执行完 CancelledError 分支后必须调此函数，否则下次重跑同节点会
    立即再次被 watcher 检测到并取消，造成"永远无法重跑"的 bug。
    """
    flag_path.unlink(missing_ok=True)
