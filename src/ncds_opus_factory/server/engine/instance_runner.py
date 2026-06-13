"""生产引擎运行时（InstanceRunner）+ 分层事件总线。

职责（design §8）：**执行一步 + 维护实例/步骤状态 + 推事件**。谁决定下一步跑什么 = driver
（web 手动 / 卧龙自治），不在引擎内。引擎靠 ``build_full_registry()`` 按步骤的 cmd/agent
字符串 key **晚绑定派发**，不直接 import 任何 agent。

步骤执行契约（与 TaskRunner 一致）：``run_fn(on_progress=fn, **params) -> dict``，同步阻塞，
在 ``asyncio.to_thread`` 工作线程里跑。

E0：单步执行 + 状态机 + 事件/落盘；先不接任何视图、不做 DAG 自动推进（那是 driver 的活）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from ncds_opus_factory.commands import build_full_registry
from ncds_opus_factory.server.engine.instance_store import InstanceStore
from ncds_opus_factory.server.engine.recipes import RECIPE_REGISTRY
from ncds_opus_factory.server.engine.types import (
    InstanceEvent,
    InstanceState,
    Recipe,
    StepState,
    StepStatus,
    can_transition,
)

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat()


def _invoke(run_fn: Callable[..., Any], params: dict[str, Any], on_progress: Callable[[str], None]) -> Any:
    """asyncio.to_thread 的同步目标：run_fn(on_progress=..., **params)。"""
    return run_fn(on_progress=on_progress, **params)


# ---------------------------------------------------------------------------
# 分层事件总线（SSE）：订阅者按 level 过滤（app 订 meta/step，web 订到 detail）
# ---------------------------------------------------------------------------
class EngineEventBus:
    """In-memory pub/sub by instance_id，每订阅者一个 asyncio.Queue + 一组关心的 level。"""

    def __init__(self) -> None:
        self._subs: dict[str, list[tuple[asyncio.Queue[InstanceEvent], set[str]]]] = {}

    def subscribe(self, instance_id: str, levels: set[str] | None = None) -> asyncio.Queue[InstanceEvent]:
        q: asyncio.Queue[InstanceEvent] = asyncio.Queue(maxsize=256)
        self._subs.setdefault(instance_id, []).append((q, levels or {"meta", "step", "detail"}))
        return q

    def unsubscribe(self, instance_id: str, q: asyncio.Queue[InstanceEvent]) -> None:
        subs = self._subs.get(instance_id)
        if not subs:
            return
        self._subs[instance_id] = [(qq, lv) for (qq, lv) in subs if qq is not q]
        if not self._subs[instance_id]:
            del self._subs[instance_id]

    def publish(self, event: InstanceEvent) -> None:
        for q, levels in self._subs.get(event.instance_id, []):
            if event.level not in levels:
                continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # 慢消费者忽略；客户端可 GET 全量状态兜底
                pass


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class InstanceRunner:
    """跑生产实例的步骤。registry/recipes 可注入（测试用 fake），默认取全局。"""

    def __init__(
        self,
        store: InstanceStore,
        registry: dict[str, Callable[..., Any]] | None = None,
        recipes: dict[str, Recipe] | None = None,
    ) -> None:
        self.store = store
        self.registry = registry if registry is not None else build_full_registry()
        self.recipes = recipes if recipes is not None else RECIPE_REGISTRY
        self.bus = EngineEventBus()

    # ------------------------------------------------------------ create
    def create_instance(
        self,
        recipe_id: str,
        inputs: dict[str, Any] | None = None,
        **meta_kwargs: Any,
    ) -> InstanceState:
        recipe = self._recipe(recipe_id)
        meta = self.store.create(recipe_id, recipe.step_ids(), inputs, **meta_kwargs)
        state = self.store.get_state(meta.instance_id)
        assert state is not None  # 刚建必存在
        return state

    # ------------------------------------------------------------ run one step
    async def run_step(
        self,
        instance_id: str,
        step_id: str,
        step_inputs: dict[str, Any] | None = None,
    ) -> StepState:
        """执行单步：派发 → 状态机推进 → 事件/落盘。返回该步终态 StepState。

        - 无执行体的步（input/output/无 cmd 的 process）：直通标 done。
        - 有 intervention 的步：跑出草稿后停在 ``awaiting_review``（等 driver/人继续）。
        - 否则：跑完直接 ``done``。
        """
        recipe = self._recipe_for(instance_id)
        step = recipe.step(step_id)
        state = self.store.get_step_state(instance_id, step_id)
        if state is None:
            raise FileNotFoundError(f"step not found: {instance_id}/{step_id}")

        performer = step.performer
        if performer is None:
            # 无执行体：直通完成（driver/UI 负责其语义）
            self._transition(instance_id, state, "done", finished_at=_now_iso())
            return state

        run_fn = self.registry.get(performer)
        if run_fn is None:
            self._fail(instance_id, state, f"no performer in registry: {performer!r}")
            return state

        # running
        self.store.update_meta_status(instance_id, "running")
        self._transition(instance_id, state, "running", started_at=_now_iso())

        def on_progress(text: str) -> None:
            # 从工作线程调：只 append 步级 detail 事件（jsonl 线程安全）+ 更新内存 progress；
            # 不从工作线程碰 asyncio 总线（非线程安全）。
            state.progress = text
            self.store.append_step_event(
                instance_id, step_id,
                InstanceEvent(instance_id=instance_id, ts=_now_ms(), level="detail",
                              type="progress", step_id=step_id, payload={"text": text}),
            )

        params = dict(step_inputs or {})
        try:
            result = await asyncio.to_thread(_invoke, run_fn, params, on_progress)
        except Exception as exc:  # noqa: BLE001  —— 任何步骤异常都收成 failed，不冒泡炸 driver
            self._fail(instance_id, state, f"{type(exc).__name__}: {exc}")
            return state

        result = result if isinstance(result, dict) else {"result": result}

        if step.intervention is not None:
            # 出草稿 → 等人工介入（content_edit / decision_only）
            state.draft = result
            state.draft_source = "agent"
            self._transition(instance_id, state, "draft_ready")
            self._transition(instance_id, state, "awaiting_review", decision="pending")
        else:
            state.outputs = result
            self._transition(instance_id, state, "done", finished_at=_now_iso())
        return state

    # ------------------------------------------------------------ internals
    def _recipe(self, recipe_id: str) -> Recipe:
        if recipe_id not in self.recipes:
            raise KeyError(f"unknown recipe_id: {recipe_id}")
        return self.recipes[recipe_id]

    def _recipe_for(self, instance_id: str) -> Recipe:
        meta = self.store.get_meta(instance_id)
        if meta is None:
            raise FileNotFoundError(f"instance not found: {instance_id}")
        return self._recipe(meta.recipe_id)

    def _transition(
        self,
        instance_id: str,
        state: StepState,
        new: StepStatus,
        *,
        started_at: str | None = None,
        finished_at: str | None = None,
        decision: str | None = None,
    ) -> None:
        if not can_transition(state.status, new):
            raise ValueError(f"illegal step transition {state.status!r} -> {new!r} ({state.step_id})")
        state.status = new
        if started_at is not None:
            state.started_at = started_at
        if finished_at is not None:
            state.finished_at = finished_at
        if decision is not None:
            state.decision = decision  # type: ignore[assignment]
        self.store.write_step_state(instance_id, state)
        ev = InstanceEvent(
            instance_id=instance_id, ts=_now_ms(), level="step",
            type="status", step_id=state.step_id, payload={"status": new},
        )
        self.store.append_step_event(instance_id, state.step_id, ev)
        self.bus.publish(ev)

    def _fail(self, instance_id: str, state: StepState, error: str) -> None:
        state.error = error
        state.finished_at = _now_iso()
        self._transition(instance_id, state, "failed")
        logger.warning("[engine] step failed: %s/%s — %s", instance_id, state.step_id, error)
