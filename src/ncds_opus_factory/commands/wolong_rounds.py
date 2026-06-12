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

P5(§8.4):选题统一走 common/topic_store(选题库 v2)——开盘时库存够直接跳过
鬼谷子开产线;不足才派鬼谷子,其产出 merge 入库后续跑段仍从**库**里挑题消费。
锁序纪律:round 锁在外、topic 锁在内(topic_store 是纯文件 IO,锁内允许)。
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

from ncds_opus_factory.common import rubric_store, topic_store
from ncds_opus_factory.common.round_store import RoundStore
from ncds_opus_factory.commands import prescreen

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

    def review(self, task_id: str, decision: str, note: str,
               reviewer: str = "wolong") -> bool: ...


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

    def review(self, task_id: str, decision: str, note: str,
               reviewer: str = "wolong") -> bool:
        """预筛拦截的写入路径(§8.3):走 loopback 路由,案卷由路由顺带落,
        commands 层禁止直访 STORE。409 = 用户已亲自决策,预筛让位,返回 False。"""
        import requests

        resp = requests.post(
            f"{self.base}/tasks/{task_id}/review",
            json={"decision": decision, "note": note, "reviewer": reviewer},
            timeout=15,
        )
        if resp.status_code == 409:
            return False
        resp.raise_for_status()
        return True


def discover_benchmark() -> str | None:
    """自动发现最新的对标数据(author_*/all_posts.json,按 mtime)。"""
    candidates = glob.glob(str(ROOT / "state" / "benchmark" / "author_*" / "all_posts.json"))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


# ---------------------------------------------------------------------------
# 派单段
# ---------------------------------------------------------------------------
def start_round(
    rounds: RoundStore,
    transport: Transport,
    count: int,
    benchmark_path: str,
    avoid: str,
    dispatch_task_id: str | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """开盘(§8.4 消费方裁定 a):选题库 fresh 库存够 -> 跳过鬼谷子直开产线;
    不足 -> 建 round、派鬼谷子选题,后续由事件驱动续跑段推进。

    round_id 由派单任务 id 确定化(round_<task_id>):服务重启把崩溃中的派单段
    重新入队再跑一遍时,撞上已存在的 round 文件 -> 转入续跑补派遗留意向,
    绝不建第二个 round(一次手发只能有一轮生产)。CLI 直跑无任务 id 时退回随机。
    """
    goal_count = max(1, int(count))
    reopened = False
    if dispatch_task_id:
        round_id = f"round_{dispatch_task_id}"
        existing = rounds.load(round_id)
        if existing is not None:
            if existing.get("status") == "active" and not existing.get("lines") \
                    and not existing.get("intents"):
                # 空壳残留:开盘段在登记意向/建产线前崩溃。重走开盘——
                # take() 按 consumed_by 回收崩溃前的预占题,重放幂等不泄漏库存
                reopened = True
                on_progress(f"round 空壳残留(开盘段崩溃重放),重走开盘: {round_id}")
            else:
                on_progress(f"round 已存在(派单段重跑),转续跑补派: {round_id}")
                return resume_round(rounds, transport, round_id, on_progress)
    else:
        round_id = f"round_{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(2)}"

    # 库存够 -> 跳过鬼谷子:此路径不需要对标数据,goal.benchmark_path 可空串。
    # 可用 = fresh + 本 round 预占(崩溃重放时不误走鬼谷子路径)
    skip_guiguzi = topic_store.count_available(round_id) >= goal_count
    benchmark = benchmark_path or discover_benchmark()
    if not skip_guiguzi and (not benchmark or not Path(benchmark).is_file()):
        raise FileNotFoundError(
            "没有可用的对标数据(all_posts.json):先跑一次沈括采集(author 模式),或显式指定 benchmark_path"
        )
    bench_str = str(benchmark) if benchmark and Path(benchmark).is_file() else ""
    goal = {"count": goal_count, "benchmark_path": bench_str, "avoid": avoid or ""}
    # learned rubric 版本进 goal:战报按版本分组对比,复盘据此评估回退(§8.3)
    goal["rubric_version"] = rubric_store.current_version()
    if reopened:
        with rounds.mutate(round_id) as r:
            r["goal"] = goal  # 重放以本次口径为准(rubric_version 可能已变)
    else:
        rounds.create(round_id, goal)
    on_progress(f"卧龙开盘: {round_id} 目标 {goal['count']} 条"
                + (f" (对标: {Path(bench_str).parent.name})" if bench_str else " (库存直开)"))

    if skip_guiguzi:
        # 锁内挑题+consume+建产线登记意向(锁序:round 外/topic 内,纯文件 IO);
        # 实际派发交给 resume_round——A 段把 task_id=None 的意向收进 dispatch_plan,
        # B 段锁外派发,C 段回填,零新增派发代码。
        with rounds.mutate(round_id) as r:
            opened = _open_lines_from_store(r)
        if opened:
            on_progress(f"选题库存直开 {opened} 条产线(跳过鬼谷子)")
            res = resume_round(rounds, transport, round_id, on_progress)
            return {
                "round_id": round_id,
                "stage": "scripts",
                "skipped_guiguzi": True,
                "lines": opened,
                "status": res.get("status"),
                "task_title": f"卧龙开盘 · 目标 {goal['count']} 条(库存直开)",
                "task_subtitle": round_id,
            }
        # 竞态兜底:count_fresh 检查后库存被并发消费光 -> 退回鬼谷子路径
        if not bench_str:
            with rounds.mutate(round_id) as r:
                _terminate(r, reason="选题库存被并发取空且无对标数据")
            raise FileNotFoundError(
                "选题库存被并发取空,且没有可用对标数据(all_posts.json)可派鬼谷子"
            )
        on_progress("选题库存被并发取空,退回鬼谷子选题")

    # 防撞题 = 用户手填 avoid + 库内全部非 expired 选题(fresh/consumed;
    # expired 老题允许被重新提出,见 topic_store)
    avoid_list = [s.strip() for s in (avoid or "").split(",") if s.strip()]
    avoid_list += [t for t in topic_store.active_titles() if t not in avoid_list]

    key = "guiguzi:0"
    params = {"benchmark_path": bench_str, "avoid": avoid_list}
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

    预筛(P4,§8.3)嵌在套圈里,判定与落盘分两圈走(崩溃安全协议):
      圈 N   A 收集 prescreen_pending 产线 → B 锁外 LLM 判定 → C 把判定记进
             line.prescreen(状态不动);
      圈 N+1 A 把已有判定的产线收进行动队列 → B 锁外写拦截 review(loopback)
             → C 落盘转移(拦截→返工/止损,通过/探索→进闸1)。
    任何一步崩掉,line 停在 prescreen_pending + 判定已持久化,对账协程拉起的
    下一段直接续作,不重判(LLM 不确定性不会让同一稿的判定漂移)。
    """
    # learned rubric 段首读一次:缺失/degraded → 预筛整体放行(冷启动安全,§8.3)
    active = rubric_store.active_rubric()
    rubric_text = active[1] if active else None
    report = None
    consumed_total = 0
    for _ in range(8):  # 圈数上限只是防御,正常 1-3 圈收敛
        # ---- A. 锁内:消费可归属事件,计算行动,登记意向,收集预筛队列 ----
        dispatch_plan: list[tuple[str, str, dict[str, Any]]] = []
        judge_queue: list[dict[str, Any]] = []   # 待判定(无本稿判定记录)
        act_queue: list[dict[str, Any]] = []     # 判定已记录,待写 review/落盘转移
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
            # 预筛收集:含上一段崩在判定前留下的 prescreen_pending(可重入)
            for line in r["lines"]:
                if line.get("status") != "prescreen_pending":
                    continue
                if rubric_text is None:
                    line["status"] = "review"
                    on_progress(f"产线 {line['slot']} 预筛放行(无可用 rubric),待验收")
                    continue
                mark = line.get("prescreen") or {}
                item = {"slot": line["slot"], "task_id": line["task_id"],
                        "topic": str((line.get("topic") or {}).get("title") or "")}
                if mark.get("task_id") == line.get("task_id") and mark.get("prediction"):
                    act_queue.append({**item, "prediction": mark["prediction"],
                                      "explore": bool(mark.get("explore")),
                                      "reason": mark.get("reason") or ""})
                else:
                    judge_queue.append(item)  # 含返工换稿后的陈旧判定:按新稿重判
            # 把计划落进 intents(task_id=None),崩溃后下一段能看见未完成的派发
            for key, cmd, params in dispatch_plan:
                if key not in r["intents"]:
                    r["intents"][key] = {"cmd": cmd, "params": params, "task_id": None,
                                         "at": datetime.now().isoformat()}
            # 上一段崩在派发前的遗留意向也一并补派
            for key, intent in r["intents"].items():
                if intent.get("task_id") is None and all(k != key for k, _, _ in dispatch_plan):
                    dispatch_plan.append((key, intent["cmd"], intent["params"]))

        # ---- B. 锁外:幂等派发 + 预筛判定/拦截 review(锁内禁 HTTP/LLM,防死锁) ----
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
        fresh_verdicts: list[dict[str, Any]] = []
        for item in judge_queue:
            verdict = prescreen.judge(rubric_text, item["topic"],
                                      transport.read_result(item["task_id"]))
            explore = (verdict["prediction"] == "rejected"
                       and prescreen.is_explore(item["task_id"]))
            fresh_verdicts.append({**item, **verdict, "explore": explore})
            on_progress(f"预筛 产线{item['slot']}: 预测 {verdict['prediction']}"
                        + (" (探索:照常送验收)" if explore else ""))
        review_ok: dict[str, bool] = {}
        for item in act_queue:
            if item["prediction"] == "rejected" and not item["explore"]:
                try:
                    review_ok[item["task_id"]] = transport.review(
                        item["task_id"], "rejected",
                        item["reason"] or "预筛:预测不过审", reviewer="wolong")
                except Exception as e:  # noqa: BLE001 — 写不进就不动产线,下段重试
                    logger.exception("[rounds] 预筛 review 写入失败 %s", item["task_id"])
                    on_progress(f"预筛 review 写入失败(稍后重试) 产线{item['slot']}: {type(e).__name__}")
                    review_ok[item["task_id"]] = False

        # ---- C. 锁内:回填 task_id,预筛判定落盘与转移,终局检查 ----
        with rounds.mutate(round_id) as r:
            for key, tid in dispatched.items():
                if key in r["intents"]:
                    r["intents"][key]["task_id"] = tid
                for line in r["lines"]:
                    if line.get("pending_intent") == key:
                        line["task_id"] = tid
                        line["status"] = "drafting"
                        line.pop("pending_intent", None)
            for v in fresh_verdicts:
                line = _line_of(r, v["task_id"])
                if line is None or line.get("status") != "prescreen_pending":
                    continue
                line["prescreen"] = {"task_id": v["task_id"], "prediction": v["prediction"],
                                     "reason": v["reason"], "explore": v["explore"],
                                     "at": datetime.now().isoformat()}
            transitions = _apply_prescreen_actions(r, act_queue, review_ok, on_progress)
            # 是否还有本段内能推进的活(返工意向待派/预筛待落盘)
            more = any(i.get("task_id") is None for i in r["intents"].values()) or (
                rubric_text is not None
                and any(ln.get("status") == "prescreen_pending" for ln in r["lines"])
            )
            report = _finalize_if_done(r, on_progress)

        progressed = bool(dispatch_plan) or bool(dispatched) or bool(fresh_verdicts) or transitions
        if report is not None or not progressed:
            break
        if deferred == 0 and not more:
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
            _plan_scripts(r, transport.read_result(task_id), on_progress)
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
                # 闸1 前先过预筛(§8.3):事件照常消费,line 置 prescreen_pending,
                # 判定在锁外执行;段在判定前被杀,对账协程凭此状态救活
                line["status"] = "prescreen_pending"
                on_progress(f"产线 {line['slot']} 成稿完成,进预筛")
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
    on_progress: ProgressFn,
) -> None:
    """选题(鬼谷子)完成:从**库**里挑题开产线(P5,§8.4)。

    真实/mock 鬼谷子的 run 返回前都已 merge 入库,所以这里不读 task result 的
    topics 当选题来源(guiguzi_result 仅留作日志);库内无 fresh(产出全是重复/
    畸形被 merge 拒收)-> 止损,不能让 stage=scripts + 空 lines 落盘成 48h 僵尸。
    """
    produced = len((guiguzi_result or {}).get("topics") or [])
    opened = _open_lines_from_store(r)
    if opened == 0:
        on_progress("选题库无新鲜选题,本轮止损")
        _terminate(r, reason="选题库无新鲜选题")
        return
    on_progress(f"开 {opened} 条产线(鬼谷子本次产出 {produced} 条,选题取自库)")


def _open_lines_from_store(r: dict[str, Any]) -> int:
    """锁内:从选题库原子取题(topic_store.take:回收本 round 预占+fresh 补足)、
    建产线+登记意向。

    返回开的产线数(0=库内无可用题,由调用方决定止损/退回鬼谷子)。
    必须在 RoundStore.mutate 锁内调用(锁序:round 外/topic 内,纯文件 IO 合规);
    意向只登记 task_id=None,实际派发由续跑段 A->B->C 三段式完成。
    take 落盘是预占、round 落盘是确认:两次写之间崩溃,重放回收预占题,幂等。
    """
    picked = topic_store.take(int(r["goal"]["count"]), r["round_id"])
    if not picked:
        return 0
    r["stage"] = "scripts"
    for slot, topic in enumerate(picked):
        key = f"liuyong:{slot}:0"
        params = {"topic": str(topic.get("title")), "user_requirements": _topic_context(topic)}
        r["lines"].append({
            "slot": slot, "topic": topic, "task_id": None,
            "status": "dispatching", "rework": 0, "history": [], "notes": [],
            "pending_intent": key,
        })
        r["intents"][key] = {"cmd": "liuyong", "params": params, "task_id": None,
                             "at": datetime.now().isoformat()}
    return len(picked)


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


def _apply_prescreen_actions(
    r: dict[str, Any],
    act_queue: list[dict[str, Any]],
    review_ok: dict[str, bool],
    on_progress: ProgressFn,
) -> int:
    """C 圈:按已持久化的预筛判定推进产线。返回完成的转移数。

    拦截(预测必被拒且非探索) → 前提是 wolong review 已写成(挡出收件箱),
    然后段内直接驱动返工/止损(计入返工配额,不经 round 事件——它是段内决策);
    通过/探索 → 进闸1。判定记录在 line.prescreen,本函数幂等可重入。
    """
    # 用户在预筛窗口抢先验收(成稿 completed 后任务已在收件箱可见):
    # 有未消费 decision 的产线预筛让位,交事件消费路径处理
    decided = {e["task_id"] for e in r["events"]
               if e["kind"] == "decision" and not e.get("consumed")}
    plan: list[tuple[str, str, dict[str, Any]]] = []
    moved = 0
    for item in act_queue:
        line = _line_of(r, item["task_id"])
        if line is None or line.get("status") != "prescreen_pending":
            continue
        mark = line.get("prescreen") or {}
        if mark.get("task_id") != item["task_id"]:
            continue  # 返工换稿后的陈旧判定,等新稿重判
        if item["task_id"] in decided:
            continue
        if mark.get("prediction") == "rejected" and not mark.get("explore"):
            if not review_ok.get(item["task_id"]):
                continue  # review 没写成(失败待重试/用户已决 409):不动产线
            line["prescreen_intercepts"] = int(line.get("prescreen_intercepts") or 0) + 1
            _rework_or_kill(r, line, note=f"(预筛) {mark.get('reason') or '预测不过审'}",
                            plan=plan, on_progress=on_progress)
        else:
            line["status"] = "review"
            on_progress(f"产线 {line['slot']} 预筛"
                        f"{'探索放行' if mark.get('explore') else '通过'},待验收")
        moved += 1
    # 返工意向落 intents(task_id=None),下一圈 A 的遗留补派逻辑负责派出
    for key, cmd, params in plan:
        if key not in r["intents"]:
            r["intents"][key] = {"cmd": cmd, "params": params, "task_id": None,
                                 "at": datetime.now().isoformat()}
    return moved


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
    # rubric 回退口径(§8.3):打回率=rejected decision/decision 总数(用户行为,
    # 预筛拦截不产 decision 事件不计入),返工率=rework_total/产线数
    decisions = [e for e in r["events"] if e["kind"] == "decision"]
    rejected_n = sum(1 for e in decisions if e.get("decision") == "rejected")
    # 探索样本与假阴性(§5.3):只认"最终判定对应的那一稿"的预筛记录
    explore_lines = [
        ln for ln in r["lines"]
        if (ln.get("prescreen") or {}).get("explore")
        and (ln.get("prescreen") or {}).get("task_id") == ln.get("task_id")
    ]
    fn = sum(1 for ln in explore_lines if ln["status"] == "approved")
    report = {
        "approved": len(approved),
        "killed": len(killed),
        "rework_total": sum(ln["rework"] for ln in r["lines"]),
        "approved_tasks": [ln["task_id"] for ln in approved],
        "rubric_version": r["goal"].get("rubric_version"),
        "reject_rate": round(rejected_n / len(decisions), 3) if decisions else None,
        "rework_rate": round(sum(ln["rework"] for ln in r["lines"]) / len(r["lines"]), 3),
        "prescreen": {
            "intercepted": sum(int(ln.get("prescreen_intercepts") or 0) for ln in r["lines"]),
            "explore": len(explore_lines),
            "false_negatives": fn,
        },
        "summary_lines": summary,
        "finished_at": datetime.now().isoformat(),
    }
    r["report"] = report
    r["status"] = "done"
    r["stage"] = "done"
    # 探索战绩回写 current.json 评估降级(假阴性率>30% → 只警告不拦截;失败不挡收盘)
    if explore_lines and isinstance(r["goal"].get("rubric_version"), int):
        try:
            rubric_store.update_fn_stats(
                r["round_id"], r["goal"]["rubric_version"], len(explore_lines), fn,
            )
        except Exception:  # noqa: BLE001
            logger.warning("[rounds] fn_stats 回写失败", exc_info=True)
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
