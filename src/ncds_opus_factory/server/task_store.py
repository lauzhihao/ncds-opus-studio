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
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ncds_opus_factory.server.schemas import Review, TaskEvent, TaskMeta, TaskStatus

# task_id 白名单：只允许字母/数字/下划线/连字符。task_dir = base_dir / task_id 是
# 直接拼接，不显式校验的话像 ../../etc 这样的 task_id 会越出 base_dir。这里禁掉 . 和 /
# 即可挡住路径穿越，同时兼容 _new_task_id() 的 t_<ms>_<hex> 与历史种子 id（如 t_demo_*）。
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _valid_task_id(task_id: str) -> bool:
    return bool(_TASK_ID_RE.match(task_id))


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

    def review_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "review.json"

    def exists(self, task_id: str) -> bool:
        # 非法 task_id 直接当作不存在：调用方（review / events 路由）据此返回 404，
        # 既挡住路径穿越，又不抛 500。
        return _valid_task_id(task_id) and self.meta_path(task_id).exists()

    # ------------------------------------------------------------
    # Create / update meta
    # ------------------------------------------------------------
    def create(
        self,
        cmd: str,
        params: dict[str, Any],
        source: str | None = None,
        parent_task_id: str | None = None,
        round_id: str | None = None,
    ) -> TaskMeta:
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
            source=source,
            parent_task_id=parent_task_id,
            round_id=round_id,
        )
        self._write_meta(meta)
        return meta

    def get_meta(self, task_id: str) -> TaskMeta | None:
        if not _valid_task_id(task_id):
            return None
        path = self.meta_path(task_id)
        if not path.exists():
            return None
        return TaskMeta(**json.loads(path.read_text(encoding="utf-8")))

    def list_tasks(self) -> list[TaskMeta]:
        """列出所有任务实例的 meta，按 created_at 倒序（最新在前）。"""
        metas: list[TaskMeta] = []
        if not self.base_dir.exists():
            return metas
        for d in self.base_dir.iterdir():
            if not d.is_dir() or not (d / "meta.json").exists():
                continue
            meta = self.get_meta(d.name)
            if meta is not None:
                # 回填人工决策，供移动端「待验收收件箱」一次拉到，省去逐条查详情。
                # 只挂在内存对象上，不写回 meta.json（决策真源是 review.json）。
                review = self.get_review(d.name)
                if review is not None:
                    meta.decision = review.decision
                metas.append(meta)
        # created_at 是 ISO 时间串，字典序即时间序，倒排得最新在前
        metas.sort(key=lambda m: m.created_at, reverse=True)
        return metas

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
        if status in ("completed", "failed", "cancelled") and not meta.finished_at:
            meta.finished_at = _now_iso()
        if error is not None:
            meta.error = error
        self._write_meta(meta)
        return meta

    def reset_for_requeue(self, task_id: str) -> TaskMeta:
        """已取消任务恢复:回到 pending,清掉上一轮的时间戳/错误(exclude_none 落盘即消失)。"""
        meta = self.get_meta(task_id)
        if not meta:
            raise FileNotFoundError(f"task not found: {task_id}")
        meta.status = "pending"
        meta.started_at = None
        meta.finished_at = None
        meta.error = None
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

    # ------------------------------------------------------------
    # Review（人工决策：同意 / 拒绝 + 备注）
    # ------------------------------------------------------------
    def write_review(self, task_id: str, review: Review) -> None:
        """覆盖写 review.json（允许重复改判）。"""
        self.review_path(task_id).write_text(
            review.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )

    def get_review(self, task_id: str) -> Review | None:
        path = self.review_path(task_id)
        if not path.exists():
            return None
        return Review(**json.loads(path.read_text(encoding="utf-8")))

    def delete_review(self, task_id: str) -> bool:
        """撤销人工决策(已归档拉回待验收)。返回是否真的删了。"""
        path = self.review_path(task_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def set_display(self, task_id: str, title: str | None, subtitle: str | None) -> None:
        """命令完成后回填展示标题/副题(任务卡显示作品信息,不显示原始链接)。"""
        meta = self.get_meta(task_id)
        if not meta:
            return
        if title:
            meta.title = str(title)[:80]
        if subtitle:
            meta.subtitle = str(subtitle)[:80]
        self._write_meta(meta)
