"""Film Guiguzi contract across auth, HTTP routes, runner and disk state."""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_authenticated_film_guiguzi_routes_fallback_to_asr_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空评论请求走 ASR 原文，并经真实 route/runner 落 guiguzi.json。"""
    state_root = tmp_path / "state"
    jobs_root = tmp_path / "video-jobs"
    monkeypatch.setenv("NOF_STATE_DIR", str(state_root / "tasks"))
    monkeypatch.setenv("NOF_VIDEO_JOBS_DIR", str(jobs_root))
    monkeypatch.setenv("NOF_INSTANCES_DIR", str(state_root / "instances"))
    monkeypatch.setenv("NOF_AUTH_DB", str(state_root / "auth.db"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "contract-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "contract-secret")
    monkeypatch.setenv("AUTH_SESSION_SECRET", "contract-session-secret")

    from ncds_opus_factory.server import access as access_mod
    from ncds_opus_factory.server import app as app_mod
    from ncds_opus_factory.server import mock as mock_mod
    from ncds_opus_factory.server import state as state_mod
    from ncds_opus_factory.server.auth import hash_session_token
    from ncds_opus_factory.server.routes import pipelines as pipelines_mod

    # state/access/route/app 在 import 时绑定单例；按依赖顺序 reload 到 tmp 根。
    state_mod = importlib.reload(state_mod)
    importlib.reload(access_mod)
    importlib.reload(pipelines_mod)
    app_mod = importlib.reload(app_mod)

    # 唯一模型 seam：使用生产内置 mock performer，但取消展示延迟。
    monkeypatch.setattr(mock_mod, "mock_delay_seconds", lambda _kind=None: 0.0)

    user = state_mod.AUTH_STORE.upsert_auth_user(
        provider="google",
        provider_sub="film-contract-user",
        email="film-contract@example.com",
        name="Film Contract",
        picture_url=None,
    )
    session_value = "film-contract-session"
    state_mod.AUTH_STORE.create_auth_session(
        user_id=user.id,
        session_hash=hash_session_token(session_value),
        expires_at="2099-01-01 00:00:00",
    )
    auth_headers = {"Authorization": f"Bearer {session_value}"}

    with TestClient(app_mod.app, raise_server_exceptions=False) as client:
        created = client.post(
            "/jobs",
            headers=auth_headers,
            json={
                "pipeline_id": "final_preview",
                "title": "film route contract",
                "inputs": {"domain": "film"},
            },
        )
        assert created.status_code == 200, created.text
        job_id = created.json()["job_id"]

        # 模拟沈括已经完成：无评论/无 audio，仅持久化原始转写文案。
        job = state_mod.PIPELINE_RUNNER._load(job_id)
        job.mock = True
        job.nodes["asr"].status = "done"
        job.nodes["asr"].outputs = {
            "collected": [
                {
                    "index": 1,
                    "aweme_id": "film-source-1",
                    "text": "沈括采集的影视解说原稿",
                    "status": {"download": "ok", "transcribe": "ok"},
                }
            ]
        }
        state_mod.PIPELINE_RUNNER._save(job)

        unauthenticated = client.post(
            f"/jobs/{job_id}/guiguzi/analyze",
            json={"items": []},
        )
        analyze_response = client.post(
            f"/jobs/{job_id}/guiguzi/analyze",
            headers=auth_headers,
            json={"items": []},
        )

        assert unauthenticated.status_code == 401
        assert analyze_response.status_code == 200, analyze_response.text
        analyze_doc = analyze_response.json()
        assert analyze_doc["status"] == "running"
        assert analyze_doc["items"] == [
            {"text": "沈括采集的影视解说原稿", "comment": ""}
        ]

        guiguzi_path = jobs_root / job_id / "guiguzi.json"
        analyzed: dict[str, object] = {}
        for _ in range(100):
            polled = client.get(
                f"/jobs/{job_id}/guiguzi",
                headers=auth_headers,
            )
            if polled.status_code == 200:
                analyzed = polled.json()
                if analyzed.get("status") in {"analyzed", "failed"}:
                    break
            time.sleep(0.01)

        assert analyzed["status"] == "analyzed"
        assert guiguzi_path.is_file()
        persisted = json.loads(guiguzi_path.read_text(encoding="utf-8"))
        assert persisted["items"] == analyze_doc["items"]

        topics_response = client.post(
            f"/jobs/{job_id}/guiguzi/topics",
            headers=auth_headers,
            json={
                "items": [],
                "analysis": analyzed["analysis"],
                "force": True,
            },
        )

        # 合法 body 必须进入 handler，不能因 Request/ForwardRef 绑定错误返回 500。
        assert topics_response.status_code == 200, topics_response.text
        topics_doc = topics_response.json()
        assert topics_doc["status"] == "running"
        assert topics_doc["items"] == analyze_doc["items"]

        terminal: dict[str, object] = {}
        for _ in range(100):
            polled = client.get(
                f"/jobs/{job_id}/guiguzi",
                headers=auth_headers,
            )
            if polled.status_code == 200:
                terminal = polled.json()
                if terminal.get("status") in {"done", "failed"}:
                    break
            time.sleep(0.01)

        assert terminal["status"] == "done"
        persisted = json.loads(guiguzi_path.read_text(encoding="utf-8"))
        assert persisted["status"] == "done"
        assert persisted["items"] == analyze_doc["items"]
