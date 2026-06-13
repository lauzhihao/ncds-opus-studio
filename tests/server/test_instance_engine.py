"""E0 生产引擎骨架单测：状态机 + 晚绑定派发 + 建实例→跑步→出事件→落 store。

派发用注入的 fake registry/recipe（hermetic，不跑真命令）；另有一条用例核真实 015 配方
的每个 performer 都能在 build_full_registry() 里解析（晚绑定 wiring 为真）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

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


_FAKE_REG = {"stub_ok": _fake_ok, "stub_boom": _fake_boom}

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


def _runner(tmp_path: Path) -> InstanceRunner:
    store = InstanceStore(tmp_path / "instances")
    return InstanceRunner(store, registry=dict(_FAKE_REG), recipes={"t": _FAKE_RECIPE})


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
