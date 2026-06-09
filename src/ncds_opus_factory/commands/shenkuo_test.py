"""沈括(shenkuo)单测:TikHub 封装解析/分页、幂等、编排(全打桩,离线可跑)。"""

from __future__ import annotations

import json

from ncds_opus_factory.commands import shenkuo
from ncds_opus_factory.common import tikhub_client


# --------------------------------------------------------------------------- #
# tikhub_client
# --------------------------------------------------------------------------- #
def test_simplify_aweme():
    a = {
        "aweme_id": 123, "desc": "测试", "create_time": 1700,
        "statistics": {"digg_count": 100, "comment_count": 5, "share_count": 2, "collect_count": 9},
    }
    assert tikhub_client.simplify_aweme(a) == {
        "aweme_id": "123", "desc": "测试", "digg": 100, "comment": 5, "share": 2, "collect": 9, "create": 1700,
    }


def test_fetch_user_posts_paging(monkeypatch):
    pages = [
        ([{"aweme_id": "1", "desc": "a", "statistics": {"digg_count": 10}},
          {"aweme_id": "2", "desc": "b", "statistics": {"digg_count": 20}}], 100, True),
        ([{"aweme_id": "2", "desc": "b", "statistics": {"digg_count": 20}},  # 重复,应去重
          {"aweme_id": "3", "desc": "c", "statistics": {"digg_count": 30}}], 0, False),
    ]
    calls = {"n": 0}

    def fake_page(sec, cursor, count, token):
        p = pages[calls["n"]]
        calls["n"] += 1
        return p

    monkeypatch.setattr(tikhub_client, "get_token", lambda t=None: "tok")
    monkeypatch.setattr(tikhub_client, "fetch_user_posts_page", fake_page)
    monkeypatch.setattr(tikhub_client.time, "sleep", lambda *_: None)

    out = tikhub_client.fetch_user_posts("sec", max_items=10)
    assert [p["aweme_id"] for p in out] == ["1", "2", "3"]  # 去重 + 跨页
    assert out[2]["digg"] == 30


# --------------------------------------------------------------------------- #
# shenkuo helpers
# --------------------------------------------------------------------------- #
def test_read_dashscope_key_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    assert shenkuo._read_dashscope_key() == "sk-test"


# --------------------------------------------------------------------------- #
# collect_one 幂等
# --------------------------------------------------------------------------- #
def test_collect_one_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(shenkuo, "COLLECTED", tmp_path / "collected")
    author_dir = tmp_path / "author_x"
    author_dir.mkdir()
    aid = "999"
    # 预置全部产物 -> 应全 cached、不调任何真函数
    (author_dir / f"{aid}.mp4").write_bytes(b"x")
    (author_dir / f"{aid}.paraformer.json").write_text("{}", encoding="utf-8")
    (author_dir / f"{aid}.txt").write_text("t", encoding="utf-8")
    fdir = author_dir / aid / "frames"
    fdir.mkdir(parents=True)
    (fdir / "frame_001.jpg").write_bytes(b"x")
    cdir = tmp_path / "collected" / aid
    cdir.mkdir(parents=True)
    (cdir / "frame_001.png").write_bytes(b"x")

    def boom(*a, **k):
        raise AssertionError("幂等应跳过,不该调用")

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_video_url", boom)
    monkeypatch.setattr(shenkuo.tikhub_client, "download_video", boom)
    monkeypatch.setattr(shenkuo, "_transcribe", boom)
    monkeypatch.setattr(shenkuo, "_extract_frames", boom)
    monkeypatch.setattr(shenkuo, "_cutout", boom)

    entry = shenkuo.collect_one(aid, author_dir)
    assert entry["status"]["download"] == "cached"
    assert entry["status"]["transcribe"] == "cached"
    assert len(entry["frames"]) == 1
    assert len(entry["cutouts"]) == 1


def test_collect_one_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(shenkuo, "COLLECTED", tmp_path / "collected")
    author_dir = tmp_path / "author_x"
    author_dir.mkdir()

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_video_url", lambda aid, token=None: "http://v/x.mp4")

    def fake_dl(url, out, **k):
        out.write_bytes(b"MP4")
        return str(out)

    monkeypatch.setattr(shenkuo.tikhub_client, "download_video", fake_dl)
    monkeypatch.setattr(shenkuo, "_transcribe", lambda v, op: ({"task": "x"}, "转写文案"))

    def fake_frames(v, od, mf):
        od.mkdir(parents=True, exist_ok=True)
        f = od / "frame_001.jpg"
        f.write_bytes(b"J")
        return [f]

    def fake_cut(frames, od, **kw):
        od.mkdir(parents=True, exist_ok=True)
        c = od / "frame_001.png"
        c.write_bytes(b"P")
        return [c]

    monkeypatch.setattr(shenkuo, "_extract_frames", fake_frames)
    monkeypatch.setattr(shenkuo, "_cutout", fake_cut)

    entry = shenkuo.collect_one("123", author_dir, meta={"desc": "d", "digg": 99})
    assert entry["status"]["download"] == "ok"
    assert entry["status"]["transcribe"] == "ok"
    assert entry["digg"] == 99
    assert (author_dir / "123.paraformer.json").exists()
    assert (author_dir / "123.txt").read_text(encoding="utf-8") == "转写文案"
    assert len(entry["frames"]) == 1 and len(entry["cutouts"]) == 1


# --------------------------------------------------------------------------- #
# run 作者模式编排
# --------------------------------------------------------------------------- #
def test_run_author_picks_top_by_digg(tmp_path, monkeypatch):
    monkeypatch.setattr(shenkuo, "BENCH", tmp_path / "benchmark")
    monkeypatch.setattr(shenkuo, "COLLECTED", tmp_path / "collected")
    posts = [
        {"aweme_id": "1", "desc": "a", "digg": 10},
        {"aweme_id": "2", "desc": "b", "digg": 99},
        {"aweme_id": "3", "desc": "c", "digg": 50},
    ]
    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_user_posts",
                        lambda sec, max_items, on_progress=None: posts)
    seen: list[str] = []

    def fake_collect(aid, ad, meta=None, max_frames=8, engine="threshold", on_progress=shenkuo._noop):
        seen.append(aid)
        return {"aweme_id": aid, "digg": (meta or {}).get("digg"), "status": {"download": "ok"}}

    monkeypatch.setattr(shenkuo, "collect_one", fake_collect)

    result = shenkuo.run(author="secXYZ", top=2)
    ad = tmp_path / "benchmark" / "author_secXYZ"
    assert (ad / "all_posts.json").exists()
    assert seen == ["2", "3"]  # 按 digg 降序选 top2
    coll = json.loads((ad / "collected.json").read_text(encoding="utf-8"))
    assert len(coll["items"]) == 2
    assert result["all_posts"] == 3


# --------------------------------------------------------------------------- #
# 裁舞台区 + 阈值分割抠图
# --------------------------------------------------------------------------- #
def test_crop_stage_and_threshold_cutout():
    import numpy as np
    from PIL import Image

    arr = np.full((100, 200, 3), (245, 240, 225), dtype=np.uint8)  # 浅纸背景
    arr[40:70, 80:120] = (10, 10, 10)  # 中间一块黑剪影
    im = Image.fromarray(arr, "RGB")

    crop = shenkuo._crop_stage(im)
    assert crop.size == (int(200 * 0.96) - int(200 * 0.04), int(100 * 0.82) - int(100 * 0.13))

    rgba, ratio = shenkuo._threshold_cutout(im)
    assert rgba.mode == "RGBA"
    assert 0.0 < ratio < 0.3  # 黑块占小部分:有前景有背景


def test_cutout_filters_solid_frames(tmp_path):
    import numpy as np
    from PIL import Image

    # 纯黑帧(标题过场):前景占比≈1 -> 应被过滤
    black = tmp_path / "black.jpg"
    Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8), "RGB").save(black)
    # 正常内容帧:浅背景 + 一块黑 -> 保留
    arr = np.full((100, 200, 3), 240, dtype=np.uint8)
    arr[40:70, 80:120] = 10
    content = tmp_path / "content.jpg"
    Image.fromarray(arr, "RGB").save(content)

    cuts = shenkuo._cutout([black, content], tmp_path / "out", engine="threshold")
    names = [c.name for c in cuts]
    assert "content.png" in names
    assert "black.png" not in names  # 纯色过场被过滤
