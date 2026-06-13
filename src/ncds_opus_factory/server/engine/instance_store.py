"""生产实例文件存储（state/instances）。

目录布局（design §6）：

    state/instances/{instance_id}/
    ├── meta.json                 # InstanceMeta
    ├── inputs.json               # 全局输入
    ├── instance_events.jsonl     # 实例级事件
    └── steps/{step_id}/
        ├── state.json            # StepState
        └── events.jsonl          # 步级事件（progress/draft/decision）

沿用 TaskStore 的纪律：tmp+os.replace 原子写 meta/state（防撕裂），事件用 O_APPEND
单行 JSON（跨线程 append 原子）。instance_id 白名单挡路径穿越。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ncds_opus_factory.server.engine.types import (
    InstanceEvent,
    InstanceMeta,
    InstanceState,
    InstanceStatus,
    StepState,
)

logger = logging.getLogger(__name__)

_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _valid_instance_id(instance_id: str) -> bool:
    return bool(_INSTANCE_ID_RE.match(instance_id))


def _valid_step_id(step_id: str) -> bool:
    # step_id 同样进路径拼接（steps/{step_id}/），同白名单挡 ../ 穿越
    return bool(_INSTANCE_ID_RE.match(step_id))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _new_instance_id() -> str:
    # i_ 前缀区分 instance；时间戳便于按生成序观察；hex 后缀防并发碰撞
    return f"i_{_now_ms()}_{secrets.token_hex(4)}"


class InstanceStore:
    """按 instance_id 在文件系统持久化生产实例（meta + 每步 state/事件 + 输入）。"""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ paths
    def instance_dir(self, iid: str) -> Path:
        return self.base_dir / iid

    def meta_path(self, iid: str) -> Path:
        return self.instance_dir(iid) / "meta.json"

    def inputs_path(self, iid: str) -> Path:
        return self.instance_dir(iid) / "inputs.json"

    def instance_events_path(self, iid: str) -> Path:
        return self.instance_dir(iid) / "instance_events.jsonl"

    def step_dir(self, iid: str, step_id: str) -> Path:
        return self.instance_dir(iid) / "steps" / step_id

    def step_state_path(self, iid: str, step_id: str) -> Path:
        return self.step_dir(iid, step_id) / "state.json"

    def step_events_path(self, iid: str, step_id: str) -> Path:
        return self.step_dir(iid, step_id) / "events.jsonl"

    def exists(self, iid: str) -> bool:
        return _valid_instance_id(iid) and self.meta_path(iid).exists()

    # ------------------------------------------------------------ create
    def create(
        self,
        recipe_id: str,
        step_ids: list[str],
        inputs: dict[str, Any] | None = None,
        *,
        owner_id: str | None = None,
        source: str | None = None,
        driver: str = "manual",
        round_id: str | None = None,
        parent_instance_id: str | None = None,
        intent_key: str | None = None,
        title: str = "",
    ) -> InstanceMeta:
        iid = _new_instance_id()
        now = _now_iso()
        meta = InstanceMeta(
            instance_id=iid,
            owner_id=owner_id,
            recipe_id=recipe_id,
            status="pending",
            title=title,
            created_at=now,
            updated_at=now,
            source=source,
            driver=driver,  # type: ignore[arg-type]
            round_id=round_id,
            parent_instance_id=parent_instance_id,
            intent_key=intent_key,
        )
        self.instance_dir(iid).mkdir(parents=True, exist_ok=True)
        self._write_model(self.meta_path(iid), meta)
        self._write_json(self.inputs_path(iid), inputs or {})
        self.instance_events_path(iid).touch()
        # 每步初始化为 idle
        for sid in step_ids:
            self.step_dir(iid, sid).mkdir(parents=True, exist_ok=True)
            self._write_model(self.step_state_path(iid, sid), StepState(step_id=sid))
            self.step_events_path(iid, sid).touch()
        return meta

    # ------------------------------------------------------------ read
    def get_meta(self, iid: str) -> InstanceMeta | None:
        if not _valid_instance_id(iid) or not self.meta_path(iid).exists():
            return None
        return InstanceMeta(**json.loads(self.meta_path(iid).read_text(encoding="utf-8")))

    def get_inputs(self, iid: str) -> dict[str, Any]:
        if not _valid_instance_id(iid):
            return {}
        p = self.inputs_path(iid)
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def get_step_state(self, iid: str, step_id: str) -> StepState | None:
        if not _valid_instance_id(iid) or not _valid_step_id(step_id):
            return None
        p = self.step_state_path(iid, step_id)
        if not p.exists():
            return None
        return StepState(**json.loads(p.read_text(encoding="utf-8")))

    def get_state(self, iid: str) -> InstanceState | None:
        """装配完整实例状态（meta + 各步 state + 输入）。"""
        meta = self.get_meta(iid)
        if meta is None:
            return None
        steps: dict[str, StepState] = {}
        steps_root = self.instance_dir(iid) / "steps"
        if steps_root.is_dir():
            for d in sorted(steps_root.iterdir()):
                st = self.get_step_state(iid, d.name)
                if st is not None:
                    steps[d.name] = st
        return InstanceState(meta=meta, inputs=self.get_inputs(iid), steps=steps)

    def list_instances(self, owner_id: str | None = None) -> list[InstanceMeta]:
        """列出所有实例 meta，按 created_at 倒序。``owner_id`` 给定时只返回该租户的。"""
        metas: list[InstanceMeta] = []
        if not self.base_dir.exists():
            return metas
        for d in self.base_dir.iterdir():
            if not d.is_dir() or not (d / "meta.json").exists():
                continue
            try:
                meta = self.get_meta(d.name)
            except Exception:  # noqa: BLE001
                logger.warning("[InstanceStore] 跳过不可读 meta: %s", d.name)
                continue
            if meta is None:
                continue
            if owner_id is not None and meta.owner_id != owner_id:
                continue
            metas.append(meta)
        metas.sort(key=lambda m: m.created_at, reverse=True)
        return metas

    # ------------------------------------------------------------ update
    def update_meta_status(self, iid: str, status: InstanceStatus) -> InstanceMeta:
        meta = self.get_meta(iid)
        if meta is None:
            raise FileNotFoundError(f"instance not found: {iid}")
        meta.status = status
        meta.updated_at = _now_iso()
        self._write_model(self.meta_path(iid), meta)
        return meta

    def write_step_state(self, iid: str, state: StepState) -> None:
        self._write_model(self.step_state_path(iid, state.step_id), state)

    def append_step_event(self, iid: str, step_id: str, event: InstanceEvent) -> None:
        self._append_jsonl(self.step_events_path(iid, step_id), event)

    def append_instance_event(self, iid: str, event: InstanceEvent) -> None:
        self._append_jsonl(self.instance_events_path(iid), event)

    # ------------------------------------------------------------ io helpers
    @staticmethod
    def _write_model(path: Path, model: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(model.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _write_json(path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _append_jsonl(path: Path, event: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json(exclude_none=True) + "\n")
            f.flush()
