"""Shared state models and in-memory event bus for pipeline jobs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NodeState:
    name: str
    status: str = "idle"  # idle / queued / running / done / failed
    started_at: float | None = None
    finished_at: float | None = None
    progress: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    task_id: str | None = None


@dataclass
class JobState:
    job_id: str
    pipeline_id: str
    title: str
    created_at: float
    updated_at: float
    inputs: dict[str, Any] = field(default_factory=dict)
    nodes: dict[str, NodeState] = field(default_factory=dict)
    node_positions: dict[str, dict[str, float]] = field(default_factory=dict)
    node_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    mock: bool = False
    engine_iid: str | None = None


class EventBus:
    """In-memory pub/sub for SSE. Each subscriber gets one asyncio.Queue."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, job_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        lst = self._subscribers.get(job_id)
        if lst and q in lst:
            lst.remove(q)
            if not lst:
                self._subscribers.pop(job_id, None)

    def publish(self, job_id: str, event: dict[str, Any]) -> None:
        for q in self._subscribers.get(job_id, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
