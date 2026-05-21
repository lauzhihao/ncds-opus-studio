#!/usr/bin/env python3
"""ncds-opus-factory 项目地图看门狗。

long-running 进程，周期性扫描仓库关心目录的 mtime；一旦有变化，
就以 subprocess 方式调用 scripts/map_project.py 重新生成 .project_map。

设计要点：
- 无外部依赖（不用 watchdog/fswatch 包），轮询比对 mtime
- 启动时先跑一次，确保 .project_map 总是存在
- ASCII-only 日志，便于 launchd / journald 收集
- SIGTERM / SIGINT 优雅退出，方便 launchd 重启
- 同一时间只允许一个看门狗实例（PID 文件 + flock）
"""

from __future__ import annotations

import errno
import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAP_SCRIPT = PROJECT_ROOT / "scripts" / "map_project.py"
PID_FILE = PROJECT_ROOT / "state" / "map_project_watchdog.pid"
LOCK_FILE = PROJECT_ROOT / "state" / "map_project_watchdog.lock"

# 监控的目录（相对项目根）。子目录递归扫描。
WATCH_DIRS = [
    "src",
    "scripts",
    "pipelines",
    "gpt_image",
    "skills",
    "docs",
    "configs",
]

# 监控的根目录文件（变更后也触发重生成）
WATCH_ROOT_FILES = [
    "README.md", "AGENTS.md", "CLAUDE.md",
    "pyproject.toml", "package.json", ".gitignore",
]

# 仅这些扩展会被纳入快照
TRACKED_EXTS = {
    ".py", ".mjs", ".js", ".ts", ".sh",
    ".json", ".toml", ".yaml", ".yml",
    ".md",
}

# 忽略的目录名（不进入扫描）
IGNORE_DIRS = {
    "__pycache__", ".pytest_cache", ".venv", "node_modules",
    ".git", "dist", "build", ".egg-info",
    "state", "video-jobs", ".worktrees",
}

POLL_INTERVAL_SECONDS = 3.0
# 检测到变化后再等 DEBOUNCE 秒才真正跑，吸收连续保存
DEBOUNCE_SECONDS = 1.5

_running = True


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    sys.stderr.write(f"[watchdog {ts}] {msg}\n")
    sys.stderr.flush()


def _handle_signal(signum: int, _frame) -> None:  # noqa: ANN001
    global _running
    _running = False
    _log(f"received signal {signum}, shutting down")


def _ensure_single_instance() -> None:
    """通过 flock 保证同一时间只有一个看门狗实例。"""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            _log("another watchdog instance is already running; exiting")
            sys.exit(0)
        raise
    # 主动写入 PID 方便排查
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("ascii"))
    # 持有 fd 直到进程退出（不 close）
    global _LOCK_FD
    _LOCK_FD = fd  # type: ignore[name-defined]


def _write_pid() -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{os.getpid()}\n", encoding="ascii")


def _scan_snapshot() -> dict[str, float]:
    snap: dict[str, float] = {}
    for rel in WATCH_DIRS:
        base = PROJECT_ROOT / rel
        if not base.is_dir():
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.endswith(".egg-info")]
            for fname in files:
                if not any(fname.endswith(ext) for ext in TRACKED_EXTS):
                    continue
                full = Path(root) / fname
                try:
                    snap[str(full)] = full.stat().st_mtime
                except OSError:
                    continue
    for rel in WATCH_ROOT_FILES:
        p = PROJECT_ROOT / rel
        if p.exists():
            try:
                snap[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return snap


def _run_map() -> None:
    try:
        result = subprocess.run(
            [sys.executable, str(MAP_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            _log(f"map_project.py exited {result.returncode}: {result.stderr.strip()}")
        else:
            tail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "ok"
            _log(f"map regenerated: {tail}")
    except subprocess.TimeoutExpired:
        _log("map_project.py timed out (60s)")
    except Exception as e:  # noqa: BLE001
        _log(f"map_project.py crashed: {e!r}")


def main() -> int:
    if not MAP_SCRIPT.exists():
        _log(f"map script missing: {MAP_SCRIPT}")
        return 1

    _ensure_single_instance()
    _write_pid()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _log(f"watchdog starting; root={PROJECT_ROOT}")
    _run_map()
    last = _scan_snapshot()
    _log(f"initial snapshot: {len(last)} tracked files")

    pending_since: float | None = None

    while _running:
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            current = _scan_snapshot()
        except Exception as e:  # noqa: BLE001
            _log(f"scan error: {e!r}")
            continue

        if current != last:
            if pending_since is None:
                pending_since = time.monotonic()
            elif time.monotonic() - pending_since >= DEBOUNCE_SECONDS:
                added = len(current.keys() - last.keys())
                removed = len(last.keys() - current.keys())
                changed = sum(
                    1 for k in current.keys() & last.keys()
                    if current[k] != last[k]
                )
                _log(f"change detected: +{added} -{removed} ~{changed}")
                _run_map()
                last = current
                pending_since = None
        else:
            pending_since = None

    _log("watchdog stopped")
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
