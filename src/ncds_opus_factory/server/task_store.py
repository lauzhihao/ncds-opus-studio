"""任务文件存储。

目录结构（base_dir 默认 ncds-opus-studio/state/tasks）：

    state/tasks/{task_id}/
    ├── meta.json        # TaskMeta（命令、参数、状态）
    ├── events.jsonl     # TaskEvent 逐行追加（progress / done / error）
    └── result.json      # 终态产物（仅成功时写入）

读写都是短事务（open + write + close），不加锁；on_progress 回调可能
来自工作线程，append_event 写一行不会与其它线程交错（OS 级 append O_APPEND
原子性 + 单行 JSON 足够）。
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ncds_opus_factory.server.schemas import TaskEvent, TaskMeta, TaskStatus


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _new_task_id() -> str:
    # 时间戳前缀方便目录里按生成顺序观察；hex 后缀避免并发碰撞
    return f"t_{_now_ms()}_{secrets.token_hex(4)}"


class TaskStore:
    """按 task_id 在文件系统持久化任务元/事件/结果。"""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------
    def task_dir(self, task_id: str) -> Path:
        return self.base_dir / task_id

    def meta_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "meta.json"

    def events_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "events.jsonl"

    def result_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "result.json"

    def exists(self, task_id: str) -> bool:
        return self.meta_path(task_id).exists()

    # ------------------------------------------------------------
    # Create / update meta
    # ------------------------------------------------------------
    def create(self, cmd: str, params: dict[str, Any]) -> TaskMeta:
        task_id = _new_task_id()
        task_dir = self.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        # 预创建空 events 文件，便于 SSE tail
        self.events_path(task_id).touch()
        meta = TaskMeta(
            task_id=task_id,
            cmd=cmd,
            params=params,
            status="pending",
            created_at=_now_iso(),
        )
        self._write_meta(meta)
        return meta

    def get_meta(self, task_id: str) -> TaskMeta | None:
        path = self.meta_path(task_id)
        if not path.exists():
            return None
        return TaskMeta(**json.loads(path.read_text(encoding="utf-8")))

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        error: str | None = None,
    ) -> TaskMeta:
        meta = self.get_meta(task_id)
        if not meta:
            raise FileNotFoundError(f"task not found: {task_id}")
        meta.status = status
        if status == "running" and not meta.started_at:
            meta.started_at = _now_iso()
        if status in ("completed", "failed") and not meta.finished_at:
            meta.finished_at = _now_iso()
        if error is not None:
            meta.error = error
        self._write_meta(meta)
        return meta

    def _write_meta(self, meta: TaskMeta) -> None:
        self.meta_path(meta.task_id).write_text(
            meta.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )

    # ------------------------------------------------------------
    # Events (append-only jsonl)
    # ------------------------------------------------------------
    def append_event(self, task_id: str, event: TaskEvent) -> None:
        line = event.model_dump_json(exclude_none=True) + "\n"
        # 用 'a' 模式 + O_APPEND 语义保证单行写入原子性（POSIX 单次 write < PIPE_BUF）
        with self.events_path(task_id).open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()

    def append_progress(self, task_id: str, text: str) -> None:
        self.append_event(task_id, TaskEvent(type="progress", ts=_now_ms(), text=text))

    def append_done(self, task_id: str, result: dict[str, Any]) -> None:
        self.append_event(task_id, TaskEvent(type="done", ts=_now_ms(), result=result))

    def append_error(self, task_id: str, error: str) -> None:
        self.append_event(task_id, TaskEvent(type="error", ts=_now_ms(), error=error))

    # ------------------------------------------------------------
    # Result
    # ------------------------------------------------------------
    def write_result(self, task_id: str, result: dict[str, Any]) -> None:
        self.result_path(task_id).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_result(self, task_id: str) -> dict[str, Any] | None:
        path = self.result_path(task_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
