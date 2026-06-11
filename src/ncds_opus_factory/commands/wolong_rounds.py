"""卧龙 round 编排逻辑（docs/WOLONG-DESIGN.md §4）：派单段 + 续跑段。

设计取舍：round 的机械部分（状态机推进、派发、事件消费、返工计数、战报）是
**确定性 Python**——P3 的判断点（按 potential 选 top、把打回意见打包成返工要求）
都是简单规则,确定性实现可测可控;卧龙的 LLM 判断力(rubric/预筛/复盘)P4 注入。
原 opus headless 一把梭保留为 mode=legacy(commands/wolong.py)。

段的执行模型（防死锁的三段式）：
  A. 锁内:消费事件、计算行动、登记派发意向(intent) —— 纯文件操作;
  B. 锁外:执行派发(loopback HTTP,带 intent_key 幂等键,调度器查重) ——
     锁内做 HTTP 会与 event loop 上等锁的事件追加方互相等死;
  C. 锁内:回填 task_id、终局检查(全产线落定 -> 战报 + done)。
崩溃恢复:B 段是幂等的(intent_key 查重),任何一步断掉,对账协程拉起的下一段
会把 task_id 为 null 的 intent 重新派发,不重复不丢失。

流程(P3 范围,§4.6):选题(鬼谷子,无闸自动) → 成稿(柳永,闸1 人工验收) →
全部产线落定 → 战报。吴道子/伯牙/渲染段留待后续接入。
"""

from __future__ import annotations

import glob
import json
import logging
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from ncds_opus_factory.common.round_store import RoundStore

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]

MAX_REWORK = 2          # 同一产线最多返工次数,超过即止损(killed)
GUIGUZI_RETRY = 1       # 选题失败重试次数

ROOT = Path(__file__).resolve().parents[3]


def _noop(_: str) -> None:
    return None


class Transport(Protocol):
    """派发与读取的传输层——生产走 loopback HTTP + 文件读,测试注入假实现。"""

    def submit(self, cmd: str, params: dict[str, Any], source: str,
               round_id: str, intent_key: str) -> str: ...

    def read_result(self, task_id: str) -> dict[str, Any] | None: ...


class HttpTransport:
    """生产传输:派发走本机 HTTP（过路由闸门）,结果直读任务目录文件。"""

    def __init__(self) -> None:
        port = os.environ.get("NOF_SERVER_PORT", "8810")
        self.base = f"http://127.0.0.1:{port}"
        sd = os.environ.get("NOF_STATE_DIR")
        self.tasks_dir = Path(sd) if sd else ROOT / "state" / "tasks"

    def ping(self) -> None:
        """server 可达性检查。round 模式的派发走 loopback HTTP,server 没起时
        要在建 round 之前清晰报错——否则磁盘上留下孤儿 active round,
        server 下次启动会被对账协程悄悄复活开跑一整轮。"""
        import requests

        try:
            requests.get(f"{self.base}/health", timeout=3).raise_for_status()
        except Exception as e:
            raise RuntimeError(
                f"nof server({self.base})不可达——round 模式需要服务器在跑;"
                f"先启动 nof-server,或用 --legacy 走本地 opus 一把梭。原因: {type(e).__name__}"
            ) from e

    def submit(self, cmd: str, params: dict[str, Any], source: str,
               round_id: str, intent_key: str) -> str:
        import requests

        resp = requests.post(
            f"{self.base}/tasks",
            json={"cmd": cmd, "params": params, "source": source,
                  "round_id": round_id, "intent_key": intent_key},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["task_id"]

    def read_result(self, task_id: str) -> dict[str, Any] | None:
        path = self.tasks_dir / task_id / "result.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None


def discover_benchmark() -> str | None:
    """自动发现最新的对标数据(author_*/all_posts.json,按 mtime)。"""
    candidates = glob.glob(str(ROOT / "state" / "benchmark" / "author_*" / "all_posts.json"))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


# ---------------------------------------------------------------------------
# 派单段
# ---------------------------------------------------------------------------
def _recent_round_topics(rounds: RoundStore, limit_files: int = 50) -> list[str]:
    """近期 round 已开产线的选题标题——跨轮防撞题的廉价兜底(完整选题库改造在 P5)。"""
    titles: list[str] = []
    try:
        files = sorted(rounds.base_dir.glob("round_*.json"), reverse=True)[:limit_files]
        for f in files:
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for line in r.get("lines", []):
                t = str((line.get("topic") or {}).get("title") or "").strip()
                if t:
                    titles.append(t)
    except Exception:  # noqa: BLE001
        logger.warning("[rounds] 历史选题扫描失败", exc_info=True)
    return titles


def start_round(
    rounds: RoundStore,
    transport: Transport,
    count: int,
    benchmark_path: str,
    avoid: str,
    dispatch_task_id: str | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """开盘:建 round、派鬼谷子选题,然后退出(后续由事件驱动续跑段推进)。

    round_id 由派单任务 id 确定化(round_<task_id>):服务重启把崩溃中的派单段
    重新入队再跑一遍时,撞上已存在的 round 文件 -> 转入续跑补派遗留意向,
    绝不建第二个 round(一次手发只能有一轮生产)。CLI 直跑无任务 id 时退回随机。
    """
    benchmark = benchmark_path or discover_benchmark()
    if not benchmark or not Path(benchmark).is_file():
        raise FileNotFoundError(
            "没有可用的对标数据(all_posts.json):先跑一次沈括采集(author 模式),或显式指定 benchmark_path"
        )
    if dispatch_task_id:
        round_id = f"round_{dispatch_task_id}"
        if rounds.load(round_id) is not None:
            on_progress(f"round 已存在(派单段重跑),转续跑补派: {round_id}")
            return resume_round(rounds, transport, round_id, on_progress)
    else:
        round_id = f"round_{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(2)}"
    goal = {"count": max(1, int(count)), "benchmark_path": str(benchmark), "avoid": avoid or ""}
    rounds.create(round_id, goal)
    on_progress(f"卧龙开盘: {round_id} 目标 {goal['count']} 条 (对标: {Path(benchmark).parent.name})")

    # 防撞题 = 用户手填 avoid + 近期 round 已开产线的选题
    avoid_list = [s.strip() for s in (avoid or "").split(",") if s.strip()]
    avoid_list += [t for t in _recent_round_topics(rounds) if t not in avoid_list]

    key = "guiguzi:0"
    params = {"benchmark_path": str(benchmark), "avoid": avoid_list}
    with rounds.mutate(round_id) as r:
        r["intents"][key] = {"cmd": "guiguzi", "params": params, "task_id": None,
                             "at": datetime.now().isoformat()}
    try:
        tid = transport.submit("guiguzi", params, source="wolong", round_id=round_id, intent_key=key)
    except Exception:
        # 派发失败别留孤儿 active round(否则服务端对账协程会悄悄把它复活开跑)
        with rounds.mutate(round_id) as r:
            _terminate(r, reason="开盘派发失败(server 不可达或拒绝)")
        raise
    with rounds.mutate(round_id) as r:
        r["intents"][key]["task_id"] = tid
    on_progress(f"选题已派发: 鬼谷子 {tid}")
    return {
        "round_id": round_id,
        "stage": "topics",
        "guiguzi_task": tid,
        "task_title": f"卧龙开盘 · 目标 {goal['count']} 条",
        "task_subtitle": round_id,
    }


# ---------------------------------------------------------------------------
# 续跑段
# ---------------------------------------------------------------------------
def resume_round(
    rounds: RoundStore,
    transport: Transport,
    round_id: str,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """消费积压事件,推进 round。一次续跑消费全部可归属事件(§4.4 合并)。

    A→B→C 三段式套循环:无法归属的事件(意向还没回填 task_id,见 _handle_event
    的暂缓语义)本轮不消费,回填后下一圈重试;一圈没有任何进展即退出,
    剩余事件由对账协程兜底。
    """
    report = None
    consumed_total = 0
    for _ in range(8):  # 圈数上限只是防御,正常 1-2 圈收敛
        # ---- A. 锁内:消费可归属事件,计算行动,登记意向 ----
        dispatch_plan: list[tuple[str, str, dict[str, Any]]] = []
        deferred = 0
        with rounds.mutate(round_id) as r:
            if r["status"] != "active":
                on_progress(f"round {round_id} 已 {r['status']},无事可做")
                return {"round_id": round_id, "noop": True, "status": r["status"]}
            pending = [e for e in r["events"] if not e.get("consumed")]
            for ev in pending:
                if _handle_event(r, ev, transport, dispatch_plan, on_progress):
                    ev["consumed"] = True
                    ev["consumed_at"] = datetime.now().isoformat()
                    consumed_total += 1
                else:
                    deferred += 1  # 暂缓:回填后重试
            # 把计划落进 intents(task_id=None),崩溃后下一段能看见未完成的派发
            for key, cmd, params in dispatch_plan:
                if key not in r["intents"]:
                    r["intents"][key] = {"cmd": cmd, "params": params, "task_id": None,
                                         "at": datetime.now().isoformat()}
            # 上一段崩在派发前的遗留意向也一并补派
            for key, intent in r["intents"].items():
                if intent.get("task_id") is None and all(k != key for k, _, _ in dispatch_plan):
                    dispatch_plan.append((key, intent["cmd"], intent["params"]))

        # ---- B. 锁外:幂等派发(intent_key 查重由调度器兜底;锁内禁 HTTP,防死锁) ----
        dispatched: dict[str, str] = {}
        for key, cmd, params in dispatch_plan:
            try:
                tid = transport.submit(cmd, params, source="wolong",
                                       round_id=round_id, intent_key=key)
                dispatched[key] = tid
                on_progress(f"派发 {key} -> {tid}")
            except Exception as e:  # noqa: BLE001 — 单个派发失败留给对账协程重试
                logger.exception("[rounds] 派发失败 %s/%s", round_id, key)
                on_progress(f"派发失败(对账协程会重试) {key}: {type(e).__name__}: {e}")

        # ---- C. 锁内:回填 task_id,终局检查 ----
        with rounds.mutate(round_id) as r:
            for key, tid in dispatched.items():
                if key in r["intents"]:
                    r["intents"][key]["task_id"] = tid
                for line in r["lines"]:
                    if line.get("pending_intent") == key:
                        line["task_id"] = tid
                        line["status"] = "drafting"
                        line.pop("pending_intent", None)
            report = _finalize_if_done(r, on_progress)

        progressed = bool(dispatch_plan) or bool(dispatched)
        if report is not None or (deferred == 0) or not progressed:
            break

    result: dict[str, Any] = {
        "round_id": round_id,
        "consumed": consumed_total,
        "status": r["status"],
        "task_title": f"卧龙续跑 · {round_id}",
    }
    if report is not None:
        result["report"] = report
        result["count"] = report["approved"]
        result["tail"] = report["summary_lines"]
        result["task_title"] = f"卧龙收盘 · 产出 {report['approved']} 条"
    return result


def _handle_event(
    r: dict[str, Any],
    ev: dict[str, Any],
    transport: Transport,
    plan: list[tuple[str, str, dict[str, Any]]],
    on_progress: ProgressFn,
) -> bool:
    """处理一条事件。返回 False = 暂缓(本轮不消费):任务属于本 round 但
    意向尚未回填 task_id——上一段崩在派发后回填前,事件先于回填到达。
    消费掉它会让产线永卡(intent_key 查重不会再发第二次 terminal)。"""
    task_id = ev["task_id"]
    intent_key = _intent_key_of(r, task_id)
    line = _line_of(r, task_id)

    if ev["kind"] == "terminal" and intent_key and intent_key.startswith("guiguzi"):
        if ev["status"] == "completed":
            _plan_scripts(r, transport.read_result(task_id), plan, on_progress)
        else:  # failed / cancelled
            retry_no = int(intent_key.split(":")[1]) + 1
            if retry_no <= GUIGUZI_RETRY:
                on_progress(f"选题失败,重试第 {retry_no} 次")
                plan.append((f"guiguzi:{retry_no}", "guiguzi", r["intents"][intent_key]["params"]))
            else:
                on_progress("选题重试仍失败,本轮止损")
                _terminate(r, reason="选题(鬼谷子)失败且重试耗尽")
        return True

    if line is None:
        if intent_key is None and _has_unfilled_intent(r):
            return False  # 暂缓:很可能是回填窗口里的事件,补派回填后下一圈重试
        return True  # 已被返工替换的旧任务等,消费丢弃

    if ev["kind"] == "terminal":
        if ev["status"] == "completed":
            # 状态守卫:decision 先于 terminal 被消费时(API 允许对 running 任务写
            # review),不能把 approved/killed 回退成 review
            if line["status"] in ("dispatching", "drafting"):
                line["status"] = "review"   # 进闸1,等 Leader 验收
                on_progress(f"产线 {line['slot']} 成稿完成,待验收")
        else:  # failed / cancelled = 机器弃单(§4.2)
            _rework_or_kill(r, line, note=f"上一稿{ev['status']}", plan=plan, on_progress=on_progress)
        return True

    # decision(闸1 人工验收)
    if ev["decision"] == "approved":
        line["status"] = "approved"
        on_progress(f"产线 {line['slot']} 过审")
    else:
        _rework_or_kill(r, line, note=ev.get("note") or "", plan=plan, on_progress=on_progress)
    return True


def _has_unfilled_intent(r: dict[str, Any]) -> bool:
    return any(i.get("task_id") is None for i in r["intents"].values())


def _plan_scripts(
    r: dict[str, Any],
    guiguzi_result: dict[str, Any] | None,
    plan: list[tuple[str, str, dict[str, Any]]],
    on_progress: ProgressFn,
) -> None:
    """选题完成:挑 top N 开产线,派柳永成稿。"""
    raw = (guiguzi_result or {}).get("topics") or []
    # 先过滤再判空:真实鬼谷子可能产出字符串数组/空 title 等畸形,不能让
    # stage=scripts + 空 lines 落盘(那是个 48h 僵尸 round)
    topics = sorted(
        (t for t in raw if isinstance(t, dict) and str(t.get("title") or "").strip()),
        key=lambda t: t.get("potential", 0),
        reverse=True,
    )
    if not topics:
        on_progress("选题结果无有效条目,本轮止损")
        _terminate(r, reason="鬼谷子产出无有效选题")
        return
    n = min(int(r["goal"]["count"]), len(topics))
    r["stage"] = "scripts"
    for slot in range(n):
        topic = topics[slot]
        key = f"liuyong:{slot}:0"
        params = {"topic": str(topic.get("title")), "user_requirements": _topic_context(topic)}
        r["lines"].append({
            "slot": slot, "topic": topic, "task_id": None,
            "status": "dispatching", "rework": 0, "history": [], "notes": [],
            "pending_intent": key,
        })
        plan.append((key, "liuyong", params))
    on_progress(f"开 {n} 条产线(共 {len(topics)} 个候选选题)")
    _mark_topics_consumed(r["round_id"], [t.get("title") for t in topics[:n]])


def _topic_context(topic: dict[str, Any]) -> str:
    """把鬼谷子提炼的母题/角度带给柳永(它吃 user_requirements 自由文本,零接口改动)。"""
    parts = []
    for key, label in (("angle", "角度"), ("motif", "母题"), ("why", "爆点")):
        v = str(topic.get(key) or "").strip()
        if v:
            parts.append(f"【{label}】{v}")
    return "\n".join(parts)


def _rework_or_kill(
    r: dict[str, Any],
    line: dict[str, Any],
    note: str,
    plan: list[tuple[str, str, dict[str, Any]]],
    on_progress: ProgressFn,
) -> None:
    if line["status"] in ("approved", "killed"):
        return  # 状态守卫:乱序的弃单事件不能推翻已落定的产线
    if line["rework"] >= MAX_REWORK:
        line["status"] = "killed"
        on_progress(f"产线 {line['slot']} 返工 {MAX_REWORK} 次仍不过,止损")
        return
    line["rework"] += 1
    if line.get("task_id"):
        line["history"].append(line["task_id"])
    if note:
        line.setdefault("notes", []).append(note)
    key = f"liuyong:{line['slot']}:{line['rework']}"
    # 全量带历史打回意见:只带最后一条的话,柳永会把第一次被骂的毛病原样写回来
    notes = line.get("notes") or []
    reqs_parts = [_topic_context(line["topic"])] if _topic_context(line["topic"]) else []
    reqs_parts += [f"【第{i + 1}次打回意见】{n}" for i, n in enumerate(notes)]
    if not notes:
        reqs_parts.append("【打回】上一稿未过审,请换角度重写")
    params = {"topic": str(line["topic"].get("title") or ""),
              "user_requirements": "\n".join(reqs_parts)}
    line["task_id"] = None
    line["status"] = "dispatching"
    line["pending_intent"] = key
    plan.append((key, "liuyong", params))
    on_progress(f"产线 {line['slot']} 返工第 {line['rework']} 次")


def _finalize_if_done(r: dict[str, Any], on_progress: ProgressFn) -> dict[str, Any] | None:
    """全部产线落定 -> 战报 + done。"""
    if r["status"] != "active" or r["stage"] != "scripts" or not r["lines"]:
        return None
    if any(line["status"] not in ("approved", "killed") for line in r["lines"]):
        return None
    approved = [ln for ln in r["lines"] if ln["status"] == "approved"]
    killed = [ln for ln in r["lines"] if ln["status"] == "killed"]
    summary = [
        f"round {r['round_id']}: 产出 {len(approved)} / 目标 {r['goal']['count']}",
        *(f"✓ 产线{ln['slot']} {str(ln['topic'].get('title'))[:40]} (返工{ln['rework']})" for ln in approved),
        *(f"✗ 产线{ln['slot']} {str(ln['topic'].get('title'))[:40]} 止损" for ln in killed),
    ]
    report = {
        "approved": len(approved),
        "killed": len(killed),
        "rework_total": sum(ln["rework"] for ln in r["lines"]),
        "approved_tasks": [ln["task_id"] for ln in approved],
        "summary_lines": summary,
        "finished_at": datetime.now().isoformat(),
    }
    r["report"] = report
    r["status"] = "done"
    r["stage"] = "done"
    on_progress("\n".join(summary))
    return report


def _terminate(r: dict[str, Any], reason: str) -> None:
    r["status"] = "terminated"
    r["report"] = {"approved": 0, "killed": len(r.get("lines", [])),
                   "reason": reason, "summary_lines": [f"round 终止: {reason}"],
                   "finished_at": datetime.now().isoformat()}


def _intent_key_of(r: dict[str, Any], task_id: str) -> str | None:
    for key, intent in r["intents"].items():
        if intent.get("task_id") == task_id:
            return key
    return None


def _line_of(r: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for line in r["lines"]:
        if line.get("task_id") == task_id:
            return line
    return None


def _mark_topics_consumed(round_id: str, titles: list[Any]) -> None:
    """best-effort 把被挑走的选题在共享选题库里标记 consumed(P5 做完整库改造)。"""
    path = ROOT / "state" / "benchmark" / "topics" / "topics.json"
    try:
        if not path.exists():
            return
        topics = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(topics, list):
            return
        wanted = {str(t) for t in titles if t}
        changed = False
        for t in topics:
            if isinstance(t, dict) and str(t.get("title")) in wanted:
                t["consumed_by"] = round_id
                changed = True
        if changed:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
    except Exception:  # noqa: BLE001 — 选题库标记失败不影响 round 推进
        logger.warning("[rounds] 选题 consumed 标记失败", exc_info=True)
