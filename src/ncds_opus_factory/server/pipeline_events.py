"""Event log and progress helpers for PipelineRunner."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PipelineEventsMixin:
    """events.jsonl persistence plus in-memory EventBus publish helpers."""

    def _events_file(self, job_id: str) -> Path:
        """返回该 job 的事件日志路径：video-jobs/{job_id}/events.jsonl。"""
        return self.video_jobs_dir / job_id / "events.jsonl"

    def _emit(self, job_id: str, event: dict[str, Any]) -> None:
        """统一事件发射：追加到 events.jsonl，并广播给内存 EventBus。"""
        if job_id not in self._event_seq:
            ef = self._events_file(job_id)
            if ef.is_file():
                try:
                    last_seq = 0
                    with ef.open("r", encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if line:
                                try:
                                    obj = json.loads(line)
                                    last_seq = int(obj.get("seq") or last_seq)
                                except (json.JSONDecodeError, ValueError):
                                    pass
                    self._event_seq[job_id] = last_seq
                except OSError:
                    self._event_seq[job_id] = 0
            else:
                self._event_seq[job_id] = 0

        self._event_seq[job_id] += 1
        seq = self._event_seq[job_id]

        record: dict[str, Any] = {**event, "ts": time.time(), "seq": seq}
        record.setdefault("node", None)

        ef = self._events_file(job_id)
        ef.parent.mkdir(parents=True, exist_ok=True)
        if ef.is_file() and ef.stat().st_size > 0:
            try:
                with ef.open("rb") as _fb:
                    _fb.seek(-1, 2)
                    _last_byte = _fb.read(1)
                if _last_byte != b"\n":
                    with ef.open("a", encoding="utf-8") as _fix:
                        _fix.write("\n")
            except OSError:
                pass
        with ef.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()

        self.bus.publish(job_id, event)

    def _push_progress(self, job_id: str, node_name: str, text: str) -> None:
        """更新节点 progress 字段 + 落盘 + SSE publish。"""
        self._mutate_node_and_emit(
            job_id,
            node_name,
            lambda n: setattr(n, "progress", text),
            warning="[pipeline] push_progress failed: %s",
        )

    def _push_outputs_patch(self, job_id: str, node_name: str, key: str, value: Any) -> None:
        """running 期间往 node.outputs[key] 写一份实时进度 + publish。"""

        def mutate(n: Any) -> None:
            n.outputs = {**(n.outputs or {}), key: value}

        self._mutate_node_and_emit(
            job_id,
            node_name,
            mutate,
            warning="[pipeline] push_outputs_patch failed: %s",
        )

    def _mutate_node_and_emit(
        self,
        job_id: str,
        node_name: str,
        mutate: Callable[[Any], None],
        *,
        warning: str,
    ) -> None:
        """Apply a small node mutation, persist it, and publish a node_status event."""
        try:
            state = self._load(job_id)
            n = state.nodes.get(node_name)
            if n is None:
                return
            mutate(n)
            self._save(state)
            self._emit(
                job_id,
                {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)},
            )
        except Exception as exc:
            logger.warning(warning, exc)

    def _push_model_progress(self, job_id: str, node_name: str, model_progress: dict[str, Any]) -> None:
        self._push_outputs_patch(job_id, node_name, "model_progress", model_progress)
