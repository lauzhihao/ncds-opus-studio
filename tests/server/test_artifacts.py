"""tests for server.artifacts (path->url + 产物提取) 和 routes/artifacts (审看路由)。

覆盖：
    - validated_rel / file_url / dir_url：白名单根、越界、相对路径
    - kind_of：扩展名 -> kind
    - extract_artifacts：各命令 result 形态 -> 产物清单
    - GET /artifacts/files：正常 / 404 / path-traversal 403 / 非白名单根 403 / Range 头
    - GET /artifacts/dir：列目录条目
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def art_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """临时仓库根；先设 env 再 reload 让 artifacts / 路由 / app 用到新根。"""
    monkeypatch.setenv("NOF_ARTIFACTS_ROOT", str(tmp_path))
    (tmp_path / "state").mkdir()
    (tmp_path / "video-jobs").mkdir()

    from ncds_opus_factory.server import artifacts as art_mod
    importlib.reload(art_mod)
    from ncds_opus_factory.server.routes import artifacts as art_routes
    importlib.reload(art_routes)
    from ncds_opus_factory.server import app as app_mod
    importlib.reload(app_mod)
    return tmp_path


@pytest.fixture()
def client(art_root: Path) -> TestClient:
    from ncds_opus_factory.server.app import app
    return TestClient(app)


# ───────────────────────── 单元：path -> url ─────────────────────────

def test_validated_rel_allows_state_and_videojobs(art_root: Path):
    from ncds_opus_factory.server import artifacts as a
    assert a.validated_rel(art_root / "state" / "x.md") == "state/x.md"
    assert a.validated_rel(art_root / "video-jobs" / "j" / "a.mp3") == "video-jobs/j/a.mp3"


def test_validated_rel_rejects_other_roots_and_escape(art_root: Path):
    from ncds_opus_factory.server import artifacts as a
    # src/.env 等非白名单根 -> None（防泄露源码/密钥）
    assert a.validated_rel(art_root / "src" / "secret.py") is None
    assert a.validated_rel(art_root / ".env") is None
    # 越界到仓库外 -> None
    assert a.validated_rel(art_root.parent / "outside.txt") is None


def test_kind_of():
    from ncds_opus_factory.server import artifacts as a
    assert a.kind_of("x.md") == "script"
    assert a.kind_of("x.MP3") == "audio"
    assert a.kind_of("x.mp4") == "video"
    assert a.kind_of("x.png") == "image"
    assert a.kind_of("x.json") == "data"
    assert a.kind_of("x.bin") == "file"


# ───────────────────────── 单元：extract_artifacts ─────────────────────────

def test_extract_liuyong(art_root: Path):
    from ncds_opus_factory.server import artifacts as a
    script = art_root / "video-jobs" / "OGV_1" / "deliverables" / "rewrite" / "draft.md"
    result = {
        "deliverables_dir": str(art_root / "video-jobs" / "OGV_1" / "deliverables"),
        "drafts": [{"model": "gpt5", "path": str(script), "text": "..."}],
    }
    arts = a.extract_artifacts("liuyong", result)
    urls = {x["url"] for x in arts}
    assert "/artifacts/files/video-jobs/OGV_1/deliverables/rewrite/draft.md" in urls
    assert "/artifacts/files/video-jobs/OGV_1/deliverables/rewrite/draft.qc.json" in urls
    assert any(x["kind"] == "dir" for x in arts)


def test_extract_boya_and_guiguzi(art_root: Path):
    from ncds_opus_factory.server import artifacts as a
    job = art_root / "video-jobs" / "job1"
    boya = a.extract_artifacts("boya", {"job": str(job), "master": "master.mp3"})
    assert {"/artifacts/files/video-jobs/job1/master.mp3",
            "/artifacts/files/video-jobs/job1/audio_plan.json"} <= {x["url"] for x in boya}
    assert any(x["kind"] == "audio" for x in boya)

    topics = art_root / "state" / "benchmark" / "topics" / "topics.json"
    g = a.extract_artifacts("guiguzi", {"out": str(topics), "topics": []})
    assert g[0]["url"] == "/artifacts/files/state/benchmark/topics/topics.json"


def test_extract_drops_out_of_root(art_root: Path):
    from ncds_opus_factory.server import artifacts as a
    # 产物落在白名单外 -> 静默丢弃，不产 URL
    assert a.extract_artifacts("guiguzi", {"out": "/etc/passwd"}) == []


# ───────────────────────── 路由 ─────────────────────────

def test_serve_file_ok(client: TestClient, art_root: Path):
    f = art_root / "state" / "note.md"
    f.write_text("# hi", encoding="utf-8")
    resp = client.get("/artifacts/files/state/note.md")
    assert resp.status_code == 200
    assert resp.text == "# hi"


def test_serve_media_has_range_support(client: TestClient, art_root: Path):
    mp3 = art_root / "video-jobs" / "j" / "master.mp3"
    mp3.parent.mkdir(parents=True)
    mp3.write_bytes(b"\x00\x01\x02\x03fakeaudio")
    resp = client.get("/artifacts/files/video-jobs/j/master.mp3")
    assert resp.status_code == 200
    # FileResponse 声明 Accept-Ranges: bytes -> iPad 可拖动音视频进度
    assert resp.headers.get("accept-ranges") == "bytes"


def test_serve_404_missing(client: TestClient):
    assert client.get("/artifacts/files/state/nope.md").status_code == 404


def test_serve_403_non_allowed_root(client: TestClient, art_root: Path):
    # 即使文件真实存在，非白名单根也拒绝（防读 src/.env）
    (art_root / "src").mkdir(exist_ok=True)
    (art_root / "src" / "secret.py").write_text("KEY=1", encoding="utf-8")
    assert client.get("/artifacts/files/src/secret.py").status_code == 403


def test_serve_traversal_blocked(client: TestClient, art_root: Path):
    (art_root / "outside.txt").write_text("nope", encoding="utf-8")
    resp = client.get("/artifacts/files/state/../../outside.txt")
    assert resp.status_code in (403, 404)


def test_dir_listing(client: TestClient, art_root: Path):
    d = art_root / "state" / "figure_collected" / "x"
    d.mkdir(parents=True)
    (d / "a.png").write_bytes(b"x")
    (d / "sub").mkdir()
    resp = client.get("/artifacts/dir/state/figure_collected/x")
    assert resp.status_code == 200
    names = {e["name"]: e for e in resp.json()["entries"]}
    assert names["sub"]["is_dir"] is True
    assert names["a.png"]["kind"] == "image"
    assert names["a.png"]["url"] == "/artifacts/files/state/figure_collected/x/a.png"
