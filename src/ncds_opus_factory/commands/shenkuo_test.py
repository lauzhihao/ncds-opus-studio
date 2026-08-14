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
        "platform": "douyin",
        "aweme_id": "123",
        "desc": "测试",
        "digg": 100,
        "comment": 5,
        "share": 2,
        "collect": 9,
        "create": 1700,
        "cover_url": "",
        "duration": 0,
        "share_url": "https://www.douyin.com/video/123",
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

    def fake(author, max_items, token=None, on_progress=None):
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

    def fake(author, max_items, token=None, on_progress=None):
        item = seq[calls["n"]]
        calls["n"] += 1
        if item == "boom":
            raise RuntimeError("ReadTimeout simulated")
        return item

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_user_posts", fake)
    out = shenkuo._fetch_posts_multipass("sec", 200, shenkuo._noop)
    assert {p["aweme_id"] for p in out} == {"1", "2", "3"}  # 异常趟跳过,前后并集


def test_fetch_posts_multipass_passes_platform(monkeypatch):
    seen = {}

    def fake(platform, author, max_items, token=None, on_progress=None):
        seen.update({"platform": platform, "author": author, "max_items": max_items})
        return [{"aweme_id": "tk1"}]

    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_author_posts", fake)

    out = shenkuo._fetch_posts_multipass("creator", 10, shenkuo._noop, platform="tiktok")

    assert out == [{"aweme_id": "tk1"}]
    assert seen == {"platform": "tiktok", "author": "creator", "max_items": 10}


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
                        lambda sec, max_items, token=None, on_progress=None: posts)
    seen: list[str] = []

    def fake_collect(aid, ad, meta=None, max_frames=8, engine="threshold",
                     top_comments=20, platform="douyin", on_progress=shenkuo._noop,
                     author_domain=None, source_url=None):
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


def test_run_tiktok_author_uses_platform_dir_and_source_url(tmp_path, monkeypatch):
    monkeypatch.setattr(shenkuo, "BENCH", tmp_path / "benchmark")
    monkeypatch.setattr(shenkuo, "COLLECTED", tmp_path / "collected")
    monkeypatch.setattr(shenkuo, "BENCH_DB", tmp_path / "shenkuo" / "benchmark.db")
    posts = [
        {
            "aweme_id": "7650894465690766623",
            "desc": "tk",
            "digg": 10,
            "share_url": "https://www.tiktok.com/@creator/video/7650894465690766623",
        }
    ]
    monkeypatch.setattr(
        shenkuo.tikhub_client,
        "fetch_author_posts",
        lambda platform, author, max_items, token=None, on_progress=None: posts,
    )
    monkeypatch.setattr(shenkuo.tikhub_client, "fetch_tiktok_profile", lambda *_a, **_k: None)
    seen: dict[str, object] = {}

    def fake_collect(aid, ad, **kwargs):
        seen["aid"] = aid
        seen["author_dir"] = ad
        seen.update(kwargs)
        return {"aweme_id": aid, "platform": kwargs["platform"], "status": {"download": "ok"}}

    monkeypatch.setattr(shenkuo, "collect_one", fake_collect)

    result = shenkuo.run(author="creator", platform="tiktok", top=1)

    ad = tmp_path / "benchmark" / "author_tiktok_creator"
    assert result["author_dir"] == str(ad)
    assert (ad / "all_posts.json").exists()
    assert seen["author_dir"] == ad
    assert seen["platform"] == "tiktok"
    assert seen["source_url"] == "https://www.tiktok.com/@creator/video/7650894465690766623"


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


# --------------------------------------------------------------------------- #
# 说话人分离(diarize):domain 开关 + branch 编排(打桩,不碰真模型)
# --------------------------------------------------------------------------- #
from pathlib import Path  # noqa: E402

from ncds_opus_factory.common.capabilities import diarize as diarize_mod  # noqa: E402


def _mk_diarize_run(tmp_path: Path) -> shenkuo._CollectRun:
    paths = shenkuo._CollectPaths(
        wdir=tmp_path,
        video=tmp_path / "video.mp4",
        para=tmp_path / "asr.paraformer.json",
        timeline=tmp_path / "asr.timeline.json",
        txt=tmp_path / "asr.txt",
        clean=tmp_path / "asr.clean.txt",
        speakers=tmp_path / "asr.speakers.json",
        dialogue=tmp_path / "dialogue.txt",
        cover=tmp_path / "cover.jpg",
        comments_path=tmp_path / "comments.json",
        audio_dir=tmp_path / "audio",
        frames_dir=tmp_path / "frames",
        cut_dir=tmp_path / "cutouts",
    )
    return shenkuo._CollectRun(
        aweme_id="w1", platform="douyin", source_url=None,
        entry={"status": {}}, paths=paths, max_frames=1, engine="threshold",
        top_comments=0, on_progress=lambda _s: None, check=lambda: False,
    )


def _two_speaker_result() -> diarize_mod.DiarizeResult:
    return diarize_mod.DiarizeResult(
        sentences=[
            diarize_mod.DiarizedSentence(text="你怎么又迟到?", start_ms=0, end_ms=1500, speaker=0),
            diarize_mod.DiarizedSentence(text="堵车了。", start_ms=1600, end_ms=2600, speaker=1),
        ],
        speaker_count=2,
    )


def test_needs_diarization_domain_switch(monkeypatch):
    monkeypatch.delenv("NOF_DIARIZE_DOMAINS", raising=False)
    assert shenkuo._needs_diarization("comedy") is True
    assert shenkuo._needs_diarization(" Comedy ") is True
    assert shenkuo._needs_diarization("finance") is False
    assert shenkuo._needs_diarization(None) is False
    assert shenkuo._needs_diarization("") is False
    monkeypatch.setenv("NOF_DIARIZE_DOMAINS", "comedy,film")
    assert shenkuo._needs_diarization("film") is True
    monkeypatch.setenv("NOF_DIARIZE_DOMAINS", "")
    assert shenkuo._needs_diarization("comedy") is False


def test_branch_diarize_prefers_vocals_and_writes_products(monkeypatch, tmp_path):
    run = _mk_diarize_run(tmp_path)
    run.paths.audio_dir.mkdir()
    (run.paths.audio_dir / "vocals.mp3").write_bytes(b"x")
    (run.paths.audio_dir / "original.mp3").write_bytes(b"x")
    seen: dict[str, Path] = {}

    def fake_diarize(source, on_progress):
        seen["source"] = source
        return _two_speaker_result()

    monkeypatch.setattr(shenkuo.diarize_cap, "diarize", fake_diarize)
    run.branch_diarize()

    assert seen["source"] == run.paths.audio_dir / "vocals.mp3"
    assert run.entry["status"]["diarize"] == "ok"
    doc = json.loads(run.paths.speakers.read_text(encoding="utf-8"))
    assert doc["speakerCount"] == 2
    assert doc["sentences"][0]["speakerLabel"] == "A"
    lines = run.paths.dialogue.read_text(encoding="utf-8").splitlines()
    assert lines == ["说话人A: 你怎么又迟到?", "说话人B: 堵车了。"]
    assert run.entry["speaker_count"] == 2
    assert run.entry["speakers"]
    assert run.entry["dialogue"]


def test_branch_diarize_falls_back_to_video_without_audio(monkeypatch, tmp_path):
    run = _mk_diarize_run(tmp_path)
    run.paths.video.write_bytes(b"x")
    seen: dict[str, Path] = {}

    def fake_diarize(source, on_progress):
        seen["source"] = source
        return _two_speaker_result()

    monkeypatch.setattr(shenkuo.diarize_cap, "diarize", fake_diarize)
    run.branch_diarize()
    assert seen["source"] == run.paths.video
    assert run.entry["status"]["diarize"] == "ok"


def test_branch_diarize_cached_skips_model(monkeypatch, tmp_path):
    run = _mk_diarize_run(tmp_path)
    run.paths.speakers.write_text(json.dumps({"speakerCount": 3, "sentences": []}), encoding="utf-8")

    def boom(source, on_progress):
        raise AssertionError("cached branch must not call diarize")

    monkeypatch.setattr(shenkuo.diarize_cap, "diarize", boom)
    run.branch_diarize()
    assert run.entry["status"]["diarize"] == "cached"
    assert run.entry["speaker_count"] == 3


def test_branch_diarize_unavailable_degrades(monkeypatch, tmp_path):
    run = _mk_diarize_run(tmp_path)
    run.paths.video.write_bytes(b"x")

    def unavailable(source, on_progress):
        raise diarize_mod.DiarizeUnavailableError("funasr not installed")

    monkeypatch.setattr(shenkuo.diarize_cap, "diarize", unavailable)
    run.branch_diarize()
    assert run.entry["status"]["diarize"] == "unavailable"
    assert not run.paths.speakers.exists()


def test_branch_diarize_no_media_skips(tmp_path):
    run = _mk_diarize_run(tmp_path)
    run.branch_diarize()
    assert run.entry["status"]["diarize"] == "skipped:no-audio"


def _cached_work(shenkuo_mod, aid: str):
    """铺满全套已采产物(下载/转写/音轨/帧/抠图/评论全 cached),只留 diarize 未做。"""
    wdir = shenkuo_mod.works_repo.work_dir("douyin", aid)
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
    return wdir


def test_collect_one_comedy_domain_triggers_diarize(tmp_path, monkeypatch):
    """comedy domain 的采集在音轨就绪后自动做说话人分离,产物进 manifest。"""
    _works_env(tmp_path, monkeypatch)
    monkeypatch.delenv("NOF_DIARIZE_DOMAINS", raising=False)
    aid = "8001"
    wdir = _cached_work(shenkuo, aid)
    seen: dict[str, object] = {}

    def fake_diarize(source, on_progress):
        seen["source"] = source
        return _two_speaker_result()

    monkeypatch.setattr(shenkuo.diarize_cap, "diarize", fake_diarize)
    author_dir = tmp_path / "author_c"
    author_dir.mkdir()
    entry = shenkuo.collect_one(aid, author_dir, author_domain="comedy")

    assert seen["source"] == wdir / "audio" / "vocals.mp3"
    assert entry["status"]["diarize"] == "ok"
    assert entry["speaker_count"] == 2
    m = shenkuo.works_repo.load_manifest("douyin", aid)
    assert m["products"]["speakers"]["json"].endswith("asr.speakers.json")
    assert m["products"]["speakers"]["dialogue"].endswith("dialogue.txt")
    assert m["products"]["speakers"]["count"] == 2


def test_collect_one_other_domain_skips_diarize(tmp_path, monkeypatch):
    """非多人对白 domain 不做说话人分离(重步骤不白跑)。"""
    _works_env(tmp_path, monkeypatch)
    monkeypatch.delenv("NOF_DIARIZE_DOMAINS", raising=False)
    aid = "8002"
    _cached_work(shenkuo, aid)

    def boom(source, on_progress):
        raise AssertionError("finance domain 不该触发 diarize")

    monkeypatch.setattr(shenkuo.diarize_cap, "diarize", boom)
    author_dir = tmp_path / "author_f"
    author_dir.mkdir()
    entry = shenkuo.collect_one(aid, author_dir, author_domain="finance")
    assert "diarize" not in entry["status"]


def test_collect_one_diarize_falls_back_to_manifest_domain(tmp_path, monkeypatch):
    """第二趟补采不带 author_domain 时,从作品 manifest 已有 domain 兜底触发。"""
    _works_env(tmp_path, monkeypatch)
    monkeypatch.delenv("NOF_DIARIZE_DOMAINS", raising=False)
    aid = "8003"
    _cached_work(shenkuo, aid)
    shenkuo.works_repo.merge("douyin", aid, domain="comedy")

    monkeypatch.setattr(
        shenkuo.diarize_cap, "diarize", lambda source, on_progress: _two_speaker_result())
    author_dir = tmp_path / "author_m"
    author_dir.mkdir()
    entry = shenkuo.collect_one(aid, author_dir)
    assert entry["status"]["diarize"] == "ok"


def test_branch_diarize_corrupt_cache_reruns(monkeypatch, tmp_path):
    """半截 JSON(写一半被杀)不算缓存,应重跑并原子重写。"""
    run = _mk_diarize_run(tmp_path)
    run.paths.speakers.write_text('{"speakerCount": 2, "sent', encoding="utf-8")
    run.paths.video.write_bytes(b"x")

    monkeypatch.setattr(
        shenkuo.diarize_cap, "diarize", lambda source, on_progress: _two_speaker_result())
    run.branch_diarize()
    assert run.entry["status"]["diarize"] == "ok"
    assert json.loads(run.paths.speakers.read_text(encoding="utf-8"))["speakerCount"] == 2


def test_branch_diarize_upgrades_mix_source_to_vocals(monkeypatch, tmp_path):
    """当年跑在混音源(video)上,如今人声轨就绪 -> 缓存作废,改吃 vocals 重算。"""
    run = _mk_diarize_run(tmp_path)
    doc = {"speakerCount": 1, "sentences": [], "source": "video.mp4"}
    run.paths.speakers.write_text(json.dumps(doc), encoding="utf-8")
    run.paths.audio_dir.mkdir()
    (run.paths.audio_dir / "vocals.mp3").write_bytes(b"x")
    seen: dict[str, Path] = {}

    def fake_diarize(source, on_progress):
        seen["source"] = source
        return _two_speaker_result()

    monkeypatch.setattr(shenkuo.diarize_cap, "diarize", fake_diarize)
    run.branch_diarize()
    assert seen["source"] == run.paths.audio_dir / "vocals.mp3"
    new_doc = json.loads(run.paths.speakers.read_text(encoding="utf-8"))
    assert new_doc["source"] == "vocals.mp3"


def test_branch_diarize_vocals_cache_stays(monkeypatch, tmp_path):
    """vocals 结果是终态缓存,人声轨在也不重算。"""
    run = _mk_diarize_run(tmp_path)
    doc = {"speakerCount": 2, "sentences": [], "source": "vocals.mp3"}
    run.paths.speakers.write_text(json.dumps(doc), encoding="utf-8")
    run.paths.audio_dir.mkdir()
    (run.paths.audio_dir / "vocals.mp3").write_bytes(b"x")

    def boom(source, on_progress):
        raise AssertionError("vocals 缓存不该重算")

    monkeypatch.setattr(shenkuo.diarize_cap, "diarize", boom)
    run.branch_diarize()
    assert run.entry["status"]["diarize"] == "cached"


def test_branch_diarize_rebuilds_missing_dialogue_from_cache(monkeypatch, tmp_path):
    """上次死在 dialogue 落盘前:从 speakers.json 重建对话体,不调模型。"""
    run = _mk_diarize_run(tmp_path)
    cached = shenkuo.diarize_cap.result_to_dict(_two_speaker_result())
    cached["source"] = "vocals.mp3"
    run.paths.speakers.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")

    def boom(source, on_progress):
        raise AssertionError("缓存自愈不该调模型")

    monkeypatch.setattr(shenkuo.diarize_cap, "diarize", boom)
    run.branch_diarize()
    assert run.entry["status"]["diarize"] == "cached"
    lines = run.paths.dialogue.read_text(encoding="utf-8").splitlines()
    assert lines == ["说话人A: 你怎么又迟到?", "说话人B: 堵车了。"]
    assert run.entry["dialogue"]


def test_branch_diarize_write_failure_degrades(monkeypatch, tmp_path):
    """产物落盘失败也只记 status,不把异常抛出去砸掉整趟采集。"""
    run = _mk_diarize_run(tmp_path)
    run.paths.video.write_bytes(b"x")
    monkeypatch.setattr(
        shenkuo.diarize_cap, "diarize", lambda source, on_progress: _two_speaker_result())

    def broken_write(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(shenkuo, "_write_text_atomic", broken_write)
    run.branch_diarize()
    assert run.entry["status"]["diarize"] == "error:OSError"


def test_collect_one_later_pass_keeps_speaker_count(tmp_path, monkeypatch):
    """后续非 comedy 采集趟跳过 diarize 分支,manifest 里已有的 count 不被清空。"""
    _works_env(tmp_path, monkeypatch)
    monkeypatch.delenv("NOF_DIARIZE_DOMAINS", raising=False)
    aid = "8004"
    wdir = _cached_work(shenkuo, aid)
    cached = shenkuo.diarize_cap.result_to_dict(_two_speaker_result())
    cached["source"] = "vocals.mp3"
    (wdir / "asr.speakers.json").write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        shenkuo.diarize_cap, "diarize",
        lambda source, on_progress: (_ for _ in ()).throw(AssertionError("不该触发")))
    author_dir = tmp_path / "author_k"
    author_dir.mkdir()
    shenkuo.collect_one(aid, author_dir, author_domain="finance")
    m = shenkuo.works_repo.load_manifest("douyin", aid)
    assert m["products"]["speakers"]["count"] == 2
