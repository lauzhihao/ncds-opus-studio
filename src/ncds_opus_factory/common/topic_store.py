"""选题库 v2（docs/WOLONG-DESIGN.md §8.4 第 2 条）：鬼谷子产出的统一入库与消费。

文件：<state_root>/benchmark/topics/topics.json
（state_root = NOF_STATE_DIR 的父目录;生产未设 env 时落仓库根 state/——
与改造前 guiguzi 直写的 ROOT/state/benchmark/topics/topics.json 是同一文件。）

v2 格式::

    {"version": 2, "topics": [
        {"topic_id": "...", "title": "...", "motif": "?", "why": "?", "angle": "?",
         "potential": 8.5, "source": "guiguzi|legacy|...",
         "status": "fresh|consumed|expired", "created_at": "...",
         "consumed_by": "round_..."}
    ]}

- topic_id = sha1(title)[:12]，title 是全库唯一键（merge 按 title 精确去重）。
- 读到旧裸 list 自动迁移：topic_id 补算;consumed_by 存在 → consumed 否则 fresh;
  created_at 取迁移时刻;potential 容 float;source 缺省 "legacy"
  （旧条目自带的 source 是对标来源标题,迁移按"缺省补"语义原样保留）。
- 惰性过期：读时 fresh 且 created_at 超 NOF_TOPIC_TTL_DAYS（默认 14）天 →
  标 expired,下一次写盘持久化（读操作本身不写盘）。
- expired 条目可被 merge 复活成 fresh：active_titles()（喂 avoid）刻意不含
  expired,鬼谷子重新提出过期老题是合法补货,跳过它会变成"永远提、永远进不了库"。

并发安全：排产协程（server 线程）与卧龙段（worker 线程）会并发读写——
模块级按 resolve 后路径共享 threading.Lock（同 round_store._LOCKS 模式），
写盘一律 tmp + os.replace 原子替换;损坏 JSON 按空库容错（原文留 .corrupt 备份）。

锁序纪律（全仓统一）：round 锁在外、topic 锁在内。本模块全部操作是纯文件 IO,
允许在 RoundStore.mutate 锁内调用;反向（topic 锁内拿 round 锁）禁止。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_TTL_DAYS = 14.0

# 锁注册表按"解析后的文件路径"共享:跨调用方（guiguzi/mock/卧龙段/排产协程）
# 同一库文件必须是同一把锁
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.Lock()
        return _LOCKS[key]


def _state_root() -> Path:
    """NOF_STATE_DIR 指向 state/tasks,选题库在兄弟目录 state/benchmark/。"""
    sd = os.environ.get("NOF_STATE_DIR")
    if sd:
        return Path(sd).parent
    return _ROOT / "state"


def default_topics_path() -> Path:
    return _state_root() / "benchmark" / "topics" / "topics.json"


def _ttl_days() -> float:
    """env 解析容错:非法值 warning + 回默认,不成为启动单点。"""
    raw = os.environ.get("NOF_TOPIC_TTL_DAYS", "").strip()
    if not raw:
        return DEFAULT_TTL_DAYS
    try:
        return float(raw)
    except ValueError:
        logger.warning("[topics] NOF_TOPIC_TTL_DAYS 非法(%r),使用默认 %.0f", raw, DEFAULT_TTL_DAYS)
        return DEFAULT_TTL_DAYS


def topic_id_for(title: str) -> str:
    return hashlib.sha1(str(title).encode("utf-8")).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now().isoformat()


def _potential(t: dict[str, Any]) -> float:
    try:
        return float(t.get("potential") or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# 锁内读写（迁移 + 惰性过期都在读路径里完成,写路径负责持久化）
# ---------------------------------------------------------------------------
def _normalize(item: dict[str, Any]) -> bool:
    """v2 条目补缺（幂等）。返回是否有修补。"""
    dirty = False
    if not item.get("topic_id"):
        item["topic_id"] = topic_id_for(str(item.get("title") or ""))
        dirty = True
    if item.get("status") not in ("fresh", "consumed", "expired"):
        item["status"] = "consumed" if item.get("consumed_by") else "fresh"
        dirty = True
    if not item.get("created_at"):
        item["created_at"] = _now_iso()
        dirty = True
    return dirty


def _load_locked(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """读库（含旧 list 迁移、补缺、惰性过期）。返回 (topics, dirty)。

    损坏 JSON：warning + 按空库处理,原文留 .corrupt 备份不丢。
    """
    raw: Any = None
    dirty = False
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("[topics] 选题库损坏,按空库处理(原文备份 .corrupt): %s", path)
            try:
                backup = path.with_suffix(path.suffix + ".corrupt")
                backup.write_bytes(path.read_bytes())
            except OSError:
                logger.warning("[topics] .corrupt 备份写入失败", exc_info=True)
            raw = None

    legacy = False
    if isinstance(raw, dict):
        items = raw.get("topics") if isinstance(raw.get("topics"), list) else []
    elif isinstance(raw, list):
        items = raw
        legacy = True
        dirty = True
    else:
        if raw is not None:
            logger.warning("[topics] 选题库格式无法识别(%s),按空库处理", type(raw).__name__)
        items = []

    topics: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t in items:
        if not isinstance(t, dict):
            dirty = True
            continue
        title = str(t.get("title") or "").strip()
        if not title or title in seen:
            dirty = True
            continue
        seen.add(title)
        item = dict(t)
        item["title"] = title
        if legacy:
            # 旧裸 list 迁移:topic_id 补算、status 按 consumed_by 推断、
            # created_at 取迁移时刻(均由 _normalize 完成)
            item.pop("topic_id", None)
            item.pop("status", None)
            item.pop("created_at", None)
            item["potential"] = _potential(item)
            item.setdefault("source", "legacy")
        dirty |= _normalize(item)
        topics.append(item)

    # 惰性过期:读时标记,持久化等下一次写盘
    ttl = timedelta(days=_ttl_days())
    now = datetime.now()
    for t in topics:
        if t.get("status") != "fresh":
            continue
        try:
            created = datetime.fromisoformat(str(t.get("created_at")))
        except (TypeError, ValueError):
            continue  # created_at 不可解析:宁可不过期,别误杀
        if now - created > ttl:
            t["status"] = "expired"
            dirty = True
    return topics, dirty


def _save_locked(path: Path, topics: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": 2, "updated_at": _now_iso(), "topics": topics}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------
def load(path: str | Path | None = None) -> dict[str, Any]:
    """整库快照（v2 dict,迁移/过期已应用于内存,不写盘）。"""
    p = Path(path) if path else default_topics_path()
    with _lock_for(p):
        topics, _ = _load_locked(p)
    return {"version": 2, "topics": topics}


def fresh(limit: int | None = None, path: str | Path | None = None) -> list[dict[str, Any]]:
    """全部 fresh 选题,按 potential 降序。"""
    p = Path(path) if path else default_topics_path()
    with _lock_for(p):
        topics, _ = _load_locked(p)
    out = sorted((t for t in topics if t.get("status") == "fresh"),
                 key=_potential, reverse=True)
    return out[:limit] if limit is not None else out


def count_fresh(path: str | Path | None = None) -> int:
    return len(fresh(path=path))


def active_titles(path: str | Path | None = None) -> list[str]:
    """全部非 expired 的 title（fresh + consumed）,给鬼谷子 avoid 用。"""
    p = Path(path) if path else default_topics_path()
    with _lock_for(p):
        topics, _ = _load_locked(p)
    return [str(t.get("title")) for t in topics if t.get("status") != "expired"]


def merge(
    new_topics: list[dict[str, Any]] | None,
    source: str,
    path: str | Path | None = None,
) -> dict[str, int]:
    """合并写入（按 title 精确去重）。返回 {"added": n, "skipped": m}。

    新题补 topic_id / status=fresh / created_at / source=source;
    条目自带的 "source"（鬼谷子 schema 里是对标来源标题）挪到 bench_source,
    不与库的溯源字段混用。已存在的 fresh/consumed 同名题跳过;
    expired 同名题复活成 fresh（见模块 docstring）。
    """
    p = Path(path) if path else default_topics_path()
    added = skipped = 0
    with _lock_for(p):
        topics, dirty = _load_locked(p)
        by_title = {t.get("title"): t for t in topics}
        now = _now_iso()
        for t in new_topics or []:
            if not isinstance(t, dict):
                skipped += 1
                continue
            title = str(t.get("title") or "").strip()
            if not title:
                skipped += 1
                continue
            existing = by_title.get(title)
            if existing is not None and existing.get("status") != "expired":
                skipped += 1
                continue
            item = dict(t)
            item["title"] = title
            if "source" in item and item["source"] != source:
                item["bench_source"] = item.pop("source")
            item["source"] = source
            item["topic_id"] = topic_id_for(title)
            item["status"] = "fresh"
            item["created_at"] = now
            item["potential"] = _potential(item)
            item.pop("consumed_by", None)
            if existing is not None:  # expired 同名题:原位复活
                topics[topics.index(existing)] = item
            else:
                topics.append(item)
            by_title[title] = item
            added += 1
            dirty = True
        if dirty:
            _save_locked(p, topics)
    return {"added": added, "skipped": skipped}


def consume(
    topic_ids: list[str] | None,
    round_id: str,
    path: str | Path | None = None,
) -> int:
    """按 topic_id 批量标记 consumed + consumed_by=round_id。返回实际标记数。

    已 consumed 的条目不抢占（重入/竞态时先到先得）。
    """
    p = Path(path) if path else default_topics_path()
    ids = {str(i) for i in (topic_ids or []) if i}
    n = 0
    with _lock_for(p):
        topics, dirty = _load_locked(p)
        for t in topics:
            if t.get("topic_id") in ids and t.get("status") != "consumed":
                t["status"] = "consumed"
                t["consumed_by"] = round_id
                t["consumed_at"] = _now_iso()
                n += 1
                dirty = True
        if dirty:
            _save_locked(p, topics)
    return n


def take(
    limit: int,
    round_id: str,
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """原子挑题+预占：单次锁内「回收本 round 预占 -> fresh 补足 -> 全部标 consumed」。

    两阶段写协议：本函数落盘是**预占**,调用方的 round 文件落盘是**确认**——
    round 落盘前崩溃,重放按 consumed_by==round_id 把预占题原样捡回（回收优先,
    各按 potential 降序）,不泄漏库存、不误判"无新鲜选题"。挑与标在同一把锁内,
    并发 round 抢题时后到者只拿排除已占后的剩余,不会撞题。

    返回挑中条目的副本（回收题在前）;库存不足返回不足的列表。
    """
    p = Path(path) if path else default_topics_path()
    out: list[dict[str, Any]] = []
    with _lock_for(p):
        topics, dirty = _load_locked(p)
        reclaim = sorted(
            (t for t in topics
             if t.get("status") == "consumed" and t.get("consumed_by") == round_id),
            key=_potential, reverse=True)
        avail = sorted((t for t in topics if t.get("status") == "fresh"),
                       key=_potential, reverse=True)
        now = _now_iso()
        for t in (reclaim + avail)[: max(0, int(limit))]:
            if t.get("status") != "consumed":
                t["status"] = "consumed"
                t["consumed_by"] = round_id
                t["consumed_at"] = now
                dirty = True
            out.append(dict(t))
        if dirty:
            _save_locked(p, topics)
    return out


def count_available(round_id: str, path: str | Path | None = None) -> int:
    """本 round 可用库存 = fresh + 本 round 已预占（consumed_by==round_id）。

    开盘段崩溃重放时,崩溃前 take() 预占的题对本 round 仍可用（会被回收）——
    只数 fresh 会让重放误走鬼谷子路径,预占题永久泄漏。
    """
    p = Path(path) if path else default_topics_path()
    with _lock_for(p):
        topics, _ = _load_locked(p)
    return sum(
        1 for t in topics
        if t.get("status") == "fresh"
        or (t.get("status") == "consumed" and t.get("consumed_by") == round_id)
    )
