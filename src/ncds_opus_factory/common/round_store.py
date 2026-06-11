"""卧龙 round 状态机的文件存储（docs/WOLONG-DESIGN.md §4.1）。

一轮编排 = 一个 round，状态文件 state/wolong/rounds/{round_id}.json：

    {
      "round_id": "round_20260611_191500_ab12",
      "status": "active | done | terminated",
      "goal": {"count": 3, "benchmark_path": "...", "avoid": ""},
      "stage": "topics | scripts | done",
      "intents": {"guiguzi:0": {"cmd": "...", "params": {...}, "task_id": null|"t_..."}},
      "lines": [{"slot": 0, "topic": {...}, "task_id": "t_..", "status": "...", "rework": 0}],
      "events": [{"kind": "terminal|decision", "task_id": "...", ...,"consumed": false}],
      "report": null | {...}
    }

并发模型：事件追加来自 event loop（review/cancel 路由、runner 终态钩子），
读改写来自卧龙段的工作线程——同进程跨线程，按 round 文件路径共享 threading.Lock
（模块级注册表，跨 RoundStore 实例也共享）。写盘一律 tmp+os.replace 原子写
（卧龙段超时是 kill，不能留撕裂 JSON）。

事件语义：
- terminal 按 (task_id, status) 去重；
- decision 定案（§4.3）：已消费的 decision 不再接受改判（改判只影响案卷），
  未消费的可被覆盖（用户在续跑前改判是合法的）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_ROUND_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 锁注册表按"解析后的文件路径"共享:server 侧与 commands 侧各自构造 RoundStore,
# 但同一 round 文件必须是同一把锁
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.Lock()
        return _LOCKS[key]


def default_rounds_dir() -> Path:
    """与 server.state 的约定一致:NOF_STATE_DIR 指向 state/tasks,round 在兄弟目录。"""
    sd = os.environ.get("NOF_STATE_DIR")
    if sd:
        return Path(sd).parent / "wolong" / "rounds"
    root = Path(__file__).resolve().parents[3]
    return root / "state" / "wolong" / "rounds"


def _now_iso() -> str:
    return datetime.now().isoformat()


class RoundStore:
    """round 状态文件的读写（原子写 + 跨线程锁 + 事件去重/定案）。"""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else default_rounds_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def path(self, round_id: str) -> Path:
        if not _ROUND_ID_RE.match(round_id):
            raise ValueError(f"invalid round_id: {round_id}")
        return self.base_dir / f"{round_id}.json"

    # ------------------------------------------------------------
    def create(self, round_id: str, goal: dict[str, Any]) -> dict[str, Any]:
        path = self.path(round_id)
        with _lock_for(path):
            if path.exists():
                raise FileExistsError(f"round 已存在: {round_id}")
            r: dict[str, Any] = {
                "round_id": round_id,
                "status": "active",
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "goal": goal,
                "stage": "topics",
                "intents": {},
                "lines": [],
                "events": [],
                "report": None,
            }
            self._save_locked(r)
            return r

    def load(self, round_id: str) -> dict[str, Any] | None:
        path = self.path(round_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.exception("[rounds] round 文件不可读: %s", path)
            return None

    def list_active(self) -> list[dict[str, Any]]:
        out = []
        for f in sorted(self.base_dir.glob("round_*.json")):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("[rounds] 跳过不可读 round: %s", f.name)
                continue
            if r.get("status") == "active":
                out.append(r)
        return out

    @contextmanager
    def mutate(self, round_id: str) -> Iterator[dict[str, Any]]:
        """锁内读改写。round 不存在抛 FileNotFoundError。

        注意:锁内不要做网络/HTTP 调用——事件追加方在 event loop 上等同一把锁,
        而 loopback HTTP 又需要 loop 活着,会死锁。派发要拆到锁外(见 wolong_rounds)。
        """
        path = self.path(round_id)
        with _lock_for(path):
            r = self.load(round_id)
            if r is None:
                raise FileNotFoundError(f"round not found: {round_id}")
            yield r
            self._save_locked(r)

    def _save_locked(self, r: dict[str, Any]) -> None:
        r["updated_at"] = _now_iso()
        path = self.path(r["round_id"])
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------
    def append_event(
        self,
        round_id: str,
        kind: str,
        task_id: str,
        decision: str | None = None,
        status: str | None = None,
        note: str | None = None,
    ) -> bool:
        """追加一条事件。返回是否真的新增/变更（False=去重丢弃或已定案）。"""
        path = self.path(round_id)
        with _lock_for(path):
            r = self.load(round_id)
            if r is None or r.get("status") != "active":
                return False
            events: list[dict[str, Any]] = r["events"]
            if kind == "decision":
                for ev in events:
                    if ev["kind"] == "decision" and ev["task_id"] == task_id:
                        if ev.get("consumed"):
                            # 定案(§4.3):续跑段已消费,改判只影响案卷,不回卷流程
                            logger.info("[rounds] %s 的决策已定案,忽略改判", task_id)
                            return False
                        if ev.get("decision") == decision and ev.get("note") == note:
                            return False
                        # 消费前改判:覆盖未消费事件
                        ev.update({"decision": decision, "note": note, "ts": _now_iso()})
                        self._save_locked(r)
                        return True
            else:  # terminal
                for ev in events:
                    if (
                        ev["kind"] == "terminal"
                        and ev["task_id"] == task_id
                        and ev.get("status") == status
                    ):
                        return False
            events.append({
                "kind": kind,
                "task_id": task_id,
                "decision": decision,
                "status": status,
                "note": note,
                "ts": _now_iso(),
                "consumed": False,
            })
            self._save_locked(r)
            return True

    def has_unconsumed(self, round_id: str) -> bool:
        r = self.load(round_id)
        if r is None or r.get("status") != "active":
            return False
        return any(not e.get("consumed") for e in r.get("events", []))
