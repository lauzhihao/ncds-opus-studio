"""排产策略协程（docs/WOLONG-DESIGN.md §8.4 第 1 条）：信号事件 → 沈括深采。

每 NOF_PLANNER_INTERVAL_S（默认 300s）一轮 tick：

1. 事件消费：续读 state/shenkuo/events.jsonl（字节偏移存 events_offset.json），
   new_post/spike → 深采该条（shenkuo {"aweme": id}, source=cron）并登记链
   （planner_chains.json，供深采溯源/防重）。

> 历史上 planner 还有「链推进 → benchmark 派鬼谷子」与「选题库低水位补货」两步，
> 已废弃：鬼谷子改为评论驱动（提取文案 + 用户选中高赞评论），只由画布前端/卧龙
> 显式触发，不再由 planner 用 all_posts.json 自动出题。

纪律（§8.4 已裁定）：
- source 一律 cron：复用 _maybe_auto_archive 豁免与 _cron 配额桶，不新增 TaskSource；
- 派发前 quota_remaining 自查，<=0 即停，绝不制造注定 failed 的任务；
- 「防重索引检查→submit」之间不插 await（单事件循环协作式原子性）——submit 体内
  create 在 await lpush 之前同步完成；两次并发 maybe_resume 在同一 event loop 上
  协作式串行，第一次 create 落盘后第二次扫 store 能看到，防卧龙双投（S3 步3）；
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

from ncds_opus_factory.server.queue import get_default_queue
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
    # platform:aweme_id -> 最近一个(在途或 24h 内创建)cron 深采任务 id
    shenkuo_recent: dict[str, str] = field(default_factory=dict)


def _work_key(platform: str, aweme: str) -> str:
    return f"{(platform or 'douyin').strip().lower() or 'douyin'}:{aweme}"


def _source_url_for(platform: str, aweme: str, author: str = "") -> str:
    platform = (platform or "douyin").strip().lower() or "douyin"
    if platform == "youtube":
        return f"https://www.youtube.com/watch?v={aweme}"
    if platform == "tiktok":
        handle = author
        if ":" in handle:
            handle = handle.split(":", 1)[1]
        handle = handle.strip().lstrip("@") or "_"
        return f"https://www.tiktok.com/@{handle}/video/{aweme}"
    return f"https://www.douyin.com/video/{aweme}"


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
                platform = str(m.params.get("platform") or "douyin")
                idx.shenkuo_recent.setdefault(_work_key(platform, aweme), m.task_id)
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
            platform = str(ev.get("platform") or "douyin").strip().lower() or "douyin"
            aweme = str(ev.get("aweme_id") or "").strip()
            sec_uid = str(ev.get("sec_uid") or "").strip()
            if not aweme or not sec_uid:
                logger.warning("[planner] 事件缺 aweme_id/sec_uid,跳过: %r", line[:120])
            else:
                key = _work_key(platform, aweme)
                existing = idx.shenkuo_recent.get(key)
                if existing is not None:
                    # 防重命中(在途或 24h 内已深采):不重派;链缺失则补登记
                    # ——覆盖「crash 在 submit 后、链落盘前」的重放窗口
                    chains_dirty |= _register_chain(chains, aweme, sec_uid, existing)
                elif await runner.quota_remaining("shenkuo", source="cron") <= 0:
                    logger.warning("[planner] cron 配额耗尽,事件消费停在 offset=%d",
                                   offset + pos)
                    break
                else:
                    # 「防重检查→submit」之间无 await:同一事件循环内原子
                    try:
                        task_id = await runner.submit(
                            "shenkuo",
                            {
                                "aweme": aweme,
                                "platform": platform,
                                "source_url": _source_url_for(platform, aweme, sec_uid),
                            },
                            source="cron",
                        )
                    except Exception:  # noqa: BLE001 — 派发失败不消费该行,下轮重试
                        logger.exception("[planner] 深采派发失败,事件消费暂停: %s", aweme)
                        break
                    idx.shenkuo_recent[key] = task_id
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
# 链推进 / 库存补货(鬼谷子自动派发)—— 已废弃。
# 鬼谷子改为评论驱动(提取文案 + 用户选中的高赞评论),只由画布前端/卧龙显式触发,
# 不再由 planner 用 benchmark(all_posts.json)自动出题。planner 现仅保留
# 「信号事件 -> 沈括深采」一职;链文件仍登记(供深采溯源/防重),但不再转派鬼谷子。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# tick / loop
# ---------------------------------------------------------------------------
async def planner_tick(runner: TaskRunner, store: TaskStore) -> dict[str, int]:
    """跑一轮排产:消费信号事件派沈括深采。冷启动(无事件)静默空转。"""
    idx = _build_index(store)
    chains = load_chains()
    events = await _consume_events(runner, idx, chains)
    return {"events_consumed": events}


async def planner_loop(runner: TaskRunner, store: TaskStore) -> None:
    """常驻循环:每 NOF_PLANNER_INTERVAL_S 一轮。"""
    logger.info("[planner] 排产协程启动: 周期 %.0fs", INTERVAL_S)
    if os.environ.get("NOF_STATE_DIR"):
        logger.warning(
            "[planner] NOF_STATE_DIR 已设置:排产按其父目录读 events,"
            "但生产者(沈括)写死仓库根 state/——口径不一致时事件链将静默空转(模块 docstring)")
    while True:
        try:
            # Redis 探活：down 时静默跳过本 tick，不创建垃圾 failed meta（决策 D）。
            if not await get_default_queue().ping():
                await asyncio.sleep(INTERVAL_S)
                continue
            stats = await planner_tick(runner, store)
            if any(stats.values()):
                logger.info("[planner] 本轮: 事件消费 %d", stats["events_consumed"])
            await asyncio.sleep(INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 循环必须活着
            logger.exception("[planner] tick failed")
            await asyncio.sleep(_ERROR_RETRY_S)
