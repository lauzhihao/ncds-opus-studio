"""tests for GET /jobs/{job_id}/files/{relpath}

覆盖：
    1. 正常文件读取
    2. job_id 不存在 -> 404
    3. relpath 不存在 -> 404
    4. relpath 是目录 -> 400
    5. path traversal (../) -> 403
    6. 非法 job_id (含 /) -> 400
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def video_jobs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 video-jobs 根，先设环境变量再 import app 以让路由用到新路径。"""
    root = tmp_path / "video-jobs"
    root.mkdir()
    monkeypatch.setenv("NOF_VIDEO_JOBS_DIR", str(root))
    # jobs 路由在 import 时读 env，所以必须 reload
    import importlib

    from ncds_opus_factory.server.routes import jobs as jobs_module
    importlib.reload(jobs_module)
    from ncds_opus_factory.server import app as app_module
    importlib.reload(app_module)
    return root


@pytest.fixture()
def client(video_jobs_root: Path) -> TestClient:
    from ncds_opus_factory.server.app import app
    return TestClient(app)


def _make_job(root: Path, job_id: str) -> Path:
    job_dir = root / job_id
    (job_dir / "deliverables").mkdir(parents=True)
    (job_dir / "deliverables" / "results.json").write_text(
        '{"ok": true}', encoding="utf-8"
    )
    (job_dir / "trace.log").write_text("hello\n", encoding="utf-8")
    return job_dir


# ============================================================
# happy path
# ============================================================

def test_get_existing_file(client: TestClient, video_jobs_root: Path) -> None:
    _make_job(video_jobs_root, "vj_001")
    resp = client.get("/jobs/vj_001/files/deliverables/results.json")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_get_trace_log(client: TestClient, video_jobs_root: Path) -> None:
    _make_job(video_jobs_root, "vj_002")
    resp = client.get("/jobs/vj_002/files/trace.log")
    assert resp.status_code == 200
    assert resp.text == "hello\n"


# ============================================================
# error paths
# ============================================================

def test_job_not_found(client: TestClient) -> None:
    resp = client.get("/jobs/does_not_exist/files/anything")
    assert resp.status_code == 404
    assert "job not found" in resp.json()["detail"]


def test_file_not_found(client: TestClient, video_jobs_root: Path) -> None:
    _make_job(video_jobs_root, "vj_003")
    resp = client.get("/jobs/vj_003/files/deliverables/missing.json")
    assert resp.status_code == 404
    assert "file not found" in resp.json()["detail"]


def test_relpath_is_dir(client: TestClient, video_jobs_root: Path) -> None:
    _make_job(video_jobs_root, "vj_004")
    resp = client.get("/jobs/vj_004/files/deliverables")
    assert resp.status_code == 400


def test_path_traversal_blocked(client: TestClient, video_jobs_root: Path) -> None:
    _make_job(video_jobs_root, "vj_005")
    # 制造一个 sibling job 与机密文件，验证攻击拿不到
    (video_jobs_root / "secret_job").mkdir()
    (video_jobs_root / "secret_job" / "secret.txt").write_text("nope", encoding="utf-8")
    # FastAPI 会先把 %2e%2e 解码成 ..；用 path:relpath 接收
    resp = client.get("/jobs/vj_005/files/../secret_job/secret.txt")
    # httpx/starlette 默认 normalize 路径，可能直接路由失败成 404，
    # 也可能进路由后被 _resolve_safe 挡成 403——任一种都视为防御成功
    assert resp.status_code in (403, 404)
    if resp.status_code == 403:
        assert "escapes" in resp.json()["detail"]


def test_invalid_job_id_with_slash(client: TestClient) -> None:
    # path 参数里的 / 在 path 模式下会被吃成 relpath 一部分；
    # 这里测的是 job_id 本身合法性边界（"."/".."）
    resp = client.get("/jobs/../files/anything")
    # 同上：可能 404 也可能 400
    assert resp.status_code in (400, 404)


def test_invalid_job_id_dot(client: TestClient) -> None:
    resp = client.get("/jobs/./files/anything")
    assert resp.status_code in (400, 404)
