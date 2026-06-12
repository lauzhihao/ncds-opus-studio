"""排产策略协程（docs/WOLONG-DESIGN.md §8.4 第 1 条）：信号事件→深采→选题补货。

每 NOF_PLANNER_INTERVAL_S（默认 300s）一轮 tick，三件事：

1. 事件消费：续读 state/shenkuo/events.jsonl（字节偏移存 events_offset.json），
   new_post/spike → 深采该条（shenkuo {"aweme": id}, source=cron）并登记链
   （planner_chains.json）；
2. 链推进：深采到终态（completed/failed/cancelled 任一——失败也不阻塞选题，
   author_{sec_uid}/all_posts.json 由订阅刷新轮持续在写，不依赖本次深采）→
   用该文件作 benchmark_path 派鬼谷子（category 必须显式 None——guiguzi 默认
   'growth' 过滤不匹配会 ValueError；avoid=库内全部非 expired title）；
3. 库存补货：选题库 fresh < NOF_TOPIC_LOW_WATER（默认 5）→
   wolong_rounds.discover_benchmark() 自动发现对标派鬼谷子（server import
   commands 方向合法）。

纪律（§8.4 已裁定）：
- source 一律 cron：复用 _maybe_auto_archive 豁免与 _cron 配额桶，不新增 TaskSource；
- 派发前 quota_remaining 自查，<=0 即停，绝不制造注定 failed 的任务；
- 「防重索引检查→submit」之间不插 await（单事件循环原子性，RUNNER.submit 体内无 await）；
- offset 语义：一批事件全部处理完才原子写新值；配额中断只推进到已成功处理的
  最后一行之后；crash 在 submit 后 offset 前 → 重读重放，防重索引兜住
  （宁可重派也不丢事件）；
- 冷启动（无事件文件/无选题库/无对标数据）一律静默空转（debug 日志），
  绝不产 failed 任务、绝不抛异常出 tick。

路径约定同 topic_store：state_root = NOF_STATE_DIR 的父目录，生产未设 env 时
落仓库根 state/，此时与沈括 emit_signals/all_posts 的写入口径一致。
**设了 NOF_STATE_DIR 则口径分叉**——生产者（shenkuo BENCH/BENCH_DB、
discover_benchmark）写死仓库根，planner 读 env 路径，事件链会静默空转；
planner_loop 启动时对此告警（tests 靠该 env 隔离，生产部署不设它，见 §8.6）。
NOF_PLANNER=0 整体停用（app.py 判）。events.jsonl 本期不轮转（TODO）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ncds_opus_factory.commands import wolong_rounds
from ncds_opus_factory.common import topic_store
from ncds_opus_factory.server.task_runner import TaskRunner
from ncds_opus_factory.server.task_store import TaskStore

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[planner] %s 非法(%r),使用默认 %.1f", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        logger.warning("[planner] %s 非法(%r),使用默认 %d", name, raw, default)
        return default


INTERVAL_S = max(1.0, _env_float("NOF_PLANNER_INTERVAL_S", 300.0))
LOW_WATER = _env_int("NOF_TOPIC_LOW_WATER", 5)
_ERROR_RETRY_S = 300

_EVENT_TYPES = ("new_post", "spike")
_INFLIGHT = ("pending", "running")
_TERMINAL = ("completed", "failed", "cancelled")
# 防重窗口:同 params 的 cron 任务在途或 24h 内创建过 -> 不重派
_DEDUP_WINDOW = timedelta(hours=24)
# 链龄上限:深采任务被清扫/卡死等异常形态,不让链文件无限膨胀
_CHAIN_MAX_AGE = timedelta(hours=48)


# ---------------------------------------------------------------------------
# 路径(全部运行时解析,不在 import 期固化——tests/conftest.py 用 env 隔离)
# ---------------------------------------------------------------------------
def _state_root() -> Path:
    """NOF_STATE_DIR 指向 state/tasks,排产状态在兄弟目录 state/shenkuo/ 下。"""
    sd = os.environ.get("NOF_STATE_DIR")
    if sd:
        return Path(sd).parent
    return _ROOT / "state"


def shenkuo_dir() -> Path:
    return _state_root() / "shenkuo"


def events_path() -> Path:
    return shenkuo_dir() / "events.jsonl"


def offset_path() -> Path:
    return shenkuo_dir() / "events_offset.json"


def chains_path() -> Path:
    return shenkuo_dir() / "planner_chains.json"


def benchmark_path_for(sec_uid: str) -> Path:
    """订阅刷新轮持续在写的作者全量作品文件(shenkuo.py:604 同一口径)。"""
    return _state_root() / "benchmark" / f"author_{sec_uid}" / "all_posts.json"


def _now_iso() -> str:
    return datetime.now().isoformat()


def _parse_iso(raw: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _atomic_write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# offset / 链文件
# ---------------------------------------------------------------------------
def read_offset() -> int:
    p = offset_path()
    if not p.exists():
        return 0
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return max(0, int(raw.get("offset", 0)))
    except (json.JSONDecodeError, OSError, TypeError, ValueError, AttributeError):
        logger.warning("[planner] offset 文件损坏,重置 0: %s", p)
        return 0


def write_offset(offset: int) -> None:
    _atomic_write_json(offset_path(), {"offset": int(offset)})


def load_chains() -> list[dict[str, Any]]:
    p = chains_path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("[planner] 链文件损坏,按空处理: %s", p)
        return []
    if not isinstance(raw, list):
        logger.warning("[planner] 链文件格式异常(%s),按空处理", type(raw).__name__)
        return []
    return [c for c in raw if isinstance(c, dict)]


def save_chains(chains: list[dict[str, Any]]) -> None:
    _atomic_write_json(chains_path(), chains)


# ---------------------------------------------------------------------------
# 防重索引:每轮一次 list_tasks() 建索引,严禁每条目全表扫
# ---------------------------------------------------------------------------
@dataclass
class _Index:
    # aweme_id -> 最近一个(在途或 24h 内创建)cron 深采任务 id
    shenkuo_recent: dict[str, str] = field(default_factory=dict)
    # 在途或 24h 内创建的 cron 鬼谷子的 benchmark_path 集合
    guiguzi_recent_bench: set[str] = field(default_factory=set)
    # 是否存在在途(pending/running) cron 鬼谷子(补货防堆积闸)
    guiguzi_inflight: bool = False


def _build_index(store: TaskStore) -> _Index:
    idx = _Index()
    cutoff = datetime.now() - _DEDUP_WINDOW
    for m in store.list_tasks():  # created_at 倒序,首个命中即最新
        if m.source != "cron":
            continue
        inflight = m.status in _INFLIGHT
        created = _parse_iso(m.created_at)
        # created_at 不可解析按"不新鲜"处理:宁可重派也不丢事件(§8.4)
        recent = inflight or (created is not None and created >= cutoff)
        if m.cmd == "shenkuo":
            aweme = str(m.params.get("aweme") or "")
            if aweme and recent:
                idx.shenkuo_recent.setdefault(aweme, m.task_id)
        elif m.cmd == "guiguzi":
            if inflight:
                idx.guiguzi_inflight = True
            bench = str(m.params.get("benchmark_path") or "")
            if bench and recent:
                idx.guiguzi_recent_bench.add(bench)
    return idx


# ---------------------------------------------------------------------------
# 1) 事件消费
# ---------------------------------------------------------------------------
def _register_chain(chains: list[dict[str, Any]], aweme: str, sec_uid: str,
                    task_id: str) -> bool:
    """登记链(按 aweme_id 去重幂等)。返回是否有新增。"""
    if any(str(c.get("aweme_id")) == aweme for c in chains):
        return False
    chains.append({
        "aweme_id": aweme,
        "sec_uid": sec_uid,
        "shenkuo_task_id": task_id,
        "created_at": _now_iso(),
    })
    return True


async def _consume_events(
    runner: TaskRunner, idx: _Index, chains: list[dict[str, Any]],
) -> int:
    """续读 events.jsonl,派深采并登记链。返回本轮消费的事件行数。

    链文件先于 offset 持久化:crash 在两次写之间 -> 重读重放,防重索引兜住。
    """
    ep = events_path()
    if not ep.exists():
        logger.debug("[planner] 无事件文件,空转: %s", ep)
        return 0
    stored = read_offset()
    offset = stored
    try:
        size = ep.stat().st_size
    except OSError:
        logger.warning("[planner] 事件文件不可读,本轮跳过: %s", ep)
        return 0
    if offset > size:
        logger.warning("[planner] offset(%d) 超过事件文件大小(%d),文件疑似被换,重置 0",
                       offset, size)
        offset = 0
    with ep.open("rb") as f:
        f.seek(offset)
        data = f.read()

    consumed_lines = 0
    chains_dirty = False
    pos = 0  # 相对 offset 的已消费字节数(只在整行处理完后推进)
    while True:
        nl = data.find(b"\n", pos)
        if nl < 0:
            # 防御:最后的不完整行不消费(O_APPEND 整批写保证行完整,但不赌)
            break
        line = data[pos:nl]
        line_end = nl + 1
        ev: Any = None
        try:
            ev = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("[planner] 坏事件行,跳过照常推进: %r", line[:120])
        if isinstance(ev, dict) and ev.get("type") in _EVENT_TYPES:
            aweme = str(ev.get("aweme_id") or "").strip()
            sec_uid = str(ev.get("sec_uid") or "").strip()
            if not aweme or not sec_uid:
                logger.warning("[planner] 事件缺 aweme_id/sec_uid,跳过: %r", line[:120])
            else:
                existing = idx.shenkuo_recent.get(aweme)
                if existing is not None:
                    # 防重命中(在途或 24h 内已深采):不重派;链缺失则补登记
                    # ——覆盖「crash 在 submit 后、链落盘前」的重放窗口
                    chains_dirty |= _register_chain(chains, aweme, sec_uid, existing)
                elif runner.quota_remaining("shenkuo", source="cron") <= 0:
                    logger.warning("[planner] cron 配额耗尽,事件消费停在 offset=%d",
                                   offset + pos)
                    break
                else:
                    # 「防重检查→submit」之间无 await:同一事件循环内原子
                    try:
                        task_id = await runner.submit(
                            "shenkuo", {"aweme": aweme}, source="cron")
                    except Exception:  # noqa: BLE001 — 派发失败不消费该行,下轮重试
                        logger.exception("[planner] 深采派发失败,事件消费暂停: %s", aweme)
                        break
                    idx.shenkuo_recent[aweme] = task_id
                    chains_dirty |= _register_chain(chains, aweme, sec_uid, task_id)
                    logger.info("[planner] 信号深采派发: aweme=%s -> %s", aweme, task_id)
        pos = line_end
        consumed_lines += 1

    new_offset = offset + pos
    if chains_dirty:
        save_chains(chains)  # 链先于 offset 落盘
    if new_offset != stored:
        write_offset(new_offset)
    return consumed_lines


# ---------------------------------------------------------------------------
# 2) 链推进:深采终态 -> 派鬼谷子
# ---------------------------------------------------------------------------
async def _advance_chains(
    runner: TaskRunner, store: TaskStore, idx: _Index,
    chains: list[dict[str, Any]],
) -> int:
    """扫链文件,终态的深采转派鬼谷子。返回本轮派出的鬼谷子数。"""
    if not chains:
        return 0
    submitted = 0
    dirty = False
    halted = False
    kept: list[dict[str, Any]] = []
    now = datetime.now()
    for chain in chains:
        if halted:  # 配额中断:剩余链原样保留,下轮再推
            kept.append(chain)
            continue
        aweme = str(chain.get("aweme_id") or "")
        sec_uid = str(chain.get("sec_uid") or "")
        task_id = str(chain.get("shenkuo_task_id") or "")
        if not sec_uid or not task_id:
            logger.warning("[planner] 链条目缺字段,丢弃: %r", chain)
            dirty = True
            continue
        created = _parse_iso(chain.get("created_at"))
        if created is None or now - created > _CHAIN_MAX_AGE:
            logger.warning("[planner] 链超龄(>%dh),强制丢弃: aweme=%s",
                           int(_CHAIN_MAX_AGE.total_seconds() // 3600), aweme)
            dirty = True
            continue
        meta = store.get_meta(task_id)
        if meta is None:
            logger.warning("[planner] 链指向的深采任务不存在(已清扫?),丢弃: %s", task_id)
            dirty = True
            continue
        if meta.status not in _TERMINAL:
            kept.append(chain)  # 深采未到终态,下轮再看
            continue
        # 终态(失败也不阻塞选题——all_posts.json 由订阅刷新轮持续在写)
        bench = benchmark_path_for(sec_uid)
        if not bench.exists():
            logger.warning("[planner] 对标数据缺失,销链: %s", bench)
            dirty = True
            continue
        bench_str = str(bench)
        if bench_str in idx.guiguzi_recent_bench:
            logger.info("[planner] 24h 内已有同对标的 cron 鬼谷子,销链不派: %s",
                        sec_uid[:16])
            dirty = True
            continue
        if runner.quota_remaining("guiguzi", source="cron") <= 0:
            logger.warning("[planner] cron 配额耗尽,链推进暂停")
            kept.append(chain)
            halted = True
            continue
        avoid = topic_store.active_titles()  # 纯文件 IO,非 await
        try:
            # category 必须显式 None:guiguzi 默认 'growth' 过滤不匹配会 ValueError(§8.4)
            gid = await runner.submit(
                "guiguzi",
                {"benchmark_path": bench_str, "category": None, "avoid": avoid},
                source="cron",
            )
        except Exception:  # noqa: BLE001 — 单链失败保留,下轮重试
            logger.exception("[planner] 鬼谷子派发失败,链保留: %s", sec_uid[:16])
            kept.append(chain)
            continue
        idx.guiguzi_recent_bench.add(bench_str)
        idx.guiguzi_inflight = True
        submitted += 1
        dirty = True
        logger.info("[planner] 链推进派鬼谷子: %s -> %s", sec_uid[:16], gid)
    if dirty:
        chains[:] = kept
        save_chains(chains)
    return submitted


# ---------------------------------------------------------------------------
# 3) 库存补货
# ---------------------------------------------------------------------------
async def _maybe_restock(runner: TaskRunner, idx: _Index) -> str | None:
    """选题库 fresh 低水位 -> 自动发现对标派鬼谷子。返回 task_id(未派返回 None)。"""
    fresh = topic_store.count_fresh()
    if fresh >= LOW_WATER:
        return None
    benchmark = wolong_rounds.discover_benchmark()
    if not benchmark:
        logger.debug("[planner] 库存低(%d/%d)但无对标数据,补货空转", fresh, LOW_WATER)
        return None
    if benchmark in idx.guiguzi_recent_bench:
        # 与链推进同口径的 24h 防重:鬼谷子产出撞 avoid 抬不动库存时,
        # 没有这道闸会每 tick 对同一对标反复烧 LLM 直到当日 cron 配额耗尽
        logger.info("[planner] 库存低(%d/%d)但 24h 内已对该对标派过 cron 鬼谷子,补货冷却",
                    fresh, LOW_WATER)
        return None
    if idx.guiguzi_inflight:
        logger.info("[planner] 库存低(%d/%d)但已有在途 cron 鬼谷子,跳过补货(防堆积)",
                    fresh, LOW_WATER)
        return None
    if runner.quota_remaining("guiguzi", source="cron") <= 0:
        logger.warning("[planner] 库存低(%d/%d)但 cron 配额耗尽,补货暂缓", fresh, LOW_WATER)
        return None
    avoid = topic_store.active_titles()
    # 「防重检查(guiguzi_inflight)→submit」之间无 await
    task_id = await runner.submit(
        "guiguzi",
        {"benchmark_path": benchmark, "category": None, "avoid": avoid},
        source="cron",
    )
    idx.guiguzi_inflight = True
    logger.info("[planner] 库存补货派鬼谷子: fresh=%d/%d -> %s", fresh, LOW_WATER, task_id)
    return task_id


# ---------------------------------------------------------------------------
# tick / loop
# ---------------------------------------------------------------------------
async def planner_tick(runner: TaskRunner, store: TaskStore) -> dict[str, int]:
    """跑一轮排产。冷启动三缺失(事件/选题库/对标)一律静默空转。"""
    idx = _build_index(store)
    chains = load_chains()
    events = await _consume_events(runner, idx, chains)
    guiguzi = await _advance_chains(runner, store, idx, chains)
    restock = await _maybe_restock(runner, idx)
    return {
        "events_consumed": events,
        "guiguzi_dispatched": guiguzi,
        "restock_dispatched": 1 if restock else 0,
    }


async def planner_loop(runner: TaskRunner, store: TaskStore) -> None:
    """常驻循环:每 NOF_PLANNER_INTERVAL_S 一轮。"""
    logger.info("[planner] 排产协程启动: 周期 %.0fs, 选题低水位 %d", INTERVAL_S, LOW_WATER)
    if os.environ.get("NOF_STATE_DIR"):
        logger.warning(
            "[planner] NOF_STATE_DIR 已设置:排产按其父目录读 events/all_posts,"
            "但生产者(沈括)写死仓库根 state/——口径不一致时事件链将静默空转(模块 docstring)")
    while True:
        try:
            stats = await planner_tick(runner, store)
            if any(stats.values()):
                logger.info("[planner] 本轮: 事件 %d, 链转鬼谷子 %d, 补货 %d",
                            stats["events_consumed"], stats["guiguzi_dispatched"],
                            stats["restock_dispatched"])
            await asyncio.sleep(INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 循环必须活着
            logger.exception("[planner] tick failed")
            await asyncio.sleep(_ERROR_RETRY_S)
