"""tests：P2 调度器——per-cmd 队列/并发额度/配额/出队 CAS/启动恢复/自动归档/递归闸。"""

from __future__ import annotations

import asyncio
import importlib
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ncds_opus_factory.common import cancel
from ncds_opus_factory.server import task_runner as tr
from ncds_opus_factory.server.label_store import LabelStore
from ncds_opus_factory.server.schemas import TaskMeta
from ncds_opus_factory.server.task_store import TaskStore


def _ok(on_progress=None, **kw):
    return {"ok": True}


async def _wait_status(store: TaskStore, ids: list[str], want: str, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all((store.get_meta(i) or TaskMeta(task_id="x", cmd="x", params={}, created_at="")).status == want
               for i in ids):
            return True
        await asyncio.sleep(0.05)
    return False


def test_concurrency_cap(tmp_path: Path, monkeypatch):
    """同 cmd 并发不超过额度（5 个任务、额度 2,峰值并发 ≤2 且全部完成）。"""
    monkeypatch.setitem(tr.CONCURRENCY, "x", 2)
    peak = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow(on_progress=None, **kw):
        with lock:
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
        time.sleep(0.25)
        with lock:
            peak["now"] -= 1
        return {"ok": True}

    async def main():
        store = TaskStore(tmp_path / "tasks")
        runner = tr.TaskRunner(store, {"x": slow})
        await runner.recover_and_start()
        ids = [await runner.submit("x", {}) for _ in range(5)]
        assert await _wait_status(store, ids, "completed")
        return store, ids

    asyncio.run(main())
    assert peak["max"] <= 2
    assert peak["max"] >= 2  # 真的并行过,不是串行侥幸通过


def test_quota_fail_fast_and_gate_exempt(tmp_path: Path, monkeypatch):
    """派单类超配额直接 failed 并注明原因；gate/retro 豁免照常执行。"""
    monkeypatch.setitem(tr.DAILY_QUOTA, "x", 2)

    async def main():
        store = TaskStore(tmp_path / "tasks")
        runner = tr.TaskRunner(store, {"x": _ok})
        await runner.recover_and_start()
        ok1 = await runner.submit("x", {})
        ok2 = await runner.submit("x", {})
        over = await runner.submit("x", {})          # 第 3 个:超额
        gate = await runner.submit("x", {}, source="gate")   # 豁免
        retro = await runner.submit("x", {}, source="retro")  # 豁免
        assert await _wait_status(store, [ok1, ok2, gate, retro], "completed")
        return store, over

    store, over = asyncio.run(main())
    meta = store.get_meta(over)
    assert meta.status == "failed"
    assert "配额" in (meta.error or "")


def test_dequeue_cas_skips_cancelled(tmp_path: Path):
    """排队中被取消的任务出队时被 CAS 丢弃,不执行。"""
    ran = {"n": 0}

    def counting(on_progress=None, **kw):
        ran["n"] += 1
        return {"ok": True}

    async def main():
        store = TaskStore(tmp_path / "tasks")
        runner = tr.TaskRunner(store, {"x": counting})
        # 先提交(无 worker,堆在队列里)——lpush 进 Redis，再取消，再拉起 worker
        # recover DEL 旧 List，disk-scan 时 status=cancelled 不进 backlog，不重投
        tid = await runner.submit("x", {})
        store.update_status(tid, "cancelled")
        await runner.recover_and_start()
        await asyncio.sleep(0.5)
        return store, tid

    store, tid = asyncio.run(main())
    assert store.get_meta(tid).status == "cancelled"
    assert ran["n"] == 0


def test_recover_backlog_and_orphans(tmp_path: Path):
    """启动恢复:pending 重新入队执行、孤儿 running 复位执行、种子任务不动、超龄判失败。"""
    store = TaskStore(tmp_path / "tasks")
    fresh_pending = store.create("x", {})
    orphan_running = store.create("x", {})
    store.update_status(orphan_running.task_id, "running")
    stale = store.create("x", {})
    m = store.get_meta(stale.task_id)
    m.created_at = (datetime.now() - timedelta(hours=100)).isoformat()
    store._write_meta(m)
    # 种子任务:手工放一个 t_demo_* 的 pending
    seed = TaskMeta(task_id="t_demo_zombie", cmd="x", params={},
                    status="pending", created_at=datetime.now().isoformat())
    store.task_dir(seed.task_id).mkdir(parents=True)
    store.events_path(seed.task_id).touch()
    store._write_meta(seed)

    async def main():
        runner = tr.TaskRunner(store, {"x": _ok})
        await runner.recover_and_start()
        assert await _wait_status(store, [fresh_pending.task_id, orphan_running.task_id], "completed")

    asyncio.run(main())
    assert store.get_meta(seed.task_id).status == "pending"      # 种子不参与恢复
    stale_meta = store.get_meta(stale.task_id)
    assert stale_meta.status == "failed" and "恢复" in (stale_meta.error or "")


def test_cron_task_auto_archives(tmp_path: Path):
    """source=cron 完成即自动归档:reviewer=system 的 approved review + 案卷;user 任务不动。"""
    store = TaskStore(tmp_path / "tasks")
    labels = LabelStore(tmp_path / "labels")

    async def main():
        runner = tr.TaskRunner(store, {"x": _ok}, labels=labels)
        await runner.recover_and_start()
        cron_id = await runner.submit("x", {"author": "a"}, source="cron")
        user_id = await runner.submit("x", {})
        assert await _wait_status(store, [cron_id, user_id], "completed")
        return cron_id, user_id

    cron_id, user_id = asyncio.run(main())
    review = store.get_review(cron_id)
    assert review is not None and review.decision == "approved" and review.reviewer == "system"
    label = labels.get(cron_id)
    assert label is not None and label["reviewer"] == "system"
    assert store.get_review(user_id) is None  # 用户任务仍待人工验收


# ---------------------------------------------------------------------------
# HTTP 层:递归闸与派生链深度
# ---------------------------------------------------------------------------

@pytest.fixture()
def client_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.setenv("NOF_ARTIFACTS_ROOT", str(tmp_path))
    monkeypatch.setenv("NOF_MOCK_AGENTS", "all")

    from ncds_opus_factory.server import artifacts as art_mod
    importlib.reload(art_mod)
    from ncds_opus_factory.server import state as state_mod
    importlib.reload(state_mod)
    from ncds_opus_factory.server.routes import tasks as tasks_mod
    importlib.reload(tasks_mod)
    from ncds_opus_factory.server.routes import subscriptions as subs_routes_mod
    importlib.reload(subs_routes_mod)
    from ncds_opus_factory.server import app as app_mod
    importlib.reload(app_mod)

    return TestClient(app_mod.app), state_mod


def test_wolong_cannot_spawn_wolong(client_env):
    client, _ = client_env
    resp = client.post("/tasks", json={"cmd": "wolong", "params": {}, "source": "wolong"})
    assert resp.status_code == 400
    assert "递归" in resp.json()["detail"]


def test_gate_requires_round_and_single_inflight(client_env):
    """续跑段必须带 round_id;同 round 同时只允许一个在途续跑段。"""
    client, _ = client_env
    no_round = client.post("/tasks", json={"cmd": "wolong", "params": {}, "source": "gate"})
    assert no_round.status_code == 400

    ok = client.post(
        "/tasks", json={"cmd": "wolong", "params": {}, "source": "gate", "round_id": "r1"}
    )
    assert ok.status_code == 201
    # 上一个续跑段还 pending(测试无 worker),同 round 再来一个 -> 409
    dup = client.post(
        "/tasks", json={"cmd": "wolong", "params": {}, "source": "gate", "round_id": "r1"}
    )
    assert dup.status_code == 409


def test_derivation_depth_capped(client_env):
    client, state = client_env
    t1 = state.STORE.create("guiguzi", {})
    t2 = state.STORE.create("liuyong", {}, parent_task_id=t1.task_id)

    ok = client.post("/tasks", json={"cmd": "liuyong", "params": {}, "parent_task_id": t1.task_id})
    assert ok.status_code == 201
    over = client.post("/tasks", json={"cmd": "liuyong", "params": {}, "parent_task_id": t2.task_id})
    assert over.status_code == 400
    assert "深度" in over.json()["detail"]
    # 悬空 parent -> 400,不准创建溯源断裂的任务
    dangling = client.post(
        "/tasks", json={"cmd": "liuyong", "params": {}, "parent_task_id": "t_404_nope"}
    )
    assert dangling.status_code == 400


def test_depth_skips_wolong_segments(client_env):
    """深度按业务层计:卧龙段(cmd=wolong)是编排层不算一层,干活任务才计层。"""
    client, state = client_env
    w = state.STORE.create("wolong", {}, source="gate", round_id="r9")
    l1 = state.STORE.create("liuyong", {}, parent_task_id=w.task_id)

    # parent=卧龙段:深度 0 -> 允许
    ok = client.post("/tasks", json={"cmd": "liuyong", "params": {}, "parent_task_id": w.task_id})
    assert ok.status_code == 201
    # parent=干活任务(其上是卧龙段):深度 1 -> 允许
    ok2 = client.post("/tasks", json={"cmd": "boya", "params": {}, "parent_task_id": l1.task_id})
    assert ok2.status_code == 201
    # 再往下一层:深度 2 -> 拒
    l2 = state.STORE.create("boya", {}, parent_task_id=l1.task_id)
    over = client.post("/tasks", json={"cmd": "boya", "params": {}, "parent_task_id": l2.task_id})
    assert over.status_code == 400


def test_cron_failed_also_auto_archived(tmp_path: Path):
    """cron 任务失败同样自动归档:失败行不准在收件箱点红灯。"""
    store = TaskStore(tmp_path / "tasks")
    labels = LabelStore(tmp_path / "labels")

    def boom(on_progress=None, **kw):
        raise RuntimeError("tikhub down")

    async def main():
        runner = tr.TaskRunner(store, {"x": boom}, labels=labels)
        await runner.recover_and_start()
        tid = await runner.submit("x", {"author": "a"}, source="cron")
        assert await _wait_status(store, [tid], "failed")
        return tid

    tid = asyncio.run(main())
    review = store.get_review(tid)
    assert review is not None and review.reviewer == "system"


def test_inflight_tracking(tmp_path: Path):
    """执行中任务在 in-flight 集合里(restore 据此拒绝双跑);结束后移除。"""
    started = threading.Event()

    def slow(on_progress=None, **kw):
        started.set()
        time.sleep(0.4)
        return {"ok": True}

    async def main():
        store = TaskStore(tmp_path / "tasks")
        runner = tr.TaskRunner(store, {"x": slow})
        await runner.recover_and_start()
        tid = await runner.submit("x", {})
        for _ in range(50):
            if started.is_set():
                break
            await asyncio.sleep(0.02)
        # S3 步7：is_inflight 改 async（Redis SISMEMBER）
        assert await runner.is_inflight(tid)
        # 执行中取消:任务保持 cancelled、结果作废,且退出后 in-flight 清空
        store.update_status(tid, "cancelled")
        for _ in range(100):
            if not await runner.is_inflight(tid):
                break
            await asyncio.sleep(0.02)
        assert not await runner.is_inflight(tid)
        return store, tid

    store, tid = asyncio.run(main())
    assert store.get_meta(tid).status == "cancelled"
    assert store.get_result(tid) is None


def test_shutdown_requests_cooperative_stop_without_user_cancel(tmp_path: Path):
    """worker 停机时通过 cancel.current() 请求当前任务自停，任务保持 running 等 recover。"""
    started = threading.Event()
    stopped = threading.Event()

    def cooperative(on_progress=None, **kw):
        check = cancel.current()
        started.set()
        while not check():
            time.sleep(0.01)
        stopped.set()
        raise cancel.TaskCancelled("worker shutdown")

    async def main():
        store = TaskStore(tmp_path / "tasks")
        runner = tr.TaskRunner(store, {"x": cooperative})
        await runner.recover_and_start()
        tid = await runner.submit("x", {})
        for _ in range(100):
            if started.is_set() and await runner.is_inflight(tid):
                break
            await asyncio.sleep(0.02)

        await runner.shutdown(grace_seconds=2.0)
        return store, runner, tid

    store, runner, tid = asyncio.run(main())
    assert stopped.is_set(), "shutdown 应触发命令协作式取消检查"
    assert store.get_meta(tid).status == "running"
    assert store.get_result(tid) is None
    assert runner._workers == []


# ---------------------------------------------------------------------------
# S3 步3 防双投测试（fakeredis，不连真 Redis）
# ---------------------------------------------------------------------------

def test_recover_clears_redis_stale_before_disk_scan(tmp_path: Path):
    """recover DEL 先于 disk-scan 重投：Redis List 残留 + 磁盘 pending，每个 task_id 只执行一次。

    场景：
    - 磁盘有一个 pending task_id（模拟上次 lpush 后进程崩溃，task 残留 pending）。
    - Redis List 里也有同一 task_id（模拟旧进程已 lpush 但没 BRPOP）。
    - recover 必须先 DEL List，再 disk-scan 全量重投一次。
    - 断言：run_fn 只被调用一次（不是两次）——CAS 兜底第二次 _run 也能防重。
    """
    # 用简单计数器（不依赖 _dispatch_task_id 注入，那是 wolong-only 逻辑）
    run_count = {"n": 0}

    def counting(on_progress=None, **kw):
        run_count["n"] += 1
        return {"ok": True}

    async def main():
        store = TaskStore(tmp_path / "tasks")
        runner = tr.TaskRunner(store, {"x": counting})

        # 造一个真实 task_id（符合 _REAL_TASK_ID_RE）的 pending 任务
        meta = store.create("x", {})
        tid = meta.task_id

        # 模拟 Redis List 里预先有该 task_id（旧进程已 lpush，但进程崩了没 brpop）
        await runner._rq.lpush("x", tid)

        # recover 先 DEL List（清掉上面那条残留），再 disk-scan（disk 还是 pending，重投一次）
        await runner.recover_and_start()
        # cancel 所有 worker（防 brpop 等 5s 拖慢测试，测试直接驱动 _run）
        for w in runner._workers:
            w.cancel()

        # 直接断言 DEL 生效：Redis 残留 1 条 + 磁盘 pending 1 条，DEL 清残留后只重投 1 次
        # → List 恰好 1 条(不是 2)。workers 刚 cancel、事件循环未 yield 给它们故未消费，
        # 此处 llen 非竞态。这条直接守护「DEL 先于重投」决策——少了它仅靠 CAS 也能 exactly-once，
        # 但 List 会残留双份(膨胀 + 多一次被 CAS 丢弃的空跑)。
        assert await runner._rq._client.llen("nof:q:x") == 1, "DEL 未清残留：List 应恰好 1 条"

        # 直接调 _run 模拟出队执行
        # _run 是幂等 CAS（只跑 pending）：第一次执行后 status=completed，
        # 第二次 CAS 检查到非 pending，静默跳过——不会双跑
        await runner._run(tid, "x")
        await runner._run(tid, "x")  # 第二次 CAS skip

        return store, tid

    store, tid = asyncio.run(main())
    assert store.get_meta(tid).status == "completed", "task 应已 completed"
    # 关键断言：只执行一次（DEL 清掉残留 + CAS 兜底，两层防双投）
    assert run_count["n"] == 1, (
        f"每个 task_id 应恰好执行一次，实际执行 {run_count['n']} 次"
    )


def test_recover_orphan_running_requeued_once(tmp_path: Path):
    """孤儿 running 在 recover 时复位 pending 并重投，最终恰好执行一次。

    场景：
    - 磁盘有一个 running 任务（上次进程死在执行中，成为孤儿）。
    - recover 应把它复位为 pending，然后 LPUSH 一次。
    - 断言：run_fn 只被调用一次（不是零次也不是两次）。
    """
    run_count: dict[str, int] = {}

    def counting(on_progress=None, **kw):
        run_count["hit"] = run_count.get("hit", 0) + 1
        return {"ok": True}

    async def main():
        store = TaskStore(tmp_path / "tasks")

        # 造一个孤儿 running 任务
        orphan = store.create("x", {})
        tid = orphan.task_id
        store.update_status(tid, "running")

        runner = tr.TaskRunner(store, {"x": counting})

        # recover 先 DEL 旧 List（空的），再 disk-scan 把 running 复位为 pending，lpush 一次
        await runner.recover_and_start()
        # cancel 所有 worker（防 brpop 等 5s 拖慢测试）
        for w in runner._workers:
            w.cancel()

        # 此时 task 应已被 reset_for_requeue → pending，Redis List 有一条
        meta_after = store.get_meta(tid)
        assert meta_after is not None and meta_after.status == "pending", (
            f"孤儿 running 应已复位为 pending，实际 {meta_after.status}"
        )

        # 直接调 _run 模拟出队执行
        await runner._run(tid, "x")

        return store, tid

    store, tid = asyncio.run(main())
    assert store.get_meta(tid).status == "completed"
    assert run_count.get("hit", 0) == 1, (
        f"孤儿恢复后应恰好执行一次，实际 {run_count.get('hit', 0)} 次"
    )
