"""沈括(shenkuo)单测:TikHub 封装解析/分页、幂等、编排(全打桩,离线可跑)。"""

from __future__ import annotations

import json

import pytest

from ncds_opus_factory.commands import shenkuo
from ncds_opus_factory.common import tikhub_client


@pytest.fixture(autouse=True)
def _disable_ytdlp(monkeypatch):
    """单测禁用 yt-dlp 优先,让下载走 TikHub 兜底,复用现有 tikhub_client 打桩。
    yt-dlp 优先/回退路径由 capabilities/download_test.py 专门覆盖。"""
    monkeypatch.setattr(shenkuo.capabilities.download, "_ytdlp_download", lambda *a, **k: None)


# --------------------------------------------------------------------------- #
# tikhub_client
# --------------------------------------------------------------------------- #
def test_simplify_aweme():
    a = {
        "aweme_id": 123, "desc": "测试", "create_time": 1700,
        "statistics": {"digg_count": 100, "comment_count": 5, "share_count": 2, "collect_count": 9},
    }
    assert tikhub_client.simplify_aweme(a) == {
        "aweme_id": "123", "desc": "测试", "digg": 100, "comment": 5, "share": 2, "collect": 9,
        "create": 1700, "cover_url": "", "duration": 0,  # 无 video 字段 -> 封面/时长回退
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


def test_fetch_user_posts_ignores_flaky_has_more(monkeypatch):
    # 抖音回归:page1 谎报 has_more=False(cursor 仍在前进),不该就此停,要翻到空页/cursor 归 0 为止。
    pages = [
        ([{"aweme_id": "1", "statistics": {}}, {"aweme_id": "2", "statistics": {}}], 100, False),  # 假 has_more=False
        ([{"aweme_id": "3", "statistics": {}}, {"aweme_id": "4", "statistics": {}}], 200, True),
        ([], 0, False),  # 真到底:空页
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
    assert [p["aweme_id"] for p in out] == ["1", "2", "3", "4"]  # 没被假 has_more 截断


def test_merge_all_posts_never_shrinks(tmp_path):
    # 回归:短趟(只 2 条)不该把已存的 5 条覆盖丢 —— 只增不减,fresh 覆盖重叠项 stats,create 倒序。
    p = tmp_path / "all_posts.json"
    existing = [{"aweme_id": str(i), "create": i, "digg": i} for i in range(1, 6)]  # 5 条
    p.write_text(json.dumps(existing), encoding="utf-8")
    fresh = [{"aweme_id": "3", "create": 3, "digg": 999}, {"aweme_id": "6", "create": 6, "digg": 6}]
    merged = shenkuo._merge_all_posts(p, fresh)
    assert {m["aweme_id"] for m in merged} == {"1", "2", "3", "4", "5", "6"}  # 5 旧 + 1 新,没丢
    assert next(m["digg"] for m in merged if m["aweme_id"] == "3") == 999  # 重叠项取 fresh 新 stats
    assert merged[0]["aweme_id"] == "6"  # create 倒序,最新在前


def test_fetch_posts_multipass_unions_flaky_passes(monkeypatch):
    # 抖音单趟欠收:pass1 回 2 条,pass2 回 4 条,pass3 无新增 -> 并集 4 条、第 3 趟后停。
    seq = [
        [{"aweme_id": "1"}, {"aweme_id": "2"}],
        [{"aweme_id": "1"}, {"aweme_id": "2"}, {"aweme_id": "3"}, {"aweme_id": "4"}],
        [{"aweme_id": "1"}, {"aweme_id": "2"}, {"aweme_id": "3"}, {"aweme_id": "4"}],
    ]
    calls = {"n": 0}

    def fake(author, max_items, on_progress=None):
        r = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return r

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_user_posts", fake)
    out = shenkuo._fetch_posts_multipass("sec", 200, shenkuo._noop)
    assert {p["aweme_id"] for p in out} == {"1", "2", "3", "4"}
    assert calls["n"] == 3  # 第 3 趟跑了、发现 0 新增才停


def test_fetch_posts_multipass_tolerates_exception(monkeypatch):
    # 某趟抛异常(如 ReadTimeout)只丢该趟、用已得的,不让整次采集失败。
    seq = [[{"aweme_id": "1"}, {"aweme_id": "2"}], "boom", [{"aweme_id": "2"}, {"aweme_id": "3"}]]
    calls = {"n": 0}

    def fake(author, max_items, on_progress=None):
        item = seq[calls["n"]]
        calls["n"] += 1
        if item == "boom":
            raise RuntimeError("ReadTimeout simulated")
        return item

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_user_posts", fake)
    out = shenkuo._fetch_posts_multipass("sec", 200, shenkuo._noop)
    assert {p["aweme_id"] for p in out} == {"1", "2", "3"}  # 异常趟跳过,前后并集


# --------------------------------------------------------------------------- #
# shenkuo helpers
# --------------------------------------------------------------------------- #
def test_read_dashscope_key_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    assert shenkuo.capabilities.read_dashscope_key() == "sk-test"


# --------------------------------------------------------------------------- #
# collect_one：产物按 平台+作品id 落作品仓库 state/works/{platform}/{aweme_id}/
# --------------------------------------------------------------------------- #
def _works_env(tmp_path, monkeypatch):
    """works 仓库根随 NOF_STATE_DIR -> tmp;COLLECTED(历史抠图根)也指 tmp,离线隔离。"""
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.setattr(shenkuo, "COLLECTED", tmp_path / "figure_collected")


def _fake_frames(v, od, mf):
    od.mkdir(parents=True, exist_ok=True)
    f = od / "frame_001.jpg"
    f.write_bytes(b"J")
    return [f]


def _fake_cut(frames, od, **kw):
    od.mkdir(parents=True, exist_ok=True)
    c = od / "frame_001.png"
    c.write_bytes(b"P")
    return [c]


def test_top_comments_from_items_filters_and_sorts():
    """>10 赞才入选,按赞降序,取前 top_n,塑成精简结构。"""
    items = [
        {"nickname": "a", "text": "低赞", "digg": 5, "ip": "x"},     # <=10 被滤
        {"nickname": "b", "text": "中", "digg": 50, "ip": "y"},
        {"nickname": "c", "text": "高", "digg": 200, "ip": "z"},
        {"nickname": "d", "text": "边界", "digg": 11, "ip": "w"},
    ]
    top = shenkuo.top_comments_from_items(items, top_n=2)
    assert [c["text"] for c in top] == ["高", "中"]
    assert top[0] == {"nickname": "c", "text": "高", "digg": 200, "ip": "z"}


def test_refresh_stats_comments(tmp_path, monkeypatch):
    """轻量刷新:重取播放数据 + 强制重取评论,只产出 stats/digg/comments/top_comments。"""
    _works_env(tmp_path, monkeypatch)
    aid = "555"
    monkeypatch.setattr(
        shenkuo.tikhub_client, "fetch_one_video_detail", lambda a, token=None: {"id": a})
    monkeypatch.setattr(
        shenkuo.tikhub_client, "extract_meta",
        lambda d: {"digg": 300, "comment": 20, "share": 5, "collect": 8})
    monkeypatch.setattr(
        shenkuo.tikhub_client, "fetch_top_comments",
        lambda a, top_n=20, on_progress=None: [
            {"nickname": "u", "text": "好", "digg": 99, "ip": "p"},
            {"nickname": "v", "text": "弱", "digg": 1, "ip": "q"},  # 被 >10 阈值滤掉
        ])

    patch = shenkuo.refresh_stats_comments(aid, platform="douyin", top_comments=20)
    assert patch["stats"] == {"digg": 300, "comment": 20, "share": 5, "collect": 8}
    assert patch["digg"] == 300
    assert [c["text"] for c in patch["top_comments"]] == ["好"]
    # comments.json 落盘(覆写)
    wdir = shenkuo.works_repo.work_dir("douyin", aid)
    assert (wdir / "comments.json").exists()


def test_collect_one_idempotent(tmp_path, monkeypatch):
    """作品仓库里产物齐全 -> 全 cached、不调任何真函数。"""
    _works_env(tmp_path, monkeypatch)
    aid = "999"
    wdir = shenkuo.works_repo.work_dir("douyin", aid)
    (wdir / "video.mp4").write_bytes(b"x")
    (wdir / "asr.paraformer.json").write_text("{}", encoding="utf-8")
    (wdir / "asr.txt").write_text("t", encoding="utf-8")
    (wdir / "asr.clean.txt").write_text("clean", encoding="utf-8")
    adir = wdir / "audio"
    adir.mkdir()
    for n in ("original.mp3", "vocals.mp3", "bgm.mp3"):
        (adir / n).write_bytes(b"a")
    (wdir / "frames").mkdir()
    (wdir / "frames" / "frame_001.jpg").write_bytes(b"x")
    (wdir / "cutouts").mkdir()
    (wdir / "cutouts" / "frame_001.png").write_bytes(b"x")
    (wdir / "comments.json").write_text(json.dumps({"items": []}), encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("幂等应跳过,不该调用")

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_video_url", boom)
    monkeypatch.setattr(shenkuo.tikhub_client, "download_video", boom)
    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_top_comments", boom)
    monkeypatch.setattr(shenkuo.capabilities, "transcribe", boom)
    monkeypatch.setattr(shenkuo.capabilities, "extract_frames", boom)
    monkeypatch.setattr(shenkuo.capabilities, "cutout", boom)

    author_dir = tmp_path / "author_x"
    author_dir.mkdir()
    entry = shenkuo.collect_one(aid, author_dir)
    assert entry["status"]["download"] == "cached"
    assert entry["status"]["transcribe"] == "cached"
    assert entry["status"]["comments"] == "cached"
    assert len(entry["frames"]) == 1 and len(entry["cutouts"]) == 1
    # manifest 落盘,products 指向作品仓库
    m = shenkuo.works_repo.load_manifest("douyin", aid)
    assert m and m["products"]["asr"]["txt"].endswith("asr.txt")


def test_collect_one_fresh(tmp_path, monkeypatch):
    """首采:产物落作品仓库 state/works/douyin/{aid}/ + 写 manifest。"""
    _works_env(tmp_path, monkeypatch)
    author_dir = tmp_path / "author_x"
    author_dir.mkdir()
    aid = "123"

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_video_url", lambda a, token=None: "http://v/x.mp4")

    def fake_dl(url, out, **k):
        out.write_bytes(b"MP4")
        return str(out)

    monkeypatch.setattr(shenkuo.tikhub_client, "download_video", fake_dl)
    monkeypatch.setattr(shenkuo.capabilities, "transcribe", lambda v, op: ({"task": "x"}, "转写文案"))
    monkeypatch.setattr(shenkuo.capabilities, "separate_audio", lambda *a, **k: {})  # 跳过 ffmpeg/demucs
    monkeypatch.setattr(shenkuo.capabilities, "clean_transcript", lambda raw, op=shenkuo._noop: None)
    monkeypatch.setattr(shenkuo.capabilities, "extract_frames", _fake_frames)
    monkeypatch.setattr(shenkuo.capabilities, "cutout", _fake_cut)

    entry = shenkuo.collect_one(aid, author_dir, meta={"desc": "d", "digg": 99}, top_comments=0)
    wdir = shenkuo.works_repo.work_dir("douyin", aid)
    assert entry["status"]["download"] == "ok"
    assert entry["status"]["transcribe"] == "ok"
    assert entry["digg"] == 99
    assert (wdir / "asr.paraformer.json").exists()
    assert (wdir / "asr.txt").read_text(encoding="utf-8") == "转写文案"
    assert len(entry["frames"]) == 1 and len(entry["cutouts"]) == 1
    m = shenkuo.works_repo.load_manifest("douyin", aid)
    assert m["status"]["download"] == "ok" and m["products"]["video"]


def test_collect_one_adopts_legacy(tmp_path, monkeypatch):
    """历史 author_dir 产物原地兼容:软链入仓库、就地复用,不搬字节、不重采。"""
    _works_env(tmp_path, monkeypatch)
    aid = "777"
    author_dir = tmp_path / "author_legacy"
    author_dir.mkdir()
    (author_dir / f"{aid}.mp4").write_bytes(b"x")
    (author_dir / f"{aid}.paraformer.json").write_text("{}", encoding="utf-8")
    (author_dir / f"{aid}.txt").write_text("legacy", encoding="utf-8")
    (author_dir / f"{aid}.clean.txt").write_text("legacy-clean", encoding="utf-8")
    lfr = author_dir / aid / "frames"
    lfr.mkdir(parents=True)
    (lfr / "frame_001.jpg").write_bytes(b"x")
    lcut = tmp_path / "figure_collected" / aid
    lcut.mkdir(parents=True)
    (lcut / "frame_001.png").write_bytes(b"x")
    ladir = author_dir / aid / "audio"
    ladir.mkdir(parents=True)
    for n in ("original.mp3", "vocals.mp3", "bgm.mp3"):
        (ladir / n).write_bytes(b"a")

    def boom(*a, **k):
        raise AssertionError("应复用历史产物,不该重采")

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_video_url", boom)
    monkeypatch.setattr(shenkuo.tikhub_client, "download_video", boom)
    monkeypatch.setattr(shenkuo.capabilities, "transcribe", boom)
    monkeypatch.setattr(shenkuo.capabilities, "extract_frames", boom)
    monkeypatch.setattr(shenkuo.capabilities, "cutout", boom)

    entry = shenkuo.collect_one(aid, author_dir, top_comments=0)
    assert entry["status"]["download"] == "cached"
    assert entry["status"]["transcribe"] == "cached"
    wdir = shenkuo.works_repo.work_dir("douyin", aid)
    assert (wdir / "video.mp4").is_symlink()  # 软链,字节仍在旧位置
    assert (wdir / "asr.txt").read_text(encoding="utf-8") == "legacy"


def test_collect_one_shared_across_authors(tmp_path, monkeypatch):
    """同一作品出现在不同对标号:第二个对标号全部命中作品仓库,不重采。"""
    _works_env(tmp_path, monkeypatch)
    aid = "555"
    a1 = tmp_path / "author_1"
    a1.mkdir()
    a2 = tmp_path / "author_2"
    a2.mkdir()

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_video_url", lambda a, token=None: "http://v/x.mp4")
    monkeypatch.setattr(shenkuo.tikhub_client, "download_video",
                        lambda url, out, **k: out.write_bytes(b"MP4"))
    monkeypatch.setattr(shenkuo.capabilities, "transcribe", lambda v, op: ({"t": 1}, "文案"))
    monkeypatch.setattr(shenkuo.capabilities, "separate_audio", lambda *a, **k: {})
    monkeypatch.setattr(shenkuo.capabilities, "clean_transcript", lambda raw, op=shenkuo._noop: None)
    monkeypatch.setattr(shenkuo.capabilities, "extract_frames", _fake_frames)
    monkeypatch.setattr(shenkuo.capabilities, "cutout", _fake_cut)

    e1 = shenkuo.collect_one(aid, a1, meta={"desc": "d"}, top_comments=0)
    assert e1["status"]["download"] == "ok" and e1["status"]["transcribe"] == "ok"

    def boom(*a, **k):
        raise AssertionError("跨对标号同作品应命中缓存,不该重采")

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_video_url", boom)
    monkeypatch.setattr(shenkuo.tikhub_client, "download_video", boom)
    monkeypatch.setattr(shenkuo.capabilities, "transcribe", boom)
    monkeypatch.setattr(shenkuo.capabilities, "extract_frames", boom)
    monkeypatch.setattr(shenkuo.capabilities, "cutout", boom)

    e2 = shenkuo.collect_one(aid, a2, meta={"desc": "d"}, top_comments=0)
    assert e2["status"]["download"] == "cached"
    assert e2["status"]["transcribe"] == "cached"
    assert len(e2["frames"]) == 1 and len(e2["cutouts"]) == 1


# --------------------------------------------------------------------------- #
# run 作者模式编排
# --------------------------------------------------------------------------- #
def test_run_author_picks_top_by_digg(tmp_path, monkeypatch):
    monkeypatch.setattr(shenkuo, "BENCH", tmp_path / "benchmark")
    monkeypatch.setattr(shenkuo, "COLLECTED", tmp_path / "collected")
    monkeypatch.setattr(shenkuo, "BENCH_DB", tmp_path / "shenkuo" / "benchmark.db")
    posts = [
        {"aweme_id": "1", "desc": "a", "digg": 10},
        {"aweme_id": "2", "desc": "b", "digg": 99},
        {"aweme_id": "3", "desc": "c", "digg": 50},
    ]
    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_user_posts",
                        lambda sec, max_items, on_progress=None: posts)
    seen: list[str] = []

    def fake_collect(aid, ad, meta=None, max_frames=8, engine="threshold",
                     top_comments=20, platform="douyin", on_progress=shenkuo._noop,
                     author_domain=None):
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

    crop = shenkuo.capabilities.crop_stage(im)
    assert crop.size == (int(200 * 0.96) - int(200 * 0.04), int(100 * 0.82) - int(100 * 0.13))

    rgba, ratio = shenkuo.capabilities.threshold_cutout(im)
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

    cuts = shenkuo.capabilities.cutout([black, content], tmp_path / "out", engine="threshold")
    names = [c.name for c in cuts]
    assert "content.png" in names
    assert "black.png" not in names  # 纯色过场被过滤
