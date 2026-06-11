"""信号事件层（docs/WOLONG-DESIGN.md §6.1）：refresh 后检测信号，落事件流。

沈括每轮刷新（refresh_only 或深采）写完指标层后调用 emit_signals：
- new_post：作者出了新作品（first_seen == 本轮 ts 且发布时间晚于已知最大值；
  首轮导入与覆盖面扩张都不算新作——见 benchmark_store.new_posts 的两道闸）
- spike：  既有作品指标飙升（归一化增速 ≥ 阈值，且仅评估本轮真插了新快照的作品）

事件落 {events_dir}/events.jsonl（逐行 JSON，append-only），供排产（P5）消费；
消费端自管 offset，本层只负责生产与去重：
- 去重索引在 benchmark.db 的 signal_dedup 表（沈括并发=2，JSON 文件读改写会丢更新；
  SQLite WAL 天然扛并发）。new_post 一次性；spike 有冷却窗口（默认 24h）。
- events.jsonl 用 O_APPEND 单次 write 落整批，并发 writer 不交错。

阈值可调（env）：NOF_SPIKE_DIGG_PER_HOUR（默认 500 赞/小时）、
NOF_SPIKE_MIN_DELTA（默认 1000 赞，防小基数高速率误报）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.common import benchmark_store

logger = logging.getLogger(__name__)

SPIKE_DIGG_PER_HOUR = float(os.environ.get("NOF_SPIKE_DIGG_PER_HOUR", "500"))
SPIKE_MIN_DELTA = int(os.environ.get("NOF_SPIKE_MIN_DELTA", "1000"))
SPIKE_COOLDOWN_S = 24 * 3600
_DEDUP_RETENTION_S = 7 * 24 * 3600


def _noop(_text: str) -> None:
    return None


def _append_events(path: Path, events: list[dict[str, Any]]) -> None:
    """整批拼好后单次 O_APPEND write:两个并发 refresh 任务的事件行不会交错。"""
    payload = "".join(json.dumps(ev, ensure_ascii=False) + "\n" for ev in events)
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)


def emit_signals(
    conn: sqlite3.Connection,
    sec_uid: str,
    ts: int,
    events_dir: str | Path,
    on_progress: Callable[[str], None] = _noop,
) -> dict[str, int]:
    """检测本轮刷新的信号并落事件流。返回 {new_post, spike} 计数。"""
    events_dir = Path(events_dir)
    events_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []

    for p in benchmark_store.new_posts(conn, sec_uid, ts):
        key = f"new_post:{p['aweme_id']}"
        if benchmark_store.dedup_get(conn, key) is not None:
            continue  # 同轮重试等场景,不重报
        benchmark_store.dedup_set(conn, key, ts)
        events.append({
            "type": "new_post",
            "aweme_id": str(p["aweme_id"]),
            "sec_uid": sec_uid,
            "ts": ts,
            "desc": (p.get("desc") or "")[:120],
        })

    # 只评估本轮真插了新快照的作品:躺平作品的陈旧快照对不参与,防 24h 周期性重报
    for g in benchmark_store.latest_growth(conn, sec_uid, refresh_ts=ts):
        if g["digg_per_hour"] < SPIKE_DIGG_PER_HOUR or g["delta"] < SPIKE_MIN_DELTA:
            continue
        key = f"spike:{g['aweme_id']}"
        last = benchmark_store.dedup_get(conn, key)
        if last is not None and ts - last < SPIKE_COOLDOWN_S:
            continue  # 同一作品持续上涨,冷却窗口内只报一次
        benchmark_store.dedup_set(conn, key, ts)
        events.append({
            "type": "spike",
            "aweme_id": str(g["aweme_id"]),
            "sec_uid": sec_uid,
            "ts": ts,
            "desc": (g.get("desc") or "")[:120],
            "digg": g["digg"],
            "digg_per_hour": g["digg_per_hour"],
            "dt_hours": g["dt_hours"],
        })

    if events:
        _append_events(events_dir / "events.jsonl", events)
        # 剪过期 spike 冷却戳控库;new_post 键即便被剪也不会重报(first_seen 不可变)
        benchmark_store.dedup_prune(conn, ts - _DEDUP_RETENTION_S)
        for ev in events:
            tag = "新作品" if ev["type"] == "new_post" else f"飙升 {ev.get('digg_per_hour')}赞/h"
            on_progress(f"信号[{tag}]: {ev['desc'][:40]}")

    return {"new_post": sum(1 for e in events if e["type"] == "new_post"),
            "spike": sum(1 for e in events if e["type"] == "spike")}
