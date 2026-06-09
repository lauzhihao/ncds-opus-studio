"""benchmark_store 单测:upsert 幂等 / 快照只在变化时追加 / growth 计算。"""

from __future__ import annotations

from ncds_opus_factory.common import benchmark_store as bs

SEC = "MS4test_sec_uid"


def _post(aweme_id: str, digg: int, comment: int = 0, collect: int = 0, share: int = 0, desc: str = "d"):
    return {"aweme_id": aweme_id, "desc": desc, "digg": digg, "comment": comment,
            "collect": collect, "share": share, "create": 1700000000}


def test_upsert_idempotent(tmp_path):
    conn = bs.connect(tmp_path / "b.db")
    posts = [_post("a1", 10), _post("a2", 20)]
    assert bs.upsert_posts(conn, SEC, posts, ts=1000) == 2
    # 再写一次同样的 -> 仍是 2 条作品(不重复插)
    bs.upsert_posts(conn, SEC, posts, ts=2000)
    n = conn.execute("SELECT COUNT(*) FROM posts WHERE sec_uid=?", (SEC,)).fetchone()[0]
    assert n == 2
    # last_seen 被刷新到 2000,first_seen 保持 1000
    row = conn.execute("SELECT first_seen, last_seen FROM posts WHERE aweme_id='a1'").fetchone()
    assert row["first_seen"] == 1000 and row["last_seen"] == 2000
    conn.close()


def test_snapshot_only_on_change(tmp_path):
    conn = bs.connect(tmp_path / "b.db")
    posts = [_post("a1", 10)]
    # 首次:插
    assert bs.record_snapshot(conn, SEC, posts, ts=1000) == 1
    # 指标没变:跳过
    assert bs.record_snapshot(conn, SEC, posts, ts=2000) == 0
    # 指标变了:插
    assert bs.record_snapshot(conn, SEC, [_post("a1", 99)], ts=3000) == 1
    rows = conn.execute("SELECT ts, digg FROM metric_snapshots WHERE aweme_id='a1' ORDER BY ts").fetchall()
    assert [(r["ts"], r["digg"]) for r in rows] == [(1000, 10), (3000, 99)]
    conn.close()


def test_record_refresh_combines(tmp_path):
    conn = bs.connect(tmp_path / "b.db")
    stat = bs.record_refresh(conn, SEC, [_post("a1", 10), _post("a2", 5)], ts=1000)
    assert stat == {"posts": 2, "snapshots": 2}
    # 第二次:a1 涨、a2 不变 -> 1 条新快照
    stat2 = bs.record_refresh(conn, SEC, [_post("a1", 30), _post("a2", 5)], ts=2000)
    assert stat2["snapshots"] == 1
    conn.close()


def test_top_by_digg_uses_latest(tmp_path):
    conn = bs.connect(tmp_path / "b.db")
    bs.record_refresh(conn, SEC, [_post("a1", 10), _post("a2", 20)], ts=1000)
    # a1 反超 a2
    bs.record_refresh(conn, SEC, [_post("a1", 99), _post("a2", 21)], ts=2000)
    top = bs.top_by_digg(conn, SEC, limit=2)
    assert [t["aweme_id"] for t in top] == ["a1", "a2"]
    assert top[0]["digg"] == 99  # 取最新快照,不是历史
    conn.close()


def test_growth(tmp_path):
    conn = bs.connect(tmp_path / "b.db")
    bs.record_refresh(conn, SEC, [_post("a1", 100, comment=5)], ts=1000)
    bs.record_refresh(conn, SEC, [_post("a1", 180, comment=12)], ts=2000)
    g = bs.growth(conn, "a1", since_ts=1000)
    assert g["digg"] == 80 and g["comment"] == 7
    # since 早于一切快照 -> 用最早一条当基线,涨幅同上
    g2 = bs.growth(conn, "a1", since_ts=0)
    assert g2["digg"] == 80
    # 不存在的作品
    assert bs.growth(conn, "nope", since_ts=0) is None
    conn.close()
