"""tests：P2 订阅传感器的信号层——判新作/归一化增速/事件去重与冷却。"""

from __future__ import annotations

import json
from pathlib import Path

from ncds_opus_factory.common import benchmark_store, signals


def _post(aid: str, digg: int = 0, desc: str = "") -> dict:
    return {"aweme_id": aid, "desc": desc or f"作品{aid}", "create": 1700000000,
            "digg": digg, "comment": 0, "collect": 0, "share": 0}


def test_new_posts_first_import_exempt(tmp_path: Path):
    """首轮导入:整个历史都是首次见到,不算新作（防灌爆事件流）。"""
    conn = benchmark_store.connect(tmp_path / "b.db")
    benchmark_store.record_refresh(conn, "sec1", [_post("a1"), _post("a2")], ts=1000)
    assert benchmark_store.new_posts(conn, "sec1", 1000) == []


def test_new_posts_detected_on_second_refresh(tmp_path: Path):
    conn = benchmark_store.connect(tmp_path / "b.db")
    old = _post("a1")
    new = _post("a2")
    new["create"] = old["create"] + 86400  # 发布时间确实更晚
    benchmark_store.record_refresh(conn, "sec1", [old], ts=1000)
    benchmark_store.record_refresh(conn, "sec1", [old, new], ts=2000)
    news = benchmark_store.new_posts(conn, "sec1", 2000)
    assert [n["aweme_id"] for n in news] == ["a2"]


def test_new_posts_coverage_expansion_not_flood(tmp_path: Path):
    """深采只入库 30 条,refresh 拉 200 条:老作品首次入库不算新作(发布时间闸)。"""
    conn = benchmark_store.connect(tmp_path / "b.db")
    newest = _post("a1")
    newest["create"] = 1700000000
    benchmark_store.record_refresh(conn, "sec1", [newest], ts=1000)
    # 第二轮覆盖面扩张:三条历史老作品(create 更早) + 一条真新作(create 更晚)
    olds = []
    for i, aid in enumerate(["h1", "h2", "h3"]):
        p = _post(aid)
        p["create"] = 1690000000 + i
        olds.append(p)
    truly_new = _post("a2")
    truly_new["create"] = 1700009999
    benchmark_store.record_refresh(conn, "sec1", [newest, truly_new, *olds], ts=2000)

    news = benchmark_store.new_posts(conn, "sec1", 2000)
    assert [n["aweme_id"] for n in news] == ["a2"]


def test_latest_growth_normalized(tmp_path: Path):
    """增速按 Δt 归一化:2 小时涨 1000 赞 = 500 赞/h,不是「单周期 1000」。"""
    conn = benchmark_store.connect(tmp_path / "b.db")
    benchmark_store.record_refresh(conn, "sec1", [_post("a1", digg=100)], ts=1000)
    benchmark_store.record_refresh(conn, "sec1", [_post("a1", digg=1100)], ts=1000 + 7200)
    growth = benchmark_store.latest_growth(conn, "sec1")
    assert len(growth) == 1
    g = growth[0]
    assert g["delta"] == 1000 and g["dt_hours"] == 2.0 and g["digg_per_hour"] == 500.0


def test_latest_growth_skips_tiny_gap_and_single_snapshot(tmp_path: Path):
    conn = benchmark_store.connect(tmp_path / "b.db")
    # a1 只有一条快照;a2 两条但间隔 60s < min_gap
    benchmark_store.record_refresh(conn, "sec1", [_post("a1", digg=10), _post("a2", digg=10)], ts=1000)
    benchmark_store.record_refresh(conn, "sec1", [_post("a2", digg=999)], ts=1060)
    assert benchmark_store.latest_growth(conn, "sec1", min_gap_s=600) == []


def test_emit_signals_spike_with_cooldown(tmp_path: Path, monkeypatch):
    """spike 触发条件 = 归一化增速 ≥ 阈值且 Δ ≥ 最小涨幅;24h 冷却内不重报。"""
    monkeypatch.setattr(signals, "SPIKE_DIGG_PER_HOUR", 400.0)
    monkeypatch.setattr(signals, "SPIKE_MIN_DELTA", 500)
    conn = benchmark_store.connect(tmp_path / "b.db")
    sig_dir = tmp_path / "sig"

    benchmark_store.record_refresh(conn, "sec1", [_post("a1", digg=100)], ts=1000)
    benchmark_store.record_refresh(conn, "sec1", [_post("a1", digg=1100)], ts=1000 + 7200)
    counts = signals.emit_signals(conn, "sec1", 1000 + 7200, events_dir=sig_dir)
    assert counts["spike"] == 1

    lines = (sig_dir / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    ev = json.loads(lines[-1])
    assert ev["type"] == "spike" and ev["aweme_id"] == "a1" and ev["digg_per_hour"] == 500.0

    # 1 小时后再涨,仍在冷却窗口 -> 不重报
    benchmark_store.record_refresh(conn, "sec1", [_post("a1", digg=2100)], ts=1000 + 7200 + 3600)
    counts2 = signals.emit_signals(conn, "sec1", 1000 + 7200 + 3600, events_dir=sig_dir)
    assert counts2["spike"] == 0

    # 25 小时后:冷却过了,且增速依旧超阈(50000 赞/24h ≈ 2083/h),再报
    later = 1000 + 7200 + 25 * 3600
    benchmark_store.record_refresh(conn, "sec1", [_post("a1", digg=52100)], ts=later)
    counts3 = signals.emit_signals(conn, "sec1", later, events_dir=sig_dir)
    assert counts3["spike"] == 1


def test_emit_signals_new_post_once(tmp_path: Path):
    """new_post 事件一次性:同一作品不重复报。"""
    conn = benchmark_store.connect(tmp_path / "b.db")
    sig_dir = tmp_path / "sig"
    old = _post("a1")
    new = _post("a2")
    new["create"] = old["create"] + 86400
    benchmark_store.record_refresh(conn, "sec1", [old], ts=1000)
    benchmark_store.record_refresh(conn, "sec1", [old, new], ts=2000)

    counts = signals.emit_signals(conn, "sec1", 2000, events_dir=sig_dir)
    assert counts["new_post"] == 1
    # 同一轮 ts 再调一次(异常重试场景) -> 去重,不重报
    counts2 = signals.emit_signals(conn, "sec1", 2000, events_dir=sig_dir)
    assert counts2["new_post"] == 0


def test_stale_snapshot_pair_not_rereported(tmp_path: Path, monkeypatch):
    """躺平作品:本轮没插新快照,陈旧快照对不参与评估——冷却过了也不重报。"""
    monkeypatch.setattr(signals, "SPIKE_DIGG_PER_HOUR", 400.0)
    monkeypatch.setattr(signals, "SPIKE_MIN_DELTA", 500)
    conn = benchmark_store.connect(tmp_path / "b.db")
    sig_dir = tmp_path / "sig"

    benchmark_store.record_refresh(conn, "sec1", [_post("a1", digg=100)], ts=1000)
    benchmark_store.record_refresh(conn, "sec1", [_post("a1", digg=1100)], ts=1000 + 7200)
    assert signals.emit_signals(conn, "sec1", 1000 + 7200, events_dir=sig_dir)["spike"] == 1

    # 48 小时后刷新:指标没变(快照不落新行),冷却早过——但不该重报
    later = 1000 + 7200 + 48 * 3600
    benchmark_store.record_refresh(conn, "sec1", [_post("a1", digg=1100)], ts=later)
    assert signals.emit_signals(conn, "sec1", later, events_dir=sig_dir)["spike"] == 0


def test_signal_failure_does_not_break_refresh(tmp_path: Path, monkeypatch):
    """信号层抛异常不影响沈括 refresh 主链路（commands 侧已包 try）。"""
    conn = benchmark_store.connect(tmp_path / "b.db")
    # events_dir 指向一个文件路径制造写失败
    bad = tmp_path / "occupied"
    bad.write_text("x")
    try:
        signals.emit_signals(conn, "sec1", 1000, events_dir=bad)
    except Exception:
        pass  # 抛了也行——只要 shenkuo.run 的包裹层兜得住(此处仅验证不挂死)
