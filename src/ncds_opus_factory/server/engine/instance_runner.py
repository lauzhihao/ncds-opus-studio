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
from typing import Any, Callable, Literal

from ncds_opus_core.common import cancel
from ncds_opus_factory.commands import build_full_registry
from ncds_opus_factory.server.engine.instance_store import InstanceStore
from ncds_opus_factory.server.engine.recipes import RECIPE_REGISTRY
from ncds_opus_factory.server.engine.types import (
    InstanceEvent,
    InstanceMeta,
    InstanceState,
    InstanceStatus,
    Recipe,
    StepState,
    StepStatus,
    can_transition,
)
from ncds_opus_factory.server.schemas import Review

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat()


def _invoke(
    run_fn: Callable[..., Any],
    params: dict[str, Any],
    on_progress: Callable[[str], None],
    cancel_check: Callable[[], bool] | None = None,
) -> Any:
    """asyncio.to_thread 的同步目标：run_fn(on_progress=..., **params)。
    cancel_check 装进线程局部，performer 在 cancel.checkpoint() 处中止。"""
    if cancel_check is not None:
        cancel.install(cancel_check)
    try:
        return run_fn(on_progress=on_progress, **params)
    finally:
        cancel.uninstall()


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
                # 慢消费者丢事件（与既有 PipelineRunner.EventBus 同款取舍）：SSE 只作实时提示，
                # 不是correctness 真源；客户端按 instance_id GET 全量状态兜底。E1 的 SSE 路由
                # 应支持断点重连 + 全量重同步（见 PRODUCTION-ENGINE-DESIGN.md §7）。
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
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------ create
    def create_instance(
        self,
        recipe_id: str,
        inputs: dict[str, Any] | None = None,
        **meta_kwargs: Any,
    ) -> InstanceState:
        recipe = self._recipe(recipe_id)
        recipe.validate()  # 坏配方（重复 step_id / 悬空 deps / 成环）早抛
        meta = self.store.create(recipe_id, recipe.step_ids(), inputs, **meta_kwargs)
        state = self.store.get_state(meta.instance_id)
        assert state is not None  # 刚建必存在
        return state

    # ------------------------------------------------------------ run one step
    def _lock_for(self, instance_id: str) -> asyncio.Lock:
        # 同一实例的步骤串行化：防并发 run_step 对 meta 的 read-modify-write 竞态。
        # 单事件循环内 dict get/set 之间无 await 间隙、天然原子，无需再锁这个 dict。
        lock = self._locks.get(instance_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[instance_id] = lock
        return lock

    async def run_step(
        self,
        instance_id: str,
        step_id: str,
        step_inputs: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        on_progress: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> StepState:
        """执行单步（**按实例串行化**，防并发步骤竞态）；执行细节见 _run_step。

        ``config`` = 运行期选择（模型/质量/profile 等）：步骤真正(重)启动时合并落 ``StepState.config``
        作**溯源记录**（终态步非法重跑直接抛、不记，避免记下没真跑的参数）。
        ⚠️ config **不** splat 进 performer——真实命令是闭合签名（rw 的 model 在 ``payload`` 里、
        不是顶层 kwarg），盲传必 ``TypeError``。driver 负责把选择折进 ``step_inputs``（与 TaskRunner
        "调用方建好 params" 的契约一致）；引擎只把 config 记成"这步用了哪个模型/参数"。

        ``on_progress`` = driver 透传回调：performer 每吐一行进度，引擎在落 jsonl 之外再调它一次
        （从工作线程同步调，driver 自己保证线程安全）。绞杀者据此把进度回桥到旧 facade SSE。
        """
        async with self._lock_for(instance_id):
            return await self._run_step(instance_id, step_id, step_inputs, config, on_progress, cancel_check)

    async def _run_step(
        self,
        instance_id: str,
        step_id: str,
        step_inputs: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        on_progress: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> StepState:
        """执行单步：派发 → 状态机推进 → 事件/落盘。返回该步终态 StepState。

        闸门判据是 ``intervention`` 而非 ``performer``（design §4）：
        - 有 intervention 的步（含**无执行体的人工编辑步**，如 final_preview 的 preview）：
          出草稿后停在 ``awaiting_review``，等 driver/人介入——绝不静默直通。
        - 真·无介入的直通步（input/output/纯 process）：直接标 done。
        - 其余有执行体步：跑完直接 done。
        """
        recipe = self._recipe_for(instance_id)
        step = recipe.step(step_id)
        state = self.store.get_step_state(instance_id, step_id)
        if state is None:
            raise FileNotFoundError(f"step not found: {instance_id}/{step_id}")

        performer = step.performer
        if performer is None:
            if step.intervention is not None:
                # 无执行体的人工编辑/决策步：以上游装配的 step_inputs 作初始草稿，停在
                # awaiting_review 等人介入。闸门 key 在 intervention，不在 performer——
                # 否则 lines/storyboard/preview 这类纯人工步会被静默直通 done，绕过强制闸门。
                self._start_running(instance_id, state, config)
                state.draft = dict(step_inputs or {})
                state.draft_source = "agent"
                self._transition(instance_id, state, "draft_ready")
                self._transition(instance_id, state, "awaiting_review", decision="pending")
            else:
                # 真·直通步（input/output/纯 process 无介入）：直接定稿。
                # 真正推进时（idle/queued）才记 config + 翻 meta running；
                # done 步重跑是 no-op，不记 config、不发假 running。
                if state.status in ("idle", "queued"):
                    self._merge_config(state, config)
                    self._publish_meta_status(instance_id, "running")
                self._transition(instance_id, state, "done", finished_at=_now_iso())
            return state

        run_fn = self.registry.get(performer)
        if run_fn is None:
            self._fail(instance_id, state, f"no performer in registry: {performer!r}")
            return state

        self._start_running(instance_id, state, config)

        def _step_progress(text: str) -> None:
            # 从工作线程调：append 步级 detail 事件（jsonl 线程安全）+ 更新内存 progress；
            # 不从工作线程碰 asyncio 总线（非线程安全）。driver 的 on_progress 也在此同步透传。
            state.progress = text
            self.store.append_step_event(
                instance_id, step_id,
                InstanceEvent(instance_id=instance_id, ts=_now_ms(), level="detail",
                              type="progress", step_id=step_id, payload={"text": text}),
            )
            if on_progress is not None:
                on_progress(text)

        # config 是溯源记录、不进 performer 参数（闭合签名会 TypeError）；driver 已把选择折进 step_inputs。
        # domain 是实例级元数据，由前端在创建实例时写进 instance inputs（task-2.3 负责）；
        # 引擎在这里把 domain 从 instance inputs 透传进每步 params，让任何 performer 都能 params.get("domain")。
        # step_inputs 显式传了 domain 时以 step_inputs 为准（step 级覆盖 instance 级）。
        instance_inputs = self.store.get_inputs(instance_id)
        instance_domain = instance_inputs.get("domain") if isinstance(instance_inputs, dict) else None
        params = dict(step_inputs or {})
        # 仅当 step_inputs 未显式传 domain 且 instance inputs 里有非空 domain 时才注入
        if instance_domain and not params.get("domain"):
            params["domain"] = instance_domain
        try:
            result = await asyncio.to_thread(_invoke, run_fn, params, _step_progress, cancel_check)
        except cancel.TaskCancelled:
            # 取消：步骤回 idle（可重跑；running→idle 非法，故直接写 fresh StepState 不走 _transition），
            # 重抛让 _execute_via_engine 透传 TaskCancelled 给 _execute 的 except TaskCancelled 分支。
            self.store.write_step_state(instance_id, StepState(step_id=step_id, config=dict(state.config)))
            raise
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

    # ============================================================ driver API
    # driver（web 手动 / 卧龙）据这些原语推进 DAG：找下一可跑步、装配上游产物、
    # 过人工闸门、改稿级联失效、结算实例终态。引擎只提供机械，不决定"下一步跑什么"。

    def get_runnable_steps(self, instance_id: str) -> list[str]:
        """返回当前可跑的步（``idle`` 且全部 deps 已 ``done``/``skipped``），按拓扑序。"""
        state = self.store.get_state(instance_id)
        if state is None:
            raise FileNotFoundError(f"instance not found: {instance_id}")
        recipe = self._recipe(state.meta.recipe_id)
        runnable: list[str] = []
        for sid in recipe.topological_order():
            st = state.steps.get(sid)
            if st is None or st.status != "idle":
                continue
            deps = recipe.step(sid).deps
            if all(
                (dep_st := state.steps.get(d)) is not None and dep_st.status in ("done", "skipped")
                for d in deps
            ):
                runnable.append(sid)
        return runnable

    def get_step_output(self, instance_id: str, step_id: str) -> dict[str, Any]:
        """该步的定稿产物（driver 据此装配下游 ``step_inputs``）；未定稿则空 dict。"""
        st = self.store.get_step_state(instance_id, step_id)
        if st is None:
            raise FileNotFoundError(f"step not found: {instance_id}/{step_id}")
        return dict(st.outputs)

    async def approve_step(
        self,
        instance_id: str,
        step_id: str,
        decision: Literal["approved", "rejected"],
        *,
        edited_draft: dict[str, Any] | None = None,
        note: str | None = None,
        reviewer: Literal["user", "wolong", "system"] = "user",
    ) -> StepState:
        """``awaiting_review`` 的出口（否则该步死锁）。**按实例串行化**。

        - ``approved``：定稿。``content_edit`` 传 ``edited_draft`` 则以编辑稿为准
          （``draft_source="user"``）；产物 = 最终草稿，落 ``outputs`` 并置 ``done``。
        - ``rejected``：置 ``rejected``（rework/skip 由 driver 后续 ``reset_step``/略过决定）。

        两路都写 ``review``（喂 label_store / retro）并发 ``decision`` 事件。
        """
        async with self._lock_for(instance_id):
            # 先经 recipe 校验 iid（缺→FileNotFoundError）+ step_id（与 run_step/reset_step 对齐，
            # 不让 URL 来的 sid 裸奔进 store.get_step_state 做路径拼接）
            recipe = self._recipe_for(instance_id)
            if step_id not in recipe.step_ids():
                raise FileNotFoundError(f"step not found: {instance_id}/{step_id}")
            state = self.store.get_step_state(instance_id, step_id)
            if state is None:
                raise FileNotFoundError(f"step not found: {instance_id}/{step_id}")
            if state.status != "awaiting_review":
                raise ValueError(
                    f"approve_step 需 awaiting_review，实为 {state.status!r} ({step_id})"
                )
            state.review = Review(
                decision=decision, note=note, reviewed_at=_now_iso(), reviewer=reviewer
            )
            if decision == "approved":
                if edited_draft is not None:
                    state.draft = edited_draft
                    state.draft_source = "user"
                self._transition(instance_id, state, "approved", decision="approved")
                state.outputs = dict(state.draft or {})
                self._transition(instance_id, state, "done", finished_at=_now_iso())
            else:
                self._transition(instance_id, state, "rejected", decision="rejected")
            self._emit_decision(instance_id, step_id, decision, note)
            return state

    async def reset_step(self, instance_id: str, step_id: str, force: bool = False) -> list[str]:
        """硬重置该步 **及全部传递下游** 为 ``idle``（改稿后下游失效，design §10）。

        保留 ``config``（运行参数复用），清运行态（draft/outputs/decision/review/error/时间戳）。
        ``running`` 步默认不可重置（拒绝，防扯掉在跑的工作线程）；``force=True`` 时无条件重置——
        仅用于调用方已确证旧执行线程已死、引擎步残留 orphan ``running`` 的场景：watcher 宽限期后的
        asyncio 强制 cancel 只回收 facade、不碰引擎 step（见 pipeline_runner._execute_via_engine；
        run_node 的 _running_nodes 守卫保证重跑前旧 task 已 done，故 force 安全）。
        重置后重算 ``meta`` 状态（completed/failed 实例被重激活为 running）。返回被重置的 step_id（拓扑序）。
        """
        async with self._lock_for(instance_id):
            recipe = self._recipe_for(instance_id)
            if step_id not in recipe.step_ids():
                raise FileNotFoundError(f"step not found: {instance_id}/{step_id}")
            targets = self._with_descendants(recipe, step_id)
            if not force:
                running = [
                    sid for sid in targets
                    if (st := self.store.get_step_state(instance_id, sid)) is not None
                    and st.status == "running"
                ]
                if running:
                    raise ValueError(f"无法重置运行中的步: {running} ({instance_id})")
            reset_ids = [sid for sid in recipe.topological_order() if sid in targets]
            for sid in reset_ids:
                old = self.store.get_step_state(instance_id, sid)
                fresh = StepState(step_id=sid, config=dict(old.config) if old else {})
                self.store.write_step_state(instance_id, fresh)
                ev = InstanceEvent(
                    instance_id=instance_id, ts=_now_ms(), level="step", type="status",
                    step_id=sid, payload={"status": "idle", "reset": True},
                )
                self.store.append_step_event(instance_id, sid, ev)
                self.bus.publish(ev)
            self._finalize(instance_id)  # 终态实例被重激活；其余无副作用
            return reset_ids

    async def finalize_instance(self, instance_id: str) -> InstanceMeta:
        """据各步终态结算 ``meta.status``（driver 在推进后调，把 E0 停在 ``running`` 收口）。"""
        async with self._lock_for(instance_id):
            return self._finalize(instance_id)

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

    @staticmethod
    def _with_descendants(recipe: Recipe, step_id: str) -> set[str]:
        """``step_id`` 及其全部传递下游（直接/间接依赖它的步）。"""
        dependents: dict[str, set[str]] = {s.step_id: set() for s in recipe.steps}
        for s in recipe.steps:
            for dep in s.deps:
                if dep in dependents:
                    dependents[dep].add(s.step_id)
        out: set[str] = set()
        stack = [step_id]
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            out.add(cur)
            stack.extend(dependents.get(cur, ()))
        return out

    @staticmethod
    def _compute_meta_status(steps: dict[str, StepState]) -> InstanceStatus:
        """据各步状态推实例状态：任一 failed→failed；全 done/skipped→completed；
        全 idle→pending；否则 running。"""
        statuses = [s.status for s in steps.values()]
        if any(st == "failed" for st in statuses):
            return "failed"
        if statuses and all(st in ("done", "skipped") for st in statuses):
            return "completed"
        if statuses and all(st == "idle" for st in statuses):
            return "pending"
        return "running"

    def _finalize(self, instance_id: str) -> InstanceMeta:
        """（须在实例锁内）算 meta 状态并发 meta 事件。"""
        state = self.store.get_state(instance_id)
        if state is None:
            raise FileNotFoundError(f"instance not found: {instance_id}")
        return self._publish_meta_status(instance_id, self._compute_meta_status(state.steps))

    def _publish_meta_status(self, instance_id: str, status: InstanceStatus) -> InstanceMeta:
        """更新 meta 状态；**仅状态变化时**发 meta 级事件（app 视图订 level=meta）。"""
        meta = self.store.get_meta(instance_id)
        if meta is None:
            raise FileNotFoundError(f"instance not found: {instance_id}")
        if meta.status == status:
            return meta
        meta = self.store.update_meta_status(instance_id, status)
        ev = InstanceEvent(
            instance_id=instance_id, ts=_now_ms(), level="meta",
            type="status", payload={"status": status},
        )
        self.store.append_instance_event(instance_id, ev)
        self.bus.publish(ev)
        return meta

    def _emit_decision(
        self, instance_id: str, step_id: str, decision: str, note: str | None
    ) -> None:
        ev = InstanceEvent(
            instance_id=instance_id, ts=_now_ms(), level="step", type="decision",
            step_id=step_id, payload={"decision": decision, "note": note},
        )
        self.store.append_step_event(instance_id, step_id, ev)
        self.bus.publish(ev)

    @staticmethod
    def _merge_config(state: StepState, config: dict[str, Any] | None) -> None:
        """把运行期 config 合并进 ``StepState.config``（溯源："这步用了哪个模型/参数"）。
        仅在步骤真正(重)启动时调——终态步非法重跑不记 config（避免记下没真跑的参数）。"""
        if config:
            state.config = {**state.config, **config}

    def _start_running(
        self, instance_id: str, state: StepState, config: dict[str, Any] | None
    ) -> None:
        """把步骤推进到 ``running``：**守门在任何副作用之前**。

        先校验 ``idle/queued → running`` 合法（对 done/failed/awaiting_review 等终态重跑直接抛、
        不污染 meta/config）；过关后才落 config（溯源）、翻 meta running、步进 running。
        这样非法重跑是干净的 no-op 失败，不会把已结算实例的 meta 永久翻成 running，也不脏写 config。
        """
        if not can_transition(state.status, "running"):
            raise ValueError(
                f"step not runnable: {state.status!r} -> running ({state.step_id})；"
                f"重跑请先 reset_step"
            )
        self._merge_config(state, config)
        self._publish_meta_status(instance_id, "running")
        self._transition(instance_id, state, "running", started_at=_now_iso())

    def _transition(
        self,
        instance_id: str,
        state: StepState,
        new: StepStatus,
        *,
        started_at: str | None = None,
        finished_at: str | None = None,
        decision: Literal["approved", "rejected", "pending"] | None = None,
    ) -> None:
        if not can_transition(state.status, new):
            raise ValueError(f"illegal step transition {state.status!r} -> {new!r} ({state.step_id})")
        state.status = new
        if started_at is not None:
            state.started_at = started_at
        if finished_at is not None:
            state.finished_at = finished_at
        if decision is not None:
            state.decision = decision
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
