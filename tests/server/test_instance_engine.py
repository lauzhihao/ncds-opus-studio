"""E0 生产引擎骨架单测：状态机 + 晚绑定派发 + 建实例→跑步→出事件→落 store。

派发用注入的 fake registry/recipe（hermetic，不跑真命令）；另有一条用例核真实 015 配方
的每个 performer 都能在 build_full_registry() 里解析（晚绑定 wiring 为真）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ncds_opus_factory.commands import build_full_registry
from ncds_opus_factory.server.engine.instance_runner import InstanceRunner
from ncds_opus_factory.server.engine.instance_store import InstanceStore
from ncds_opus_factory.server.engine.recipes import PAPER_CARD_TALK_015
from ncds_opus_factory.server.engine.types import Recipe, RecipeStep, can_transition


# --------------------------------------------------------------------------- #
# 1) 状态机
# --------------------------------------------------------------------------- #
def test_step_state_machine_legal_and_illegal():
    # 合法路径
    assert can_transition("idle", "running")
    assert can_transition("running", "draft_ready")
    assert can_transition("draft_ready", "awaiting_review")
    assert can_transition("awaiting_review", "approved")
    assert can_transition("running", "done")
    assert can_transition("running", "failed")
    assert can_transition("idle", "done")        # 直通步
    assert can_transition("done", "done")         # 同态 no-op
    # 非法
    assert not can_transition("idle", "approved")
    assert not can_transition("done", "running")
    assert not can_transition("running", "awaiting_review")  # 必须先 draft_ready


# --------------------------------------------------------------------------- #
# 2) 真实 015 配方的晚绑定 wiring：每个 performer 都能在 registry 里解析
# --------------------------------------------------------------------------- #
def test_recipe_015_performers_resolve_in_registry():
    reg = build_full_registry()
    for step in PAPER_CARD_TALK_015.steps:
        if step.performer is not None:
            assert step.performer in reg, f"{step.step_id} 的 performer {step.performer} 不在 registry"
    # E0 目标步：render 经 core 的 render_015 派发
    assert "render_015" in reg
    order = PAPER_CARD_TALK_015.topological_order()
    assert order[0] == "input" and order[-1] == "download"


# --------------------------------------------------------------------------- #
# fake registry / recipe（hermetic 派发用）
# --------------------------------------------------------------------------- #
def _fake_ok(on_progress, **params):
    on_progress("1/2 启动")
    on_progress("2/2 完成")
    return {"ok": True, "echo": params}


def _fake_boom(on_progress, **params):
    raise RuntimeError("kaboom")


def _fake_closed(on_progress, x=0):
    """闭合签名（不吃 **kwargs）—— 证 config 不会被 splat 进 performer 而炸 TypeError。"""
    on_progress("closed")
    return {"x2": x * 2}


_FAKE_REG = {"stub_ok": _fake_ok, "stub_boom": _fake_boom, "stub_closed": _fake_closed}

_FAKE_RECIPE = Recipe(
    recipe_id="t",
    name="test recipe",
    steps=[
        RecipeStep(step_id="in", label="IN", kind="input"),
        RecipeStep(step_id="work", label="WORK", cmd="stub_ok", deps=["in"]),
        RecipeStep(step_id="gated", label="GATED", cmd="stub_ok", deps=["work"],
                   intervention="decision_only"),
        RecipeStep(step_id="boom", label="BOOM", cmd="stub_boom", deps=["work"]),
        RecipeStep(step_id="miss", label="MISS", cmd="not_registered", deps=["work"]),
    ],
)

# 线性链，b 是 content_edit 闸门，c/d 是 b 的（传递）下游 —— 测改稿级联失效 + 装配上游产物。
_FAKE_RECIPE_CE = Recipe(
    recipe_id="t2",
    name="content-edit chain",
    steps=[
        RecipeStep(step_id="a", label="A", cmd="stub_ok"),
        RecipeStep(step_id="b", label="B", cmd="stub_ok", deps=["a"],
                   intervention="content_edit"),
        RecipeStep(step_id="c", label="C", cmd="stub_ok", deps=["b"]),
        RecipeStep(step_id="d", label="D", cmd="stub_ok", deps=["c"]),
    ],
)


# 无 performer 的人工编辑/决策步（如 015 lines/storyboard/preview）+ 闭合签名 performer。
_FAKE_RECIPE_HUMAN = Recipe(
    recipe_id="t3",
    name="performer-less human-edit + closed-sig",
    steps=[
        RecipeStep(step_id="src", label="SRC", cmd="stub_ok"),
        RecipeStep(step_id="edit", label="EDIT", deps=["src"],
                   intervention="content_edit"),    # 无 cmd/agent
        RecipeStep(step_id="gate", label="GATE", deps=["edit"],
                   intervention="decision_only"),    # 无 cmd/agent
        RecipeStep(step_id="closed", label="CLOSED", cmd="stub_closed", deps=["gate"]),
        RecipeStep(step_id="out", label="OUT", kind="output", deps=["closed"]),
    ],
)


def _runner(tmp_path: Path) -> InstanceRunner:
    store = InstanceStore(tmp_path / "instances")
    return InstanceRunner(store, registry=dict(_FAKE_REG),
                          recipes={"t": _FAKE_RECIPE, "t2": _FAKE_RECIPE_CE,
                                   "t3": _FAKE_RECIPE_HUMAN})


# --------------------------------------------------------------------------- #
# 3) 建实例 → 落 store（每步 idle）
# --------------------------------------------------------------------------- #
def test_create_instance_persists_all_steps_idle(tmp_path: Path):
    runner = _runner(tmp_path)
    state = runner.create_instance("t", inputs={"seed": 1}, owner_id="demo")
    iid = state.meta.instance_id
    assert iid.startswith("i_")
    assert state.meta.status == "pending"
    assert state.meta.owner_id == "demo"
    assert state.inputs == {"seed": 1}
    assert set(state.steps) == {"in", "work", "gated", "boom", "miss"}
    assert all(s.status == "idle" for s in state.steps.values())
    # 落盘可重读
    reloaded = runner.store.get_state(iid)
    assert reloaded is not None and set(reloaded.steps) == set(state.steps)


# --------------------------------------------------------------------------- #
# 4) 跑步派发 → done + 出事件（detail in jsonl, step in bus）+ 落 store
# --------------------------------------------------------------------------- #
def test_run_step_dispatch_done_with_events(tmp_path: Path):
    runner = _runner(tmp_path)
    state = runner.create_instance("t")
    iid = state.meta.instance_id
    q = runner.bus.subscribe(iid, {"meta", "step", "detail"})

    st = asyncio.run(runner.run_step(iid, "work", step_inputs={"x": 42}))

    assert st.status == "done"
    assert st.outputs == {"ok": True, "echo": {"x": 42}}
    assert st.started_at and st.finished_at
    # 落 store
    persisted = runner.store.get_step_state(iid, "work")
    assert persisted.status == "done" and persisted.outputs == st.outputs
    # 实例状态被推到 running
    assert runner.store.get_meta(iid).status == "running"
    # 步级事件进 bus（detail 不进 bus，只进 jsonl）
    bus_events = []
    while not q.empty():
        bus_events.append(q.get_nowait())
    assert any(e.level == "step" and e.type == "status" and e.payload.get("status") == "running"
               for e in bus_events)
    assert any(e.level == "step" and e.payload.get("status") == "done" for e in bus_events)
    assert all(e.level != "detail" for e in bus_events)
    # detail(progress) 进 step 的 events.jsonl
    lines = runner.store.step_events_path(iid, "work").read_text(encoding="utf-8").splitlines()
    evs = [json.loads(line) for line in lines if line.strip()]
    progress = [e for e in evs if e.get("type") == "progress"]
    assert [e["payload"]["text"] for e in progress] == ["1/2 启动", "2/2 完成"]
    assert any(e.get("type") == "status" and e["payload"]["status"] == "done" for e in evs)


# --------------------------------------------------------------------------- #
# 5) 有 intervention 的步 → 停在 awaiting_review + 草稿就位
# --------------------------------------------------------------------------- #
def test_run_step_intervention_awaits_review(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    st = asyncio.run(runner.run_step(iid, "gated", step_inputs={"a": 1}))
    assert st.status == "awaiting_review"
    assert st.decision == "pending"
    assert st.draft == {"ok": True, "echo": {"a": 1}}
    assert st.draft_source == "agent"
    assert not st.outputs  # 未定稿


# --------------------------------------------------------------------------- #
# 6) 无执行体的步（input）→ 直通 done
# --------------------------------------------------------------------------- #
def test_run_step_noperformer_passthrough(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    st = asyncio.run(runner.run_step(iid, "in"))
    assert st.status == "done"


# --------------------------------------------------------------------------- #
# 7/8) 步骤抛错 / performer 缺失 → failed（不冒泡炸 driver）
# --------------------------------------------------------------------------- #
def test_run_step_failure_captured(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    st = asyncio.run(runner.run_step(iid, "boom"))
    assert st.status == "failed"
    assert "kaboom" in (st.error or "")
    assert runner.store.get_step_state(iid, "boom").status == "failed"


def test_run_step_registry_miss_fails(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    st = asyncio.run(runner.run_step(iid, "miss"))
    assert st.status == "failed"
    assert "no performer" in (st.error or "")


# --------------------------------------------------------------------------- #
# 9) 默认构造（真实 build_full_registry + 真实 015 配方）建实例 + 跑直通步
#    —— 不跑真 render（需 ffmpeg/node_modules），只证默认 runner 的 create/run/store 链通
# --------------------------------------------------------------------------- #
def test_default_runner_real_015_create_and_passthrough(tmp_path: Path):
    store = InstanceStore(tmp_path / "instances")
    runner = InstanceRunner(store)  # 默认：build_full_registry() + RECIPE_REGISTRY
    state = runner.create_instance("paper_card_talk_015", inputs={"urls": ["x"]})
    iid = state.meta.instance_id
    assert set(state.steps) == set(PAPER_CARD_TALK_015.step_ids())
    # render 步的 performer 在真实 registry 里可解析（晚绑定 wiring 成立）
    assert runner.registry.get(PAPER_CARD_TALK_015.step("render").performer) is not None
    # 跑直通 input 步：create→run→落 store 全链通
    st = asyncio.run(runner.run_step(iid, "input"))
    assert st.status == "done"
    assert runner.store.get_step_state(iid, "input").status == "done"


# --------------------------------------------------------------------------- #
# 10) 配方自检：悬空 deps / 成环 / 重复 step_id 早抛；015 通过
# --------------------------------------------------------------------------- #
def test_recipe_validate_rejects_bad_recipes():
    PAPER_CARD_TALK_015.validate()  # 内置配方应通过，不抛

    dangling = Recipe(recipe_id="d", name="x",
                      steps=[RecipeStep(step_id="a", deps=["nope"])])
    with pytest.raises(ValueError, match="不存在"):
        dangling.validate()

    cyclic = Recipe(recipe_id="c", name="x", steps=[
        RecipeStep(step_id="a", deps=["b"]),
        RecipeStep(step_id="b", deps=["a"]),
    ])
    with pytest.raises(ValueError, match="成环"):
        cyclic.validate()

    dup = Recipe(recipe_id="dup", name="x",
                 steps=[RecipeStep(step_id="a"), RecipeStep(step_id="a")])
    with pytest.raises(ValueError, match="重复"):
        dup.validate()


def test_create_instance_validates_recipe(tmp_path: Path):
    bad = Recipe(recipe_id="bad", name="x",
                 steps=[RecipeStep(step_id="a", deps=["ghost"])])
    store = InstanceStore(tmp_path / "instances")
    runner = InstanceRunner(store, registry={}, recipes={"bad": bad})
    with pytest.raises(ValueError):
        runner.create_instance("bad")


# --------------------------------------------------------------------------- #
# 11) 并发 run_step 同一实例（不同步）：按实例锁串行化，两步都完成、不死锁、meta 一致
# --------------------------------------------------------------------------- #
def test_concurrent_run_step_same_instance_serializes(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id

    async def _both():
        return await asyncio.gather(
            runner.run_step(iid, "work", step_inputs={"x": 1}),
            runner.run_step(iid, "gated", step_inputs={"y": 2}),
        )

    work, gated = asyncio.run(_both())
    assert work.status == "done"
    assert gated.status == "awaiting_review"
    # 两步落盘一致，meta 没被竞态写坏
    assert runner.store.get_step_state(iid, "work").status == "done"
    assert runner.store.get_step_state(iid, "gated").status == "awaiting_review"
    assert runner.store.get_meta(iid).status == "running"


# =========================================================================== #
# E1 driver API
# =========================================================================== #

# --------------------------------------------------------------------------- #
# 12) run_step(config=) → 落 StepState.config 作溯源；config **不** splat 进 performer
# --------------------------------------------------------------------------- #
def test_run_step_config_persists_as_provenance(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    st = asyncio.run(runner.run_step(iid, "work", step_inputs={"x": 1}, config={"model": "gpt"}))
    assert st.status == "done"
    # config 落 StepState.config（溯源："这步用了哪个模型/参数"）
    assert st.config == {"model": "gpt"}
    assert runner.store.get_step_state(iid, "work").config == {"model": "gpt"}
    # 但 config **不**进 performer 参数 —— echo 只有 step_inputs，没有 model
    assert st.outputs["echo"] == {"x": 1}


def test_run_step_config_not_splatted_into_closed_signature_performer(tmp_path: Path):
    # 真实命令是闭合签名（rw/wst/render_015…）；config 含非参数 key（model/profile）
    # 若被 splat 进去必 TypeError → 步骤静默 failed。这里用闭合签名 performer 钉死回归。
    runner = _runner(tmp_path)
    iid = runner.create_instance("t3").meta.instance_id
    st = asyncio.run(runner.run_step(iid, "closed", step_inputs={"x": 3},
                                     config={"model": "gpt", "profile": "hi"}))
    assert st.status == "done"          # 没因 unexpected kwarg 炸成 failed
    assert st.outputs == {"x2": 6}      # performer 只吃 step_inputs.x
    assert st.config == {"model": "gpt", "profile": "hi"}   # config 仍记下作溯源


# --------------------------------------------------------------------------- #
# 13) get_runnable_steps：idle 且 deps 全 done/skipped，按拓扑序
# --------------------------------------------------------------------------- #
def test_get_runnable_steps_follows_deps(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    # 初始：只有无依赖的 input 步可跑（work 依赖 in 仍 idle）
    assert runner.get_runnable_steps(iid) == ["in"]
    asyncio.run(runner.run_step(iid, "in"))
    assert runner.get_runnable_steps(iid) == ["work"]
    asyncio.run(runner.run_step(iid, "work"))
    # work 定稿后，下游三步都可跑（拓扑序内 work 之后的输入顺序）
    assert runner.get_runnable_steps(iid) == ["gated", "boom", "miss"]


def test_get_runnable_steps_missing_instance_raises(tmp_path: Path):
    runner = _runner(tmp_path)
    with pytest.raises(FileNotFoundError):
        runner.get_runnable_steps("i_nope")


# --------------------------------------------------------------------------- #
# 14) get_step_output：定稿产物；未定稿空 dict；缺失步报错
# --------------------------------------------------------------------------- #
def test_get_step_output(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    assert runner.get_step_output(iid, "work") == {}     # 未跑
    asyncio.run(runner.run_step(iid, "work", step_inputs={"x": 9}))
    assert runner.get_step_output(iid, "work") == {"ok": True, "echo": {"x": 9}}
    with pytest.raises(FileNotFoundError):
        runner.get_step_output(iid, "ghost")


# --------------------------------------------------------------------------- #
# 15) approve_step approved（decision_only）→ done + outputs=draft + review + decision 事件
# --------------------------------------------------------------------------- #
def test_approve_step_approved_finalizes(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    asyncio.run(runner.run_step(iid, "gated", step_inputs={"a": 1}))
    q = runner.bus.subscribe(iid, {"meta", "step", "detail"})
    st = asyncio.run(runner.approve_step(iid, "gated", "approved", note="lgtm"))
    assert st.status == "done"
    assert st.decision == "approved"
    assert st.outputs == {"ok": True, "echo": {"a": 1}}   # 定稿 = 草稿
    assert st.review.decision == "approved" and st.review.note == "lgtm"
    assert st.review.reviewer == "user"
    # 落盘
    assert runner.store.get_step_state(iid, "gated").status == "done"
    # decision 事件进 bus
    evs = []
    while not q.empty():
        evs.append(q.get_nowait())
    assert any(e.type == "decision" and e.payload.get("decision") == "approved" for e in evs)


# --------------------------------------------------------------------------- #
# 16) approve_step approved + edited_draft（content_edit）→ 编辑稿定稿，draft_source=user
# --------------------------------------------------------------------------- #
def test_approve_step_with_edited_draft(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t2").meta.instance_id
    asyncio.run(runner.run_step(iid, "a"))
    asyncio.run(runner.run_step(iid, "b"))   # content_edit → awaiting_review
    assert runner.store.get_step_state(iid, "b").status == "awaiting_review"
    st = asyncio.run(runner.approve_step(iid, "b", "approved",
                                         edited_draft={"text": "人改的稿"}))
    assert st.status == "done"
    assert st.draft == {"text": "人改的稿"}
    assert st.draft_source == "user"
    assert st.outputs == {"text": "人改的稿"}


# --------------------------------------------------------------------------- #
# 17) approve_step rejected → rejected + review 记录，未定稿
# --------------------------------------------------------------------------- #
def test_approve_step_rejected(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    asyncio.run(runner.run_step(iid, "gated"))
    st = asyncio.run(runner.approve_step(iid, "gated", "rejected", note="质量不行"))
    assert st.status == "rejected"
    assert st.decision == "rejected"
    assert st.review.decision == "rejected" and st.review.note == "质量不行"
    assert not st.outputs


# --------------------------------------------------------------------------- #
# 18) approve_step 非 awaiting_review → 报错（不能批一个没在等审的步）
# --------------------------------------------------------------------------- #
def test_approve_step_wrong_state_raises(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    with pytest.raises(ValueError, match="awaiting_review"):
        asyncio.run(runner.approve_step(iid, "work", "approved"))  # work 还是 idle


# --------------------------------------------------------------------------- #
# 19) reset_step：级联重置该步 + 全部传递下游为 idle，上游不动，保留 config
# --------------------------------------------------------------------------- #
def test_reset_step_cascades_downstream(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t2").meta.instance_id
    asyncio.run(runner.run_step(iid, "a", config={"q": "hi"}))
    asyncio.run(runner.run_step(iid, "b"))
    asyncio.run(runner.approve_step(iid, "b", "approved"))
    asyncio.run(runner.run_step(iid, "c"))
    asyncio.run(runner.run_step(iid, "d"))
    assert all(runner.store.get_step_state(iid, s).status == "done" for s in ("a", "b", "c", "d"))

    reset = asyncio.run(runner.reset_step(iid, "b"))
    assert reset == ["b", "c", "d"]               # 拓扑序，含 b + 传递下游
    assert runner.store.get_step_state(iid, "a").status == "done"   # 上游不动
    for s in ("b", "c", "d"):
        rst = runner.store.get_step_state(iid, s)
        assert rst.status == "idle"
        assert not rst.outputs and rst.decision is None and rst.review is None
    # a 的 config 保留（重跑复用运行参数）
    assert runner.store.get_step_state(iid, "a").config == {"q": "hi"}


def test_reset_step_preserves_config_on_reset_target(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t2").meta.instance_id
    asyncio.run(runner.run_step(iid, "a", config={"model": "x"}))
    asyncio.run(runner.reset_step(iid, "a"))
    rst = runner.store.get_step_state(iid, "a")
    assert rst.status == "idle"
    assert rst.config == {"model": "x"}       # 保留 config，清运行态
    assert not rst.outputs


def test_reset_step_refuses_running(tmp_path: Path):
    from ncds_opus_factory.server.engine.types import StepState
    runner = _runner(tmp_path)
    iid = runner.create_instance("t2").meta.instance_id
    # 手动把 a 置 running，模拟在跑
    runner.store.write_step_state(iid, StepState(step_id="a", status="running"))
    with pytest.raises(ValueError, match="运行中"):
        asyncio.run(runner.reset_step(iid, "a"))


def test_reset_step_missing_raises(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t2").meta.instance_id
    with pytest.raises(FileNotFoundError):
        asyncio.run(runner.reset_step(iid, "ghost"))


# --------------------------------------------------------------------------- #
# 20) reset_step 重激活已结算实例：completed → running
# --------------------------------------------------------------------------- #
def test_reset_step_reactivates_finalized_instance(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t2").meta.instance_id
    for s in ("a", "b"):
        asyncio.run(runner.run_step(iid, s))
    asyncio.run(runner.approve_step(iid, "b", "approved"))
    for s in ("c", "d"):
        asyncio.run(runner.run_step(iid, s))
    assert asyncio.run(runner.finalize_instance(iid)).status == "completed"
    asyncio.run(runner.reset_step(iid, "c"))
    assert runner.store.get_meta(iid).status == "running"   # 有 idle 步 → 重激活


# --------------------------------------------------------------------------- #
# 21) finalize_instance：全 done→completed / 任一 failed→failed / 全 idle→pending / 否则 running
# --------------------------------------------------------------------------- #
def test_finalize_instance_completed(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t2").meta.instance_id
    for s in ("a", "b"):
        asyncio.run(runner.run_step(iid, s))
    asyncio.run(runner.approve_step(iid, "b", "approved"))
    for s in ("c", "d"):
        asyncio.run(runner.run_step(iid, s))
    q = runner.bus.subscribe(iid, {"meta"})
    meta = asyncio.run(runner.finalize_instance(iid))
    assert meta.status == "completed"
    # meta 级事件出
    evs = []
    while not q.empty():
        evs.append(q.get_nowait())
    assert any(e.level == "meta" and e.payload.get("status") == "completed" for e in evs)


def test_finalize_instance_failed(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    asyncio.run(runner.run_step(iid, "boom"))   # → failed
    meta = asyncio.run(runner.finalize_instance(iid))
    assert meta.status == "failed"


def test_finalize_instance_pending_and_running(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t2").meta.instance_id
    assert asyncio.run(runner.finalize_instance(iid)).status == "pending"   # 全 idle
    asyncio.run(runner.run_step(iid, "a"))
    assert asyncio.run(runner.finalize_instance(iid)).status == "running"   # 部分推进


# =========================================================================== #
# E1 评审加固（adversarial review fixes）
# =========================================================================== #

# --------------------------------------------------------------------------- #
# 22) 闸门判据是 intervention 不是 performer：无执行体的 content_edit 步必须停 awaiting_review
#     （否则 015 的 lines/storyboard/preview 被静默直通，强制闸门失效）
# --------------------------------------------------------------------------- #
def test_performerless_intervention_step_awaits_review(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t3").meta.instance_id
    st = asyncio.run(runner.run_step(iid, "edit", step_inputs={"beats": [1, 2]}))
    assert st.status == "awaiting_review"        # 不再静默直通 done
    assert st.decision == "pending"
    assert st.draft == {"beats": [1, 2]}         # 上游装配的 step_inputs 作初始草稿
    assert st.draft_source == "agent"
    assert not st.outputs
    # 人改稿 → 定稿
    done = asyncio.run(runner.approve_step(iid, "edit", "approved", edited_draft={"beats": [9]}))
    assert done.status == "done"
    assert done.outputs == {"beats": [9]}
    assert done.draft_source == "user"


def test_performerless_decision_only_step_awaits_review(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t3").meta.instance_id
    st = asyncio.run(runner.run_step(iid, "gate"))   # 无 step_inputs → 空草稿
    assert st.status == "awaiting_review"
    assert st.draft == {}


def test_real_015_performerless_content_edit_steps_gate(tmp_path: Path):
    # 真实 015 配方：lines/storyboard/preview 都是无 cmd 的 content_edit 步，必须停在闸门
    store = InstanceStore(tmp_path / "instances")
    runner = InstanceRunner(store)   # 真实 build_full_registry + RECIPE_REGISTRY
    iid = runner.create_instance("paper_card_talk_015", inputs={"urls": ["x"]}).meta.instance_id
    for sid in ("lines", "storyboard", "preview"):
        st = asyncio.run(runner.run_step(iid, sid, step_inputs={"k": sid}))
        assert st.status == "awaiting_review", f"{sid} 应停在闸门，实为 {st.status}"
        assert st.draft == {"k": sid}


# --------------------------------------------------------------------------- #
# 23) 对终态步非法重跑：抛错且**无副作用**（meta 不翻 running、config 不脏写）
# --------------------------------------------------------------------------- #
def test_rerun_terminal_step_clean_failure_no_side_effect(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t2").meta.instance_id
    for s in ("a", "b"):
        asyncio.run(runner.run_step(iid, s))
    asyncio.run(runner.approve_step(iid, "b", "approved"))
    for s in ("c", "d"):
        asyncio.run(runner.run_step(iid, s))
    assert asyncio.run(runner.finalize_instance(iid)).status == "completed"
    with pytest.raises(ValueError, match="not runnable"):
        asyncio.run(runner.run_step(iid, "a", config={"z": 9}))
    assert runner.store.get_meta(iid).status == "completed"      # meta 未被翻 running
    assert runner.store.get_step_state(iid, "a").config == {}    # config 未脏写


# --------------------------------------------------------------------------- #
# 24) 直通步真正推进时翻 meta running（不再滞留 pending）
# --------------------------------------------------------------------------- #
def test_passthrough_step_flips_meta_running(tmp_path: Path):
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    assert runner.store.get_meta(iid).status == "pending"
    asyncio.run(runner.run_step(iid, "in"))               # 直通 input 步
    assert runner.store.get_meta(iid).status == "running"  # 不再滞留 pending


def test_passthrough_step_persists_config(tmp_path: Path):
    # 直通步也守 config 溯源契约（与 performer/intervention 分支一致，不漏记）
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    st = asyncio.run(runner.run_step(iid, "in", config={"model": "gpt"}))
    assert st.status == "done"
    assert st.config == {"model": "gpt"}
    assert runner.store.get_step_state(iid, "in").config == {"model": "gpt"}


def test_rerun_done_passthrough_step_no_spurious_running(tmp_path: Path):
    # 重跑已 done 的直通步是 no-op：不能把已结算实例的 meta 翻回 running
    runner = _runner(tmp_path)
    iid = runner.create_instance("t").meta.instance_id
    asyncio.run(runner.run_step(iid, "in"))
    # 人为结算成 completed（把其余步当 skipped 处理：直接置 done 不便，这里改测 meta 不被 in 重跑污染）
    runner.store.update_meta_status(iid, "completed")
    asyncio.run(runner.run_step(iid, "in"))               # done → done no-op
    assert runner.store.get_meta(iid).status == "completed"  # 未被翻 running
