"""round 事件接线与对账（docs/WOLONG-DESIGN.md §4.2/§4.4/§4.5）。

双事件源都汇到这里:
- 人的反馈:review 路由写完决策后调 handle_decision(只认 reviewer=user 的闸门信号);
- 机器的反馈:TaskRunner 终态钩子调 handle_terminal(completed/failed),
  cancel 路由调 handle_cancelled。

事件追加成功 -> maybe_resume:无在途续跑段且 gate 配额有余量时投递一个
(cmd=wolong, source=gate)。同 round 单在途段 + 事件去重 = §4.4 的续跑合并。
卧龙段自身的终态不算 round 事件,但要补触发 maybe_resume——续跑段执行期间
攒下的新事件不能干等对账协程(5 分钟),段一退出立刻接力。

对账协程 reconcile_once(§4.5),三件事:
1. store↔round 终态对账:进程死在「任务终态落盘」与「round 事件落盘」之间的
   窗口,terminal 会永久丢失——拿 store 里的终态反向补 round 事件(去重幂等);
2. 补投丢失的续跑(配额耗尽/重启/POST 失败后 round 有积压但无在途段);
3. 超时 round 强制收尾 + 清场。

清场(_cleanup_round_tasks):round 终局(done/terminated)后,在途干活任务取消、
completed 无 review 的孤儿补 system 归档——死 round 的任务不准永久红灯。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from ncds_opus_factory.common.round_store import RoundStore
from ncds_opus_factory.server.schemas import Review, TaskMeta
from ncds_opus_factory.server.state import LABELS, RUNNER, STATE_DIR, STORE

logger = logging.getLogger(__name__)

ROUNDS = RoundStore(STATE_DIR.parent / "wolong" / "rounds")

# active round 超过此时长无任何推进 -> 强制收尾(防永久挂死)
ROUND_TIMEOUT_HOURS = float(os.environ.get("NOF_ROUND_TIMEOUT_HOURS", "48"))


async def handle_terminal(meta: TaskMeta, status: str, result: dict | None) -> None:  # noqa: ARG001
    """机器反馈:round 内干活任务的终态。卧龙段(派单/续跑)是机械层,不算 round
    事件,但其退出要补触发——段执行期间攒下的事件立刻接力,不等对账协程。"""
    if not meta.round_id:
        return
    if meta.source == "gate" or meta.cmd == "wolong":
        await maybe_resume(meta.round_id)
        await _cleanup_if_finished(meta.round_id)
        return
    added = ROUNDS.append_event(meta.round_id, kind="terminal", task_id=meta.task_id, status=status)
    if added:
        await maybe_resume(meta.round_id)
    else:
        _archive_if_dead_round(meta.round_id, meta.task_id)


async def handle_cancelled(meta: TaskMeta | None) -> None:
    """用户取消:干活任务=机器弃单事件;卧龙段=终止整轮(§4.1)——取消开盘/续跑
    的语义就是"这轮别跑了",不能让流水线自己跑完。"""
    if meta is None or not meta.round_id:
        return
    if meta.cmd == "wolong":
        try:
            with ROUNDS.mutate(meta.round_id) as r:
                if r["status"] == "active":
                    r["status"] = "terminated"
                    r["report"] = {
                        "approved": sum(1 for ln in r.get("lines", []) if ln.get("status") == "approved"),
                        "killed": sum(1 for ln in r.get("lines", []) if ln.get("status") != "approved"),
                        "reason": "用户取消卧龙段,终止本轮",
                        "summary_lines": [f"round {meta.round_id} 被用户终止"],
                        "finished_at": datetime.now().isoformat(),
                    }
        except FileNotFoundError:
            return
        await _cleanup_round_tasks(meta.round_id)
        return
    if meta.source == "gate":
        return
    added = ROUNDS.append_event(
        meta.round_id, kind="terminal", task_id=meta.task_id, status="cancelled"
    )
    if added:
        await maybe_resume(meta.round_id)


async def handle_decision(task_id: str) -> None:
    """人的反馈:闸门决策。只认 reviewer=user——system 自动归档不是闸门信号。"""
    meta = STORE.get_meta(task_id)
    if meta is None or not meta.round_id or meta.source == "gate" or meta.cmd == "wolong":
        return
    review = STORE.get_review(task_id)
    if review is None or review.reviewer != "user":
        return
    added = ROUNDS.append_event(
        meta.round_id, kind="decision", task_id=task_id,
        decision=review.decision, note=review.note,
    )
    if added:
        await maybe_resume(meta.round_id)


def _has_inflight_gate(round_id: str) -> bool:
    for m in STORE.list_tasks():
        if m.round_id == round_id and m.source == "gate" and m.status in ("pending", "running"):
            return True
    return False


def _has_pending_work(r: dict) -> bool:
    """有未消费事件,或有登记了却没派出去的意向(上一段崩在派发前),
    或有停在预筛的产线(§8.3 崩溃安全:段在判定前被杀,凭此补投段救活)。"""
    if any(not e.get("consumed") for e in r.get("events", [])):
        return True
    if any(i.get("task_id") is None for i in r.get("intents", {}).values()):
        return True
    return any(ln.get("status") == "prescreen_pending" for ln in r.get("lines", []))


async def maybe_resume(round_id: str) -> bool:
    """有积压且无在途续跑段 -> 投递一个续跑段。返回是否真的投了。

    注意:从 _has_inflight_gate 检查到 RUNNER.submit 之间不得出现 await——
    单事件循环上的原子性是"同 round 不双投"的前提(RUNNER.submit 当前无 await)。
    """
    r = ROUNDS.load(round_id)
    if r is None or r.get("status") != "active" or not _has_pending_work(r):
        return False
    if _has_inflight_gate(round_id):
        return False  # 续跑合并(§4.4):在途段退出时由 handle_terminal 补触发
    if RUNNER.quota_remaining("wolong", source="gate") <= 0:
        # 配额耗尽:静默等翌日(对账协程会再试),不铸注定失败的垃圾任务
        logger.warning("[rounds] gate 配额耗尽,round %s 续跑暂缓", round_id)
        return False
    task_id = await RUNNER.submit(
        "wolong", {"round_id": round_id, "resume": True},
        source="gate", round_id=round_id,
    )
    logger.info("[rounds] 续跑段投递: %s -> %s", round_id, task_id)
    return True


# ---------------------------------------------------------------------------
# 清场与孤儿归档
# ---------------------------------------------------------------------------
def _archive_orphan(meta: TaskMeta) -> None:
    """死 round 的孤儿任务:completed 无 review 会永久红灯,补 system 归档。"""
    if STORE.get_review(meta.task_id) is not None:
        return
    review = Review(decision="approved", reviewed_at=datetime.now().isoformat(),
                    reviewer="system")
    try:
        STORE.write_review(meta.task_id, review)
        fresh = STORE.get_meta(meta.task_id)
        if fresh is not None:
            LABELS.write(fresh, review, STORE.get_result(meta.task_id))
    except Exception:  # noqa: BLE001
        logger.exception("[rounds] 孤儿归档失败: %s", meta.task_id)


def _archive_if_dead_round(round_id: str, task_id: str) -> None:
    """事件被拒收(round 已终局)时,把该终态任务就地归档,不留永久红灯。"""
    r = ROUNDS.load(round_id)
    if r is None or r.get("status") == "active":
        return
    meta = STORE.get_meta(task_id)
    if meta is not None and meta.status in ("completed", "failed"):
        _archive_orphan(meta)


async def _cleanup_if_finished(round_id: str) -> None:
    r = ROUNDS.load(round_id)
    if r is not None and r.get("status") != "active":
        await _cleanup_round_tasks(round_id)


async def _cleanup_round_tasks(round_id: str) -> None:
    """round 终局清场:在途干活任务取消(别继续烧 scodex),终态孤儿补归档。"""
    for m in STORE.list_tasks():
        if m.round_id != round_id or m.cmd == "wolong" or m.source == "gate":
            continue
        if m.status in ("pending", "running"):
            try:
                STORE.update_status(m.task_id, "cancelled")
                STORE.append_progress(m.task_id, f"round {round_id} 已终局,任务随之取消")
                logger.info("[rounds] 清场取消: %s", m.task_id)
            except Exception:  # noqa: BLE001
                logger.exception("[rounds] 清场取消失败: %s", m.task_id)
        elif m.status in ("completed", "failed"):
            _archive_if_needed = STORE.get_review(m.task_id) is None
            if _archive_if_needed:
                _archive_orphan(m)


# ---------------------------------------------------------------------------
# 对账
# ---------------------------------------------------------------------------
def _sync_terminals_from_store(r: dict) -> int:
    """store↔round 终态对账:任务已终态而 round 没有对应 terminal 事件的,补一条。

    治「update_status 落盘后、append_event 落盘前进程被杀」的窗口——
    鬼谷子这类无闸阶段没有人工决策可救,丢一条 terminal 就是 48h 僵尸。
    append_event 自带去重,重复补写无害。
    """
    synced = 0
    round_id = r["round_id"]
    seen = {(e["task_id"], e.get("status")) for e in r.get("events", []) if e["kind"] == "terminal"}
    for intent in r.get("intents", {}).values():
        tid = intent.get("task_id")
        if not tid:
            continue
        meta = STORE.get_meta(tid)
        if meta is None or meta.status not in ("completed", "failed", "cancelled"):
            continue
        if (tid, meta.status) in seen:
            continue
        if ROUNDS.append_event(round_id, kind="terminal", task_id=tid, status=meta.status):
            synced += 1
            logger.info("[rounds] 终态补账: %s %s(%s)", round_id, tid, meta.status)
    return synced


async def reconcile_once() -> int:
    """对账一轮:终态补账、补投丢失的续跑、超时 round 强制收尾+清场。返回处理数。"""
    handled = 0
    cutoff = datetime.now() - timedelta(hours=ROUND_TIMEOUT_HOURS)
    for r in ROUNDS.list_active():
        round_id = r["round_id"]
        try:
            updated = datetime.fromisoformat(r.get("updated_at") or r["created_at"])
        except (ValueError, KeyError):
            updated = datetime.now()
        if updated < cutoff:
            # 超时强制收尾:别让 Leader 面对一个永远"进行中"的轮次
            try:
                with ROUNDS.mutate(round_id) as rr:
                    rr["status"] = "terminated"
                    rr["report"] = {
                        "approved": sum(1 for ln in rr.get("lines", []) if ln.get("status") == "approved"),
                        "killed": sum(1 for ln in rr.get("lines", []) if ln.get("status") != "approved"),
                        "reason": f"超过 {ROUND_TIMEOUT_HOURS:.0f}h 无推进,对账协程强制收尾",
                        "summary_lines": [f"round {round_id} 超时收尾"],
                        "finished_at": datetime.now().isoformat(),
                    }
                await _cleanup_round_tasks(round_id)
                logger.warning("[rounds] 超时收尾: %s", round_id)
                handled += 1
            except Exception:  # noqa: BLE001
                logger.exception("[rounds] 超时收尾失败: %s", round_id)
            continue
        try:
            if _sync_terminals_from_store(r):
                handled += 1
                r = ROUNDS.load(round_id) or r
            if await maybe_resume(round_id):
                handled += 1
        except Exception:  # noqa: BLE001 — 单 round 失败不影响其余
            logger.exception("[rounds] 对账续跑失败: %s", round_id)
    return handled


async def terminate_round(round_id: str, reason: str = "用户手动终止") -> dict | None:
    """显式止损入口(POST /rounds/{id}/terminate)。返回收尾后的 round,不存在返回 None。"""
    r = ROUNDS.load(round_id)
    if r is None:
        return None
    if r.get("status") == "active":
        with ROUNDS.mutate(round_id) as rr:
            rr["status"] = "terminated"
            rr["report"] = {
                "approved": sum(1 for ln in rr.get("lines", []) if ln.get("status") == "approved"),
                "killed": sum(1 for ln in rr.get("lines", []) if ln.get("status") != "approved"),
                "reason": reason,
                "summary_lines": [f"round {round_id} 终止: {reason}"],
                "finished_at": datetime.now().isoformat(),
            }
        await _cleanup_round_tasks(round_id)
    return ROUNDS.load(round_id)
