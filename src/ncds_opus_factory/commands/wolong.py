"""/wolong —— 卧龙先生：抖音认知内容工厂的 CEO/操盘手。

P3 起默认是**分段编排**（docs/WOLONG-DESIGN.md §4）：
- 派单段（无 round_id）：建 round、派鬼谷子选题,然后退出;
- 续跑段（round_id + resume）：由验收/任务终态事件驱动,消费积压事件推进 round
  （开产线派柳永、预筛、返工/止损、全部落定出战报）;
- 复盘段（mode="retro",P4）：离线归纳 Leader 验收标注 -> learned rubric
  （commands/wolong_retro.py,由 server/retro_trigger 每晚低峰投递）;
- 编排机械是确定性 Python（commands/wolong_rounds.py）,LLM 判断力挂在
  预筛(commands/prescreen.py)与复盘。

原 opus(sclaude) headless 一把梭保留为 mode="legacy"：脑子在 scripts/wolong_sop.md,
启动器 scripts/run_wolong.sh,30 分钟 deadline kill。
"""

from __future__ import annotations

import argparse
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.common import cancel
from ncds_opus_factory.common.round_store import RoundStore
from ncds_opus_factory.commands import wolong_rounds

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "run_wolong.sh"
REVIEW_DIR = ROOT / "state" / "benchmark" / "review"

DEFAULT_TIMEOUT_SECONDS = 1800  # legacy opus 全链路编排，30 分钟封顶

ProgressFn = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def run(
    count: int = 3,
    benchmark_path: str = "",
    avoid: str = "",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    round_id: str | None = None,
    resume: bool = False,
    mode: str | None = None,
    _dispatch_task_id: str | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """卧龙入口:默认分段编排;resume=True 跑续跑段;mode=legacy 走 opus 一把梭。

    _dispatch_task_id 由 task_runner 注入(派单段确定化 round_id 用,重启重跑收敛
    到同一个 round);CLI 直跑没有它,退回随机 round_id。
    返回:派单段 {round_id, stage, guiguzi_task},库存直开形态(跳过鬼谷子,§8.4 裁定 a)
    则为 {round_id, stage:"scripts", skipped_guiguzi, lines, status};
    续跑段 {round_id, consumed, status[, report]};
    legacy {count, review_dir, returncode, tail}。
    """
    if mode == "retro":
        from ncds_opus_factory.commands import wolong_retro

        return wolong_retro.run_retro(on_progress)
    if mode == "legacy":
        return _run_opus_legacy(count, benchmark_path, avoid, timeout_seconds, on_progress)

    rounds = RoundStore()
    transport = wolong_rounds.HttpTransport()
    transport.ping()  # round 模式依赖 server(派发走 loopback):没起就清晰报错,别留孤儿 round
    if resume:
        if not round_id:
            raise ValueError("续跑段必须携带 round_id")
        return wolong_rounds.resume_round(rounds, transport, round_id, on_progress)
    return wolong_rounds.start_round(
        rounds, transport, count=count, benchmark_path=benchmark_path,
        avoid=avoid, dispatch_task_id=_dispatch_task_id, on_progress=on_progress,
    )


# ---------------------------------------------------------------------------
# legacy:opus headless 一把梭(P3 前的卧龙本体,保留作对照/兜底)
# ---------------------------------------------------------------------------
def _run_opus_legacy(
    count: int,
    benchmark_path: str,
    avoid: str,
    timeout_seconds: int,
    on_progress: ProgressFn,
) -> dict[str, Any]:
    if not SCRIPT.is_file():
        raise FileNotFoundError(f"缺启动器: {SCRIPT}")

    # run_wolong.sh 用 ${2:-默认} / ${3:-默认}，传空串会触发默认值，所以可安全传 ""
    cmd = ["bash", str(SCRIPT), str(count), benchmark_path or "", avoid or ""]
    on_progress(f"wolong(legacy) start: count={count} (opus headless, may take minutes)")

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
    cancelled = cancel.current()
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
            if cancelled():
                # 用户取消:SIGTERM 子进程,不再派发任何东西(防幽灵 round)
                proc.send_signal(signal.SIGTERM)
                raise cancel.TaskCancelled("wolong(legacy) cancelled by user")
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
    parser = argparse.ArgumentParser(prog="nof wolong", description="卧龙先生: 内容工厂操盘手(分段编排)")
    parser.add_argument("--count", type=int, default=3, help="本轮产出条数")
    parser.add_argument("--benchmark", default="", help="对标数据 all_posts.json 路径(空=自动发现最新)")
    parser.add_argument("--avoid", default="", help="已发选题,逗号分隔")
    parser.add_argument("--resume", default="", help="续跑指定 round_id(调试用;线上由事件驱动)")
    parser.add_argument("--legacy", action="store_true", help="走 opus headless 一把梭(旧模式)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        count=args.count,
        benchmark_path=args.benchmark,
        avoid=args.avoid,
        timeout_seconds=args.timeout,
        round_id=args.resume or None,
        resume=bool(args.resume),
        mode="legacy" if args.legacy else None,
        on_progress=on_progress,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
