"""E1-b2 #3 绞杀者：PipelineRunner 的节点执行经 NOF_ENGINE_NODES 改走生产引擎 run_step。

hermetic：注入一个假引擎（不真跑 performer），验证路由 + content_edit 自动定稿 + 默认不改道。
JobState 仍是 facade 真相源；引擎实例只作执行载体。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ncds_opus_factory.server.engine.types import StepState
from ncds_opus_factory.server.pipeline_runner import PipelineRunner


class _FakeEngine:
    """最小假引擎：记录调用，run_step 返回可配置终态（done / awaiting_review）。"""

    def __init__(self, run_status: str = "done") -> None:
        self._run_status = run_status
        self._exists: set[str] = set()
        self.calls: list[tuple] = []
        self.store = SimpleNamespace(exists=lambda iid: iid in self._exists)

    def create_instance(self, recipe_id: str, inputs=None):
        iid = "i_fake_1"
        self._exists.add(iid)
        self.calls.append(("create", recipe_id, inputs))
        return SimpleNamespace(meta=SimpleNamespace(instance_id=iid))

    async def reset_step(self, iid: str, node: str, force: bool = False):
        # force：对齐真 InstanceRunner.reset_step（pipeline_runner 重跑前会带 force= 清 orphan running）。
        self.calls.append(("reset", iid, node))
        return [node]

    async def run_step(self, iid: str, node: str, step_inputs, on_progress=None, cancel_check=None):
        self.calls.append(("run", iid, node, step_inputs))
        if on_progress is not None:
            on_progress(f"{node} 引擎进度")        # 透传回 facade（绞杀者桥接）
        return StepState(step_id=node, status=self._run_status,
                         outputs={} if self._run_status != "done" else {"engine_ran": node},
                         draft={"engine_ran": node} if self._run_status == "awaiting_review" else None)

    async def approve_step(self, iid: str, node: str, decision: str):
        self.calls.append(("approve", iid, node, decision))
        return StepState(step_id=node, status="done", outputs={"engine_ran": node, "approved": True})


def _job(runner: PipelineRunner) -> str:
    return runner.create_job("final_preview", "T", {"urls": ["https://x"]}).job_id


def test_flagged_node_routes_through_engine(tmp_path: Path):
    runner = PipelineRunner(tmp_path / "video-jobs")
    engine = _FakeEngine(run_status="done")
    runner.attach_engine(engine)
    runner._engine_nodes = {"render"}          # 只让 render 改道
    jid = _job(runner)

    import asyncio
    asyncio.run(runner._execute_real(jid, "render"))   # 直接调（绕过 run_node 的 dep 检查）

    # 走了引擎：create + reset + run（render）
    kinds = [c[0] for c in engine.calls]
    assert kinds == ["create", "reset", "run"]
    run_call = next(c for c in engine.calls if c[0] == "run")
    assert run_call[2] == "render"
    assert run_call[3]["job_dir"].endswith(jid)        # 传入真实 job_dir
    # 引擎 outputs 落进 JobState（facade）
    job = runner.get_job(jid)
    assert job.nodes["render"].status == "done"
    assert job.nodes["render"].outputs == {"engine_ran": "render"}


def test_rw_stays_legacy_even_when_engine_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # rw 需要逐模型 model_progress/drafts 增量推送，暂时固定走 legacy 路径。
    runner = PipelineRunner(tmp_path / "video-jobs")
    engine = _FakeEngine(run_status="done")
    runner.attach_engine(engine)
    runner._engine_nodes = {"rw"}
    jid = _job(runner)
    called = {"rw": False}

    async def _stub_rw(job_id):
        called["rw"] = True
        return {"via": "legacy_rw"}

    monkeypatch.setattr(runner, "_execute_rw", _stub_rw)
    import asyncio
    asyncio.run(runner._execute_real(jid, "rw"))

    assert called["rw"] is True
    assert engine.calls == []
    assert runner.get_job(jid).nodes["rw"].outputs == {"via": "legacy_rw"}


def test_engine_iid_persisted_for_reuse(tmp_path: Path):
    # engine_iid 落进 JobState（持久化），重启后能复用同一实例、不留孤儿
    runner = PipelineRunner(tmp_path / "video-jobs")
    runner.attach_engine(_FakeEngine())
    runner._engine_nodes = {"render"}
    jid = _job(runner)
    import asyncio
    asyncio.run(runner._execute_real(jid, "render"))
    assert runner.get_job(jid).engine_iid == "i_fake_1"        # 已持久化
    # 重建 runner（模拟 server 重启）：engine_iid 从盘恢复，复用不重建
    runner2 = PipelineRunner(tmp_path / "video-jobs")
    eng2 = _FakeEngine()
    eng2._exists.add("i_fake_1")                                # 实例仍在盘
    runner2.attach_engine(eng2)
    runner2._engine_nodes = {"render"}
    asyncio.run(runner2._execute_real(jid, "render"))
    assert ("create",) not in [(c[0],) for c in eng2.calls]    # 没再 create（复用旧实例）


def test_progress_bridged_to_facade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 引擎 performer 的 on_progress 经绞杀者桥回 facade _push_progress（修进度文本冻结）
    runner = PipelineRunner(tmp_path / "video-jobs")
    runner.attach_engine(_FakeEngine())
    runner._engine_nodes = {"render"}
    jid = _job(runner)
    pushed: list[tuple[str, str]] = []
    monkeypatch.setattr(runner, "_push_progress",
                        lambda job_id, node, text: pushed.append((node, text)))
    import asyncio
    asyncio.run(runner._execute_real(jid, "render"))
    assert ("render", "render 引擎进度") in pushed


def test_content_edit_node_auto_approves(tmp_path: Path):
    # lines/storyboard 是 content_edit 步：引擎 run_step 停 awaiting_review → facade 自动 approve 定稿
    runner = PipelineRunner(tmp_path / "video-jobs")
    engine = _FakeEngine(run_status="awaiting_review")
    runner.attach_engine(engine)
    runner._engine_nodes = {"lines"}
    jid = _job(runner)

    import asyncio
    asyncio.run(runner._execute_real(jid, "lines"))

    assert ("approve", "i_fake_1", "lines", "approved") in engine.calls
    job = runner.get_job(jid)
    assert job.nodes["lines"].status == "done"
    assert job.nodes["lines"].outputs == {"engine_ran": "lines", "approved": True}


def test_unflagged_node_does_not_route_to_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 默认 flag 空：节点仍走旧 _execute_*，不碰引擎
    runner = PipelineRunner(tmp_path / "video-jobs")
    engine = _FakeEngine()
    runner.attach_engine(engine)
    runner._engine_nodes = set()               # 空 = 全不改道
    jid = _job(runner)

    called = {"render": False}

    async def _stub_render(job_id):
        called["render"] = True
        return {"via": "pipeline_runner"}

    monkeypatch.setattr(runner, "_execute_render", _stub_render)
    import asyncio
    asyncio.run(runner._execute_real(jid, "render"))

    assert called["render"] is True            # 走了旧路径
    assert engine.calls == []                  # 没碰引擎
    assert runner.get_job(jid).nodes["render"].outputs == {"via": "pipeline_runner"}
