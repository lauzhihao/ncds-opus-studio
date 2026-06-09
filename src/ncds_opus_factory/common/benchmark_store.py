"""沈括指标层:对标号作品的 SQLite 时间序列存储。

为什么单独一层:网络(tikhub_client)/编排(commands/shenkuo)/存储(本模块)解耦,可独立测。
为什么 SQLite:未来多个沈括实例并发刷新,需要扛并发写 + 跨号查询;WAL 模式天然满足,
比 per-account JSON(整文件覆盖 + 写竞争)稳。

两张表:
- posts             作品身份(desc/create_time),基本不变,first_seen/last_seen 追踪首末次见到。
- metric_snapshots  每次刷新一条快照(digg/comment/collect/share);仅当指标较上次有变化才插,
                    避免无变化时灌重复行 —— 既留增长曲线,又不爆库。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    sec_uid     TEXT NOT NULL,
    aweme_id    TEXT NOT NULL,
    desc        TEXT,
    create_time INTEGER,
    first_seen  INTEGER,
    last_seen   INTEGER,
    PRIMARY KEY (sec_uid, aweme_id)
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
    aweme_id TEXT NOT NULL,
    sec_uid  TEXT NOT NULL,
    ts       INTEGER NOT NULL,
    digg     INTEGER,
    comment  INTEGER,
    collect  INTEGER,
    share    INTEGER,
    PRIMARY KEY (aweme_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_snap_sec_ts ON metric_snapshots (sec_uid, ts);
CREATE INDEX IF NOT EXISTS idx_snap_aweme ON metric_snapshots (aweme_id, ts);
"""

# 一条快照里参与"是否有变化"比较的指标列
_METRIC_COLS = ("digg", "comment", "collect", "share")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开(必要时建)DB,开 WAL,建表。调用方负责 close。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 多实例并发写的前提
    conn.execute("PRAGMA busy_timeout=5000")  # 写锁竞争时等 5s 而非立刻报错
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def upsert_posts(conn: sqlite3.Connection, sec_uid: str, posts: Iterable[dict], ts: int) -> int:
    """写作品身份。新作品记 first_seen=last_seen=ts;已存在的刷新 last_seen 和 desc。

    返回处理的作品条数。posts 为 tikhub_client.simplify_aweme 的精简条目。
    """
    rows = [p for p in posts if p.get("aweme_id")]
    for p in rows:
        conn.execute(
            """
            INSERT INTO posts (sec_uid, aweme_id, desc, create_time, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(sec_uid, aweme_id) DO UPDATE SET
                desc = excluded.desc,
                last_seen = excluded.last_seen
            """,
            (sec_uid, str(p["aweme_id"]), p.get("desc", ""), p.get("create", 0), ts, ts),
        )
    conn.commit()
    return len(rows)


def _latest_metrics(conn: sqlite3.Connection, aweme_id: str) -> sqlite3.Row | None:
    cur = conn.execute(
        "SELECT digg, comment, collect, share FROM metric_snapshots "
        "WHERE aweme_id = ? ORDER BY ts DESC LIMIT 1",
        (aweme_id,),
    )
    return cur.fetchone()


def record_snapshot(conn: sqlite3.Connection, sec_uid: str, posts: Iterable[dict], ts: int) -> int:
    """为每条作品追加一条指标快照,但仅当指标较上次有变化(或首次)才插。

    返回实际新增的快照行数(未变化的作品被跳过)。
    """
    inserted = 0
    for p in posts:
        aid = str(p.get("aweme_id") or "")
        if not aid:
            continue
        cur_vals = (p.get("digg", 0), p.get("comment", 0), p.get("collect", 0), p.get("share", 0))
        prev = _latest_metrics(conn, aid)
        if prev is not None and tuple(prev[c] for c in _METRIC_COLS) == cur_vals:
            continue  # 指标没变,不灌重复行
        conn.execute(
            "INSERT OR REPLACE INTO metric_snapshots "
            "(aweme_id, sec_uid, ts, digg, comment, collect, share) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, sec_uid, ts, *cur_vals),
        )
        inserted += 1
    conn.commit()
    return inserted


def record_refresh(conn: sqlite3.Connection, sec_uid: str, posts: list[dict], ts: int) -> dict[str, int]:
    """一次刷新的便捷封装:写身份 + 追加变化的快照。返回 {posts, snapshots}。"""
    n_posts = upsert_posts(conn, sec_uid, posts, ts)
    n_snap = record_snapshot(conn, sec_uid, posts, ts)
    return {"posts": n_posts, "snapshots": n_snap}


# --------------------------------------------------------------------------- #
# 查询助手
# --------------------------------------------------------------------------- #
def top_by_digg(conn: sqlite3.Connection, sec_uid: str, limit: int = 10) -> list[dict[str, Any]]:
    """按最新一条快照的点赞数倒序取 top N。返回 [{aweme_id, desc, digg, comment, collect, ts}]。"""
    cur = conn.execute(
        """
        SELECT p.aweme_id, p.desc, s.digg, s.comment, s.collect, s.share, s.ts
        FROM posts p
        JOIN metric_snapshots s ON s.aweme_id = p.aweme_id
        JOIN (SELECT aweme_id, MAX(ts) AS mts FROM metric_snapshots GROUP BY aweme_id) m
             ON m.aweme_id = s.aweme_id AND m.mts = s.ts
        WHERE p.sec_uid = ?
        ORDER BY s.digg DESC
        LIMIT ?
        """,
        (sec_uid, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def growth(conn: sqlite3.Connection, aweme_id: str, since_ts: int) -> dict[str, int] | None:
    """某条作品自 since_ts 起的指标涨幅(最新快照 - since_ts 当时或之后第一条之前的基线)。

    取 since_ts 之前最近一条作基线(没有则取最早一条),与最新一条相减。无快照返回 None。
    """
    latest = conn.execute(
        "SELECT digg, comment, collect, share, ts FROM metric_snapshots "
        "WHERE aweme_id = ? ORDER BY ts DESC LIMIT 1",
        (aweme_id,),
    ).fetchone()
    if latest is None:
        return None
    base = conn.execute(
        "SELECT digg, comment, collect, share FROM metric_snapshots "
        "WHERE aweme_id = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
        (aweme_id, since_ts),
    ).fetchone()
    if base is None:  # since_ts 早于一切快照,用最早一条当基线
        base = conn.execute(
            "SELECT digg, comment, collect, share FROM metric_snapshots "
            "WHERE aweme_id = ? ORDER BY ts ASC LIMIT 1",
            (aweme_id,),
        ).fetchone()
    return {c: int(latest[c]) - int(base[c]) for c in _METRIC_COLS}
