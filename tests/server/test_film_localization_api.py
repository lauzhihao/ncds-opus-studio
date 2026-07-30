"""film_localization 的真实 HTTP 上传契约。

这些用例通过 FastAPI app、/jobs 路由与磁盘 JobStore 走完整边界；不调用路由
函数或上传辅助函数。媒体分析与渲染不在本 API slice 的范围内。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ncds_opus_factory.server.pipeline_runner import PipelineRunner


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """将实际 /jobs 路由接到临时 JobStore，避免读写开发机作品目录。"""
    from ncds_opus_factory.server import access
    from ncds_opus_factory.server.app import app
    from ncds_opus_factory.server.routes import pipelines

    runner = PipelineRunner(tmp_path / "video-jobs")
    monkeypatch.setattr(pipelines, "PIPELINE_RUNNER", runner)
    monkeypatch.setattr(access, "PIPELINE_RUNNER", runner)
    return TestClient(app)


@pytest.fixture()
def mp4_bytes() -> bytes:
    """极小 MP4 header fixture；上传 API 只负责持久化，尚不执行媒体分析。"""
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


def _create_film_job(client: TestClient) -> str:
    registered = client.get("/pipelines/film_localization")
    assert registered.status_code == 200, registered.text

    response = client.post(
        "/jobs",
        json={"pipeline_id": "film_localization", "title": "授权影视本地化"},
    )
    assert response.status_code == 200, response.text
    return response.json()["job_id"]


def test_film_localization_link_source_creates_import_ready_job(client: TestClient) -> None:
    response = client.post(
        "/jobs",
        json={
            "pipeline_id": "film_localization",
            "title": "链接原片本地化",
            "inputs": {
                "profile": "film",
                "source_language": "zh",
                "target_language": "en",
                "rights_confirmed": True,
                "source_ref": {
                    "platform": "douyin",
                    "work_id": "7650894465690766623",
                    "source_url": "https://www.douyin.com/video/7650894465690766623",
                    "title": "授权原片",
                },
            },
        },
    )

    assert response.status_code == 200, response.text
    job = response.json()
    assert job["nodes"]["input"]["status"] == "done"
    assert job["nodes"]["import"]["status"] == "idle"
    assert job["nodes"]["analyze"]["status"] == "idle"
    assert job["inputs"]["source_ref"]["source_url"].startswith("https://www.douyin.com/")


def test_film_localization_source_upload_persists_authorized_mp4(
    client: TestClient,
    mp4_bytes: bytes,
) -> None:
    job_id = _create_film_job(client)

    response = client.post(
        f"/jobs/{job_id}/source",
        data={"rights_confirmed": "true"},
        files={"source": ("licensed-source.mp4", mp4_bytes, "video/mp4")},
    )

    assert response.status_code == 200, response.text
    stored = response.json()
    assert stored["source"]["filename"] == "licensed-source.mp4"
    assert stored["source"]["path"] == "00_source/source.mp4"
    assert stored["source"]["size_bytes"] == len(mp4_bytes)
    assert stored["source"]["rights_confirmed"] is True

    job = client.get(f"/jobs/{job_id}")
    assert job.status_code == 200, job.text
    assert job.json()["inputs"]["source"] == stored["source"]

    # The API has persisted the raw media in the job's conventional source dir.
    # This artifact assertion verifies the JobStore persistence result, while the
    # request itself has gone through the real FastAPI route.
    from ncds_opus_factory.server import access

    assert (
        access.PIPELINE_RUNNER.video_jobs_dir
        / job_id
        / "00_source"
        / "source.mp4"
    ).read_bytes() == mp4_bytes


@pytest.mark.parametrize("rights_confirmed", [None, "false"])
def test_film_source_requires_explicit_rights_confirmation(
    client: TestClient,
    mp4_bytes: bytes,
    rights_confirmed: str | None,
) -> None:
    job_id = _create_film_job(client)
    data = {} if rights_confirmed is None else {"rights_confirmed": rights_confirmed}

    response = client.post(
        f"/jobs/{job_id}/source",
        data=data,
        files={"source": ("licensed-source.mp4", mp4_bytes, "video/mp4")},
    )

    assert 400 <= response.status_code < 500


def test_film_source_rejects_unsupported_extension(client: TestClient) -> None:
    job_id = _create_film_job(client)

    response = client.post(
        f"/jobs/{job_id}/source",
        data={"rights_confirmed": "true"},
        files={"source": ("licensed-source.avi", b"not an accepted container", "video/x-msvideo")},
    )

    assert 400 <= response.status_code < 500


def test_film_source_rejects_file_over_configured_limit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOF_FILM_SOURCE_MAX_BYTES", "8")
    job_id = _create_film_job(client)

    response = client.post(
        f"/jobs/{job_id}/source",
        data={"rights_confirmed": "true"},
        files={"source": ("licensed-source.mp4", b"more-than-eight-bytes", "video/mp4")},
    )

    assert 400 <= response.status_code < 500
