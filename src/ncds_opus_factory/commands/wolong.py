"""/wolong —— 卧龙先生：抖音认知内容工厂的 CEO/操盘手（HTTP 包装层）。

卧龙本体是 opus(sclaude) headless 编排器：脑子在 scripts/wolong_sop.md，启动器在
scripts/run_wolong.sh。它自主编排 鬼谷子(选题) -> 柳永(成稿+质检)，把待验收清单落到
state/benchmark/review/。全程走 Claude 订阅(sclaude 账号池)，不碰 API。

本模块只做一件事：把 shell 启动器包装成和其它 command 一致的
``run(...on_progress) -> dict`` 契约，让 server.task_runner 能像普通命令一样异步拉起它，
把 opus 的 stdout 逐行喂给 on_progress（-> SSE），完成后把待验收目录带回。

注意：run() 会拉起 opus(Claude Max 订阅)，属"重"任务、可能跑数分钟；deadline 到点会 kill。
"""

from __future__ import annotations

import argparse
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "run_wolong.sh"
REVIEW_DIR = ROOT / "state" / "benchmark" / "review"

DEFAULT_TIMEOUT_SECONDS = 1800  # opus 全链路编排，30 分钟封顶

ProgressFn = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def run(
    count: int = 3,
    benchmark_path: str = "",
    avoid: str = "",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """拉起卧龙(opus headless)自主编排一轮内容生产。

    Args:
        count: 本轮产出条数（run_wolong.sh 的位置参 1）。
        benchmark_path: 对标数据 all_posts.json 路径；空串 = 用脚本内置默认。
        avoid: 已发选题(逗号分隔)；空串 = 用脚本内置默认。
    返回 {count, review_dir, returncode, tail}（tail = 最后若干行输出，便于排查）。
    """
    if not SCRIPT.is_file():
        raise FileNotFoundError(f"缺启动器: {SCRIPT}")

    # run_wolong.sh 用 ${2:-默认} / ${3:-默认}，传空串会触发默认值，所以可安全传 ""
    cmd = ["bash", str(SCRIPT), str(count), benchmark_path or "", avoid or ""]
    on_progress(f"wolong start: count={count} (opus headless orchestration, may take minutes)")

    # 二进制 + 非阻塞读：select 配 deadline，子进程即便卡死(无输出)也能按时 kill
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        env=os.environ.copy(),
    )
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)

    deadline = time.monotonic() + timeout_seconds
    tail: list[str] = []
    buf = b""

    def _emit(raw: bytes) -> None:
        line = raw.decode("utf-8", "replace").rstrip()
        if not line:
            return
        tail.append(line)
        if len(tail) > 40:
            del tail[0]
        on_progress(line[:500])

    try:
        while True:
            if time.monotonic() > deadline:
                proc.kill()
                raise TimeoutError(f"wolong timeout (>{timeout_seconds}s), opus killed")
            rlist, _, _ = select.select([fd], [], [], 1.0)
            if rlist:
                chunk = os.read(fd, 65536)
                if chunk == b"":  # EOF
                    if proc.poll() is not None:
                        break
                    continue
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for lb in lines:
                    _emit(lb)
            elif proc.poll() is not None:
                break
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()

    if buf.strip():
        _emit(buf)

    code = proc.returncode
    on_progress(f"wolong done: returncode={code}")
    if code != 0:
        raise RuntimeError(f"wolong (run_wolong.sh) failed code={code}: {' | '.join(tail[-5:])}")
    return {
        "count": count,
        "review_dir": str(REVIEW_DIR),
        "returncode": code,
        "tail": tail[-20:],
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof wolong", description="卧龙先生: 内容工厂操盘手(opus 编排)")
    parser.add_argument("--count", type=int, default=3, help="本轮产出条数")
    parser.add_argument("--benchmark", default="", help="对标数据 all_posts.json 路径(空=脚本默认)")
    parser.add_argument("--avoid", default="", help="已发选题,逗号分隔(空=脚本默认)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        count=args.count,
        benchmark_path=args.benchmark,
        avoid=args.avoid,
        timeout_seconds=args.timeout,
        on_progress=on_progress,
    )
    print(f"review_dir={result['review_dir']} returncode={result['returncode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
