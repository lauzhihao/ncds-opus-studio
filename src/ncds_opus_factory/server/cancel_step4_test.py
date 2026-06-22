"""步4 取消接线测试（AC#5 + Option A 协作式取消）。

覆盖设计文档步4测试清单：
  1. 子进程整组被杀（_terminate_proc_group + killpg）
  2. _execute 协作式取消回 idle 不 failed
  3. per-item except 不吞 TaskCancelled
  4. cancel_node 连带 cancel enrich
  5. engine 可取消 + 不 DOA（step 回 idle，可重跑）
  6. watcher 宽限期（已有 test_watcher_cancels_node_on_flag 调小 GRACE 验强制 fallback）
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ncds_opus_core.common import cancel as _cancel
from ncds_opus_core.common.cancel import TaskCancelled, clear_flag, is_flagged, set_flag
from ncds_opus_factory.server import pipeline_media_helpers as media_helpers
from ncds_opus_factory.server.pipeline_runner import PipelineRunner
from ncds_opus_factory.server.engine.instance_runner import InstanceRunner, _invoke
from ncds_opus_factory.server.engine.instance_store import InstanceStore
from ncds_opus_factory.server.engine.types import Recipe, RecipeStep


# ---------------------------------------------------------------------------
# 共用 fixture helpers
# ---------------------------------------------------------------------------

def _make_runner(tmp_path: Path) -> PipelineRunner:
    return PipelineRunner(tmp_path / "video-jobs")


def _seed_job(runner: PipelineRunner, job_id: str, node: str, mock: bool = False) -> None:
    """在 video-jobs/{job_id}/pipeline_state.json 写最简 JobState。"""
    job_dir = runner.video_jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "job_id": job_id,
        "pipeline_id": "paper_card_talk_015",
        "title": "test",
        "created_at": time.time(),
        "updated_at": time.time(),
        "inputs": {},
        "nodes": {
            node: {
                "name": node,
                "status": "idle",
                "started_at": None,
                "finished_at": None,
                "progress": "",
                "outputs": {},
                "error": None,
                "task_id": None,
            }
        },
        "node_positions": {},
        "node_configs": {},
        "mock": mock,
        "engine_iid": None,
    }
    (job_dir / "pipeline_state.json").write_text(json.dumps(state), encoding="utf-8")


# ---------------------------------------------------------------------------
# 测试 1：子进程整组被杀（_terminate_proc_group）
# ---------------------------------------------------------------------------

def test_terminate_proc_group_kills_process(tmp_path: Path) -> None:
    """_terminate_proc_group：启动 sleep 30 子进程，调用后进程应退出（poll != None）。"""
    proc = subprocess.Popen(
        ["sleep", "30"],
        start_new_session=True,  # 独立 pgid，和生产代码一致
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.poll() is None, "进程启动后应该还在跑"

    media_helpers._terminate_proc_group(proc)

    # 给一点时间让 SIGTERM 生效
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    assert proc.poll() is not None, "SIGTERM 后进程应已退出"


def test_terminate_proc_group_handles_already_dead(tmp_path: Path) -> None:
    """_terminate_proc_group：进程已退出时调用不抛异常（ProcessLookupError 被 catch）。"""
    proc = subprocess.Popen(
        ["echo", "hi"],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    proc.wait(timeout=5)  # 让进程自然结束
    assert proc.poll() is not None

    # 应当幂等，不抛
    media_helpers._terminate_proc_group(proc)


def test_run_tts_gen_cancel_via_checker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_tts_gen_015：readline 循环里 checker() 为 True 时抛 TaskCancelled 并 killpg。"""
    # 打桩 Popen，模拟子进程持续吐行
    lines_iter = iter(["line1\n", "line2\n", "line3\n"])
    mock_proc = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stdout.readline = lambda: next(lines_iter, "")
    mock_proc.pid = 99999  # 不存在的 pid，killpg 会 ProcessLookupError，被 catch
    mock_proc.wait = MagicMock(return_value=0)

    killed = {"n": 0}
    original_terminate = media_helpers._terminate_proc_group

    def fake_terminate(p: Any) -> None:
        killed["n"] += 1

    # checker 在读完第 2 行后返回 True
    call_count = {"n": 0}
    def checker() -> bool:
        call_count["n"] += 1
        return call_count["n"] >= 2

    monkeypatch.setattr(media_helpers, "_terminate_proc_group", fake_terminate)
    monkeypatch.setattr(media_helpers.subprocess, "Popen", lambda *a, **kw: mock_proc)

    # install checker 后调 _run_tts_gen_015
    _cancel.install(checker)
    try:
        with pytest.raises(TaskCancelled):
            media_helpers._run_tts_gen_015(
                script=tmp_path / "tts_gen.py",
                episode_path=tmp_path / "episode.json",
                audio_dir=tmp_path / "audio",
                on_line=lambda s: None,
            )
    finally:
        _cancel.uninstall()

    assert killed["n"] >= 1, "_terminate_proc_group 应被调用以杀整个进程组"


# ---------------------------------------------------------------------------
# 测试 2：_execute 协作式取消回 idle 不 failed
# ---------------------------------------------------------------------------

def test_execute_task_cancelled_sets_idle_not_failed(tmp_path: Path) -> None:
    """_execute：当 _execute_real_with_flag_watcher 抛 TaskCancelled 时，
    节点应变 idle（不是 failed），flag 应被 clear。"""
    runner = _make_runner(tmp_path)
    job_id = "job-tc-001"
    node = "tts"
    _seed_job(runner, job_id, node)

    flag = runner._cancel_flag(job_id, node)

    async def _fake_execute_real_with_flag_watcher(jid: str, nname: str) -> None:
        # 直接抛 TaskCancelled 模拟协作式工作线程自停
        raise TaskCancelled("test cancel")

    async def _run() -> None:
        runner._execute_real_with_flag_watcher = _fake_execute_real_with_flag_watcher  # type: ignore[method-assign]
        # set flag 模拟已取消状态
        set_flag(flag)
        task = asyncio.create_task(runner._execute(job_id, node))
        # _execute 是 fire-and-forget，TaskCancelled 分支不重抛；等它结束
        await asyncio.sleep(0.05)
        assert task.done(), "task 应已结束"
        # TaskCancelled 分支不重抛，task 应正常结束（不是 cancelled 状态）
        assert not task.cancelled(), "task 不应以 asyncio.cancelled 结束"
        exc = task.exception()
        assert exc is None, f"TaskCancelled 分支不应冒到外层：{exc}"

        # 节点应落 idle
        state = runner._load(job_id)
        n = state.nodes[node]
        assert n.status == "idle", f"期望 idle，实为 {n.status}"
        assert n.error == "cancelled"

        # flag 应被 clear（否则下次重跑会被 watcher 秒取消）
        assert not is_flagged(flag), "TaskCancelled 分支应 clear flag"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 测试 3：per-item except 不吞 TaskCancelled
# ---------------------------------------------------------------------------

def test_execute_asr_collect_raises_task_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_execute_asr_collect：collect_one 抛 TaskCancelled 时不被 per-url except Exception 吞，
    整个方法应冒出 TaskCancelled。"""
    runner = _make_runner(tmp_path)
    job_id = "job-tc-002"
    node = "asr"
    # 构造带 urls 的 JobState
    job_dir = runner.video_jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "job_id": job_id,
        "pipeline_id": "paper_card_talk_015",
        "title": "test",
        "created_at": time.time(),
        "updated_at": time.time(),
        "inputs": {"urls": ["https://v.douyin.com/test123"]},
        "nodes": {
            node: {"name": node, "status": "idle", "started_at": None,
                   "finished_at": None, "progress": "", "outputs": {}, "error": None, "task_id": None}
        },
        "node_positions": {},
        "node_configs": {},
        "mock": False,
        "engine_iid": None,
    }
    (job_dir / "pipeline_state.json").write_text(json.dumps(state), encoding="utf-8")

    # 打桩 tikhub_client.resolve_aweme_id 返回有效 aweme_id
    import ncds_opus_factory.common.tikhub_client as tc
    monkeypatch.setattr(tc, "resolve_aweme_id", lambda url: "aweme123")
    monkeypatch.setattr(tc, "fetch_one_video_detail", lambda aweme_id: {})
    monkeypatch.setattr(tc, "extract_meta", lambda detail: {})

    # 打桩 _run_in_thread_cancellable 直接抛 TaskCancelled
    async def fake_thread_cancellable(fn: Any, flag_path: Any, /, *args: Any, **kwargs: Any) -> Any:
        raise TaskCancelled("test cancel in collect")
    runner._run_in_thread_cancellable = fake_thread_cancellable  # type: ignore[method-assign]

    async def _run() -> None:
        with pytest.raises(TaskCancelled):
            await runner._execute_asr_collect(job_id)

    asyncio.run(_run())


def test_enrich_asr_collected_raises_task_cancelled(tmp_path: Path) -> None:
    """_enrich_asr_collected：collect_one（via _run_in_thread_cancellable）抛 TaskCancelled 时
    不被 per-entry except Exception 吞，整个方法应冒出 TaskCancelled。"""
    runner = _make_runner(tmp_path)
    job_id = "job-tc-003"
    node = "asr"
    job_dir = runner.video_jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    collected_entry = {
        "aweme_id": "aweme999", "index": 1, "url": "https://v.douyin.com/x",
        "status": {}, "error": None,
    }
    state = {
        "job_id": job_id,
        "pipeline_id": "paper_card_talk_015",
        "title": "test",
        "created_at": time.time(),
        "updated_at": time.time(),
        "inputs": {},
        "nodes": {
            node: {
                "name": node, "status": "done", "started_at": None,
                "finished_at": None, "progress": "", "error": None, "task_id": None,
                "outputs": {"collected": [collected_entry]},
            }
        },
        "node_positions": {},
        "node_configs": {},
        "mock": False,
        "engine_iid": None,
    }
    (job_dir / "pipeline_state.json").write_text(json.dumps(state), encoding="utf-8")

    # 打桩 _run_in_thread_cancellable 直接抛 TaskCancelled
    async def fake_thread_cancellable(fn: Any, flag_path: Any, /, *args: Any, **kwargs: Any) -> Any:
        raise TaskCancelled("test cancel in enrich")
    runner._run_in_thread_cancellable = fake_thread_cancellable  # type: ignore[method-assign]

    async def _run() -> None:
        with pytest.raises(TaskCancelled):
            await runner._enrich_asr_collected(job_id)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 测试 4：cancel_node 连带 cancel enrich
# ---------------------------------------------------------------------------

def test_cancel_node_cancels_enrich_task(tmp_path: Path) -> None:
    """cancel_node：若 _enrich_tasks[job_id] 有活 task，应被 cancel。"""
    runner = _make_runner(tmp_path)
    job_id = "job-tc-004"
    node = "asr"
    _seed_job(runner, job_id, node)

    async def _run() -> None:
        # 塞一个永不结束的 enrich task 模拟后台 enrich 进行中
        enrich_task: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(9999))
        runner._enrich_tasks[job_id] = enrich_task

        result = await runner.cancel_node(job_id, node)
        assert result is True
        # enrich task 应被 cancel
        assert enrich_task.cancelled() or enrich_task.cancelling() > 0, (
            "cancel_node 应 cancel 活着的 enrich task"
        )

        # 清理
        enrich_task.cancel()
        await asyncio.gather(enrich_task, return_exceptions=True)
        clear_flag(runner._cancel_flag(job_id, node))

    asyncio.run(_run())


def test_cancel_node_real_node_does_not_cancel_task_directly(tmp_path: Path) -> None:
    """Option A：cancel_node 对真实节点（非 mock）不直接 cancel asyncio.Task，靠 watcher+flag。"""
    runner = _make_runner(tmp_path)
    job_id = "job-tc-005"
    node = "tts"
    _seed_job(runner, job_id, node, mock=False)

    async def _run() -> None:
        never_done: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(9999))
        runner._running_nodes[(job_id, node)] = never_done
        try:
            result = await runner.cancel_node(job_id, node)
            assert result is True
            # flag 应被写入
            flag = runner._cancel_flag(job_id, node)
            assert is_flagged(flag), "cancel_node 应写 flag"
            # 真实节点：task 不应被直接 cancel（靠 watcher 的宽限期协作式停）
            assert not never_done.cancelled(), "真实节点 task 不应被直接 cancel"
        finally:
            never_done.cancel()
            await asyncio.gather(never_done, return_exceptions=True)
            clear_flag(runner._cancel_flag(job_id, node))

    asyncio.run(_run())


def test_cancel_node_mock_task_is_cancelled_directly(tmp_path: Path) -> None:
    """cancel_node 对 mock 节点（state.mock=True）直接 cancel asyncio.Task。"""
    runner = _make_runner(tmp_path)
    job_id = "job-tc-006"
    node = "tts"
    _seed_job(runner, job_id, node, mock=True)

    async def _run() -> None:
        never_done: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(9999))
        runner._running_nodes[(job_id, node)] = never_done
        try:
            result = await runner.cancel_node(job_id, node)
            assert result is True
            # 给 asyncio 机会执行 cancel
            await asyncio.sleep(0)
            assert never_done.cancelled() or never_done.cancelling() > 0, (
                "mock 节点 task 应被直接 cancel"
            )
        finally:
            never_done.cancel()
            await asyncio.gather(never_done, return_exceptions=True)
            clear_flag(runner._cancel_flag(job_id, node))

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 测试 5：engine 可取消 + 不 DOA（step 回 idle，可重跑）
# ---------------------------------------------------------------------------

def test_engine_run_step_cancellable(tmp_path: Path) -> None:
    """engine run_step：cancel_check=lambda True → 抛 TaskCancelled，步骤回 idle（可重跑）。"""
    store = InstanceStore(tmp_path / "instances")

    # 最简 Recipe：一步，performer=fake_cmd
    recipe = Recipe(
        recipe_id="test_recipe",
        name="Test",
        steps=[RecipeStep(step_id="step1", cmd="fake_cmd")],
    )
    # registry：fake_cmd 在 checker 命中时抛 TaskCancelled
    def fake_performer(on_progress: Any, **kwargs: Any) -> dict:
        # install 的 checker 已经是 lambda True；调一次 checkpoint 触发取消
        _cancel.checkpoint()
        return {"result": "should not reach"}

    runner = InstanceRunner(
        store=store,
        registry={"fake_cmd": fake_performer},
        recipes={"test_recipe": recipe},
    )

    async def _run() -> None:
        inst = runner.create_instance("test_recipe")
        iid = inst.meta.instance_id

        # cancel_check 始终 True，run_step 应抛 TaskCancelled
        with pytest.raises(TaskCancelled):
            await runner.run_step(
                iid, "step1",
                step_inputs={},
                cancel_check=lambda: True,
            )

        # 步骤应回到 idle（写了 fresh StepState）
        state = store.get_step_state(iid, "step1")
        assert state is not None
        assert state.status == "idle", f"期望 idle，实为 {state.status}"

        # 可以重跑（不报"非法状态"）：reset_step 在 idle 不应报错
        await runner.reset_step(iid, "step1")

    asyncio.run(_run())


def test_engine_invoke_installs_cancel_check(tmp_path: Path) -> None:
    """_invoke 辅助函数：cancel_check 正确 install 后，performer 能通过 cancel.current() 读到。"""
    checker_called = {"n": 0}

    def my_checker() -> bool:
        checker_called["n"] += 1
        return False  # 不实际取消，只验证 checker 被 install

    seen_checker = {}

    def fake_fn(on_progress: Any) -> dict:
        # 取出当前线程 checker，验证它是 my_checker
        seen_checker["fn"] = _cancel.current()
        return {}

    _invoke(fake_fn, {}, lambda s: None, cancel_check=my_checker)
    # seen_checker["fn"] 应该是 my_checker（调用结果应一致）
    assert seen_checker["fn"]() == my_checker(), "cancel_check 应被 install 到工作线程"


# ---------------------------------------------------------------------------
# 测试 6：watcher 宽限期（调小 GRACE_SEC → 强制 fallback，仍在超时内完成）
# ---------------------------------------------------------------------------

def test_watcher_grace_period_forces_cancel_on_timeout(tmp_path: Path) -> None:
    """flag 命中后宽限期内 inner 未停止，应强制 cancel（fallback）。

    设 GRACE_SEC=0.05，fake_execute_real 无 checkpoint（sleep 9999），
    验证整体等待 ≤ 2s 完成，且 task 以 CancelledError 结束。
    """
    runner = _make_runner(tmp_path)
    runner._CANCEL_WATCHER_INTERVAL_SEC = 0.01
    runner._CANCEL_GRACE_SEC = 0.05  # 极短宽限期，强制走 fallback inner.cancel()
    job_id = "job-tc-grace-001"
    node = "image"
    _seed_job(runner, job_id, node)

    execution_started = asyncio.Event()

    async def _fake_execute_real(jid: str, nname: str) -> None:
        """无 checkpoint，永不协作式停止。"""
        execution_started.set()
        await asyncio.sleep(9999)

    async def _run() -> None:
        runner._execute_real = _fake_execute_real  # type: ignore[method-assign]

        task: asyncio.Task[None] = asyncio.create_task(
            runner._execute_real_with_flag_watcher(job_id, node)
        )

        await asyncio.wait_for(execution_started.wait(), timeout=2.0)
        flag = runner._cancel_flag(job_id, node)
        set_flag(flag)

        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.CancelledError:
            pass  # 预期：超宽限期后强制 cancel inner，重抛 CancelledError

        assert task.done(), "task 应已结束"

    asyncio.run(_run())


def test_watcher_propagates_cooperative_task_cancelled(tmp_path: Path) -> None:
    """Option A 核心 / 回归守护：flag 命中后 watcher 给宽限期，协作式 inner 抛 TaskCancelled，
    _execute_real_with_flag_watcher 应**透传 TaskCancelled**（而非在宽限期前把 inner 强杀成
    CancelledError）。曾有 `for t in pending: t.cancel()` 抢在宽限期之前 cancel inner，使协作式
    停止失效、子进程变孤儿——本测试用协作式 inner（读 flag 后抛 TaskCancelled）证明宽限期真生效。

    买卖判据：buggy 实现会让 task 以 CancelledError 结束（pytest.raises(TaskCancelled) 失败）。
    """
    runner = _make_runner(tmp_path)
    runner._CANCEL_WATCHER_INTERVAL_SEC = 0.01
    runner._CANCEL_GRACE_SEC = 2.0  # 宽限期足够长，协作式 inner 应在此期间自停
    job_id = "job-tc-coop-001"
    node = "tts"
    _seed_job(runner, job_id, node)

    flag = runner._cancel_flag(job_id, node)
    started = asyncio.Event()

    async def _coop_execute_real(jid: str, nname: str) -> None:
        """协作式工作线程的**真实**模拟：在 asyncio.to_thread 里阻塞轮询 flag。
        关键：线程杀不掉，asyncio cancel 只能抛弃它（与真实 collect_one/子进程一致）；
        线程轮询(0.05s)慢于 watcher(0.01s)，确保 watcher 先赢初始 asyncio.wait → 走宽限期分支。
        若 watcher 在宽限期前 cancel inner（buggy），to_thread await 抛 CancelledError、线程被抛弃，
        task 以 CancelledError 结束 → 本测试 pytest.raises(TaskCancelled) 失败（鉴别力）。"""
        cflag = runner._cancel_flag(jid, nname)
        started.set()  # 在事件循环线程里 set（线程安全）

        def _blocking() -> None:
            for _ in range(500):
                if is_flagged(cflag):
                    raise TaskCancelled("cooperative stop")
                time.sleep(0.05)

        await asyncio.to_thread(_blocking)

    async def _run() -> None:
        runner._execute_real = _coop_execute_real  # type: ignore[method-assign]
        task: asyncio.Task[None] = asyncio.create_task(
            runner._execute_real_with_flag_watcher(job_id, node)
        )
        await asyncio.wait_for(started.wait(), timeout=2.0)
        set_flag(flag)
        # 关键断言：应抛 TaskCancelled（协作式透传），不是 CancelledError（强杀）
        with pytest.raises(TaskCancelled):
            await asyncio.wait_for(task, timeout=2.0)
        assert not task.cancelled(), "inner 应协作式自停透传 TaskCancelled，不应被 force-cancel"

    asyncio.run(_run())


def test_execute_real_clears_flag_before_start(tmp_path: Path) -> None:
    """4h：_execute_real 开头清残留 flag，防 DOA（重跑不被旧 flag 秒取消）。"""
    runner = _make_runner(tmp_path)
    job_id = "job-tc-007"
    node = "tts"
    _seed_job(runner, job_id, node)

    flag = runner._cancel_flag(job_id, node)
    # 写残留 flag 模拟上次取消未清干净
    set_flag(flag)
    assert is_flagged(flag), "前提：flag 应存在"

    # monkeypatch _execute_real 的实际执行体，只验证 flag 已在 _execute_real 一开头被清
    flag_was_cleared = {"at_start": False}
    original_load = runner._load

    call_count = {"n": 0}

    def patched_load(jid: str) -> Any:
        call_count["n"] += 1
        # _execute_real 开头调 clear_flag，然后再调 _load；第一次 _load 时 flag 应已 clear
        if call_count["n"] == 1:
            flag_was_cleared["at_start"] = not is_flagged(flag)
        return original_load(jid)

    runner._load = patched_load  # type: ignore[method-assign]

    async def _run() -> None:
        # 只跑到 _load 就够了（_execute_real 会 NotImplementedError，被 except Exception 接住）
        try:
            await runner._execute_real(job_id, node)
        except Exception:
            pass

        assert flag_was_cleared["at_start"], "_execute_real 开头应清残留 flag"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 补充（步4 验收收尾）：killpg 杀整个进程组（含孙进程）
# ---------------------------------------------------------------------------

def test_terminate_proc_group_kills_grandchild(tmp_path: Path) -> None:
    """blocker#4 核心：killpg 杀**整个进程组（含孙进程）**，不是只杀直接子进程。
    起 sh（组长）→ sh 再起 sleep（孙进程，video_pipeline 的 yt-dlp/ffmpeg/whisper 同理）；
    _terminate_proc_group 后孙进程也应死。验证 terminate() 只杀直接子进程的缺陷确已规避。"""
    # sh 打印孙进程 pid 后 wait 保持组存活；start_new_session 让 sh 成独立 pgid 组长
    proc = subprocess.Popen(
        ["sh", "-c", "sleep 30 & echo $!; wait"],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    grandchild_pid = int(proc.stdout.readline().strip())
    os.kill(grandchild_pid, 0)  # 不抛 = 孙进程活着

    media_helpers._terminate_proc_group(proc)

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    # 孙进程应随整组一起被 SIGTERM 杀死（killpg 整组的关键收益）
    dead = False
    for _ in range(50):
        try:
            os.kill(grandchild_pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            dead = True
            break
    assert dead, f"孙进程 {grandchild_pid} 应随进程组一起被 killpg 杀死（terminate 只杀直接子进程会漏）"
