"""单测：cancel 模块的跨进程文件标记 API（S2）。

覆盖：
1. set_flag / is_flagged / clear_flag 基本行为
2. clear_flag 对不存在的文件幂等（不报错）
3. 跨进程语义：进程 A 写标记、进程 B（新建对象/不共享内存）读到 True
   使用 multiprocessing 起第二进程，证明信号经文件传递、不靠内存。
"""
from __future__ import annotations

import multiprocessing
from pathlib import Path

from ncds_opus_core.common.cancel import (
    clear_flag,
    is_flagged,
    set_flag,
)


# ---------------------------------------------------------------------------
# 基本 API 行为
# ---------------------------------------------------------------------------

def test_set_flag_creates_file(tmp_path: Path) -> None:
    flag = tmp_path / "cancel" / "rw.flag"
    assert not flag.exists()
    set_flag(flag)
    assert flag.exists()


def test_is_flagged_true_when_file_exists(tmp_path: Path) -> None:
    flag = tmp_path / "cancel" / "tts.flag"
    assert not is_flagged(flag)
    set_flag(flag)
    assert is_flagged(flag)


def test_clear_flag_removes_file(tmp_path: Path) -> None:
    flag = tmp_path / "cancel" / "image.flag"
    set_flag(flag)
    assert is_flagged(flag)
    clear_flag(flag)
    assert not is_flagged(flag)


def test_clear_flag_idempotent_on_missing(tmp_path: Path) -> None:
    """clear 不存在的 flag 不应抛异常（幂等）。"""
    flag = tmp_path / "cancel" / "render.flag"
    assert not flag.exists()
    clear_flag(flag)  # 不报错
    assert not flag.exists()


def test_set_flag_auto_creates_parent(tmp_path: Path) -> None:
    """父目录不存在时 set_flag 应自动创建，不能要求调用方先 mkdir。"""
    nested = tmp_path / "a" / "b" / "c" / "x.flag"
    set_flag(nested)
    assert nested.exists()


# ---------------------------------------------------------------------------
# 跨进程语义（multiprocessing）
# ---------------------------------------------------------------------------

def _subprocess_is_flagged(flag_path_str: str, result_queue: "multiprocessing.Queue[bool]") -> None:
    """在子进程里调 is_flagged，把结果放入 queue。

    关键：子进程与父进程不共享任何内存状态，is_flagged 必须经文件系统读。
    """
    from ncds_opus_core.common.cancel import is_flagged as _is_flagged
    result_queue.put(_is_flagged(Path(flag_path_str)))


def test_cross_process_flag_visibility(tmp_path: Path) -> None:
    """进程 A（当前进程）写标记，进程 B（子进程）读到 True。

    验证取消信号经文件传递，不依赖内存共享（S2 的跨进程语义核心）。
    """
    flag = tmp_path / "cancel" / "asr.flag"

    # 进程 A：写标记
    set_flag(flag)
    assert is_flagged(flag), "写完本进程就应该能读到"

    # 进程 B：新进程，不共享父进程的任何内存
    ctx = multiprocessing.get_context("spawn")  # spawn 确保不继承父进程内存镜像
    queue: multiprocessing.Queue[bool] = ctx.Queue()  # type: ignore[type-arg]
    proc = ctx.Process(target=_subprocess_is_flagged, args=(str(flag), queue))
    proc.start()
    proc.join(timeout=10)
    assert proc.exitcode == 0, f"子进程异常退出: {proc.exitcode}"
    result = queue.get(timeout=5)
    assert result is True, "子进程应通过文件系统读到 flag=True（跨进程语义验证）"


def test_cross_process_no_flag(tmp_path: Path) -> None:
    """对照：未写标记时子进程读到 False。"""
    flag = tmp_path / "cancel" / "rw.flag"
    assert not flag.exists()

    ctx = multiprocessing.get_context("spawn")
    queue: multiprocessing.Queue[bool] = ctx.Queue()  # type: ignore[type-arg]
    proc = ctx.Process(target=_subprocess_is_flagged, args=(str(flag), queue))
    proc.start()
    proc.join(timeout=10)
    assert proc.exitcode == 0
    result = queue.get(timeout=5)
    assert result is False, "未写标记子进程应读到 False"
