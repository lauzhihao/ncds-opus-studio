"""Film text revision contracts across auth, HTTP, runner and disk state."""

from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

RAW_NARRATION_ONE = "艾米丽找到史坦兹"
REVISED_NARRATION_ONE = "艾米莉找到斯坦斯。"
RAW_ORIGINAL_DIALOGUE = "Emily, find Stans."
RAW_NARRATION_TWO = "艾米莉随后发现斯坦斯的秘蜜"
REVISED_NARRATION_TWO = "艾米莉随后发现斯坦斯的秘密。"

TIMELINE_SEGMENTS = [
    {
        "id": "seg-001",
        "start_ms": 0,
        "end_ms": 2300,
        "text": RAW_NARRATION_ONE,
        "words": [],
    },
    {
        "id": "seg-002",
        "start_ms": 2300,
        "end_ms": 4100,
        "text": RAW_ORIGINAL_DIALOGUE,
        "words": [],
    },
    {
        "id": "seg-003",
        "start_ms": 4100,
        "end_ms": 6500,
        "text": RAW_NARRATION_TWO,
        "words": [],
    },
]

ENTITY_GLOSSARY = [
    {
        "canonical": "艾米莉",
        "aliases": ["艾米丽"],
        "category": "character",
        "note": "影片女主角",
    },
    {
        "canonical": "斯坦斯",
        "aliases": ["史坦兹"],
        "category": "character",
    },
]


@dataclass
class FilmContractEnvironment:
    client: TestClient
    state: Any
    headers: dict[str, str]
    jobs_root: Path
    revision_agent_calls: list[list[dict[str, Any]]]
    translation_agent_calls: list[dict[str, Any]]


@pytest.fixture()
def film_contract_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> FilmContractEnvironment:
    """Start the assembled API with isolated auth, runner and artifact stores."""
    state_root = tmp_path / "state"
    jobs_root = tmp_path / "video-jobs"
    monkeypatch.setenv("NOF_STATE_DIR", str(state_root / "tasks"))
    monkeypatch.setenv("NOF_VIDEO_JOBS_DIR", str(jobs_root))
    monkeypatch.setenv("NOF_INSTANCES_DIR", str(state_root / "instances"))
    monkeypatch.setenv("NOF_AUTH_DB", str(state_root / "auth.db"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "film-revision-contract-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "film-revision-contract-secret")
    monkeypatch.setenv(
        "AUTH_SESSION_SECRET",
        "film-revision-contract-session-secret",
    )

    from ncds_opus_factory.commands import film_localization, film_script_split
    from ncds_opus_factory.server import access as access_mod
    from ncds_opus_factory.server import app as app_mod
    from ncds_opus_factory.server import state as state_mod
    from ncds_opus_factory.server.auth import hash_session_token
    from ncds_opus_factory.server.routes import pipelines as pipelines_mod

    # state/access/route/app 在 import 时绑定单例；按依赖顺序 reload 到 tmp 根。
    state_mod = importlib.reload(state_mod)
    importlib.reload(access_mod)
    importlib.reload(pipelines_mod)
    app_mod = importlib.reload(app_mod)
    # 本契约覆盖 API → PipelineRunner → domain strategy → 磁盘边界；
    # 测试进程不启动独立 nof-worker，显式走 scheduler 的 in-process dev path。
    state_mod.PIPELINE_RUNNER.attach_task_runner(None)

    revision_agent_calls: list[list[dict[str, Any]]] = []
    translation_agent_calls: list[dict[str, Any]] = []

    def deterministic_revision_agent(
        narration_segments: list[dict[str, Any]],
        _on_progress: Any,
    ) -> dict[str, Any]:
        revision_agent_calls.append(
            [dict(segment) for segment in narration_segments]
        )
        return {
            "entity_glossary": ENTITY_GLOSSARY,
            "segments": [
                {
                    "segment_key": "film-contract-source:seg-001",
                    "corrected_text": REVISED_NARRATION_ONE,
                },
                {
                    "segment_key": "film-contract-source:seg-003",
                    "corrected_text": REVISED_NARRATION_TWO,
                },
            ],
        }

    def deterministic_translation_agent(
        segments: list[dict[str, Any]],
        target_language: str,
        entity_glossary: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        translation_agent_calls.append(
            {
                "segments": [dict(segment) for segment in segments],
                "target_language": target_language,
                "entity_glossary": [
                    dict(entry) for entry in entity_glossary
                ],
            }
        )
        return [
            {
                "segment_id": str(segment["segment_key"]),
                "translated_text": (
                    f"translated:{segment['segment_key']}"
                ),
            }
            for segment in segments
        ]

    monkeypatch.setattr(
        film_script_split,
        "FILM_TEXT_REVISION_AGENT",
        deterministic_revision_agent,
    )
    monkeypatch.setattr(
        film_localization,
        "TRANSLATION_AGENT",
        deterministic_translation_agent,
    )

    user = state_mod.AUTH_STORE.upsert_auth_user(
        provider="google",
        provider_sub="film-revision-contract-user",
        email="film-revision-contract@example.com",
        name="Film Revision Contract",
        picture_url=None,
    )
    session_value = "film-revision-contract-session"
    state_mod.AUTH_STORE.create_auth_session(
        user_id=user.id,
        session_hash=hash_session_token(session_value),
        expires_at="2099-01-01 00:00:00",
    )
    auth_headers = {"Authorization": f"Bearer {session_value}"}

    with TestClient(app_mod.app, raise_server_exceptions=False) as client:
        yield FilmContractEnvironment(
            client=client,
            state=state_mod,
            headers=auth_headers,
            jobs_root=jobs_root,
            revision_agent_calls=revision_agent_calls,
            translation_agent_calls=translation_agent_calls,
        )


def _create_film_job(env: FilmContractEnvironment) -> str:
    created = env.client.post(
        "/jobs",
        headers=env.headers,
        json={
            "pipeline_id": "final_preview",
            "title": "film revision route contract",
            "inputs": {"domain": "film", "target_language": "en"},
        },
    )
    assert created.status_code == 200, created.text
    return str(created.json()["job_id"])


def _seed_asr_timeline(env: FilmContractEnvironment, job_id: str) -> None:
    timeline_path = env.jobs_root / job_id / "01_collect" / "asr.timeline.json"
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path.write_text(
        json.dumps(
            {"version": 1, "segments": TIMELINE_SEGMENTS},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    job = env.state.PIPELINE_RUNNER._load(job_id)
    job.nodes["asr"].status = "done"
    job.nodes["asr"].outputs = {
        "collected": [
            {
                "index": 1,
                "aweme_id": "film-contract-source",
                "text": " ".join(
                    str(segment["text"]) for segment in TIMELINE_SEGMENTS
                ),
                "timeline": "01_collect/asr.timeline.json",
                "status": {"download": "ok", "transcribe": "ok"},
            }
        ],
        "timeline_path": "01_collect/asr.timeline.json",
    }
    env.state.PIPELINE_RUNNER._save(job)


def _poll_guiguzi(
    env: FilmContractEnvironment,
    job_id: str,
) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(200):
        response = env.client.get(
            f"/jobs/{job_id}/guiguzi",
            headers=env.headers,
        )
        if response.status_code == 200:
            last = response.json()
            if last.get("status") in {"done", "failed"}:
                return last
        time.sleep(0.01)
    return last


def _poll_rw_node(
    env: FilmContractEnvironment,
    job_id: str,
) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(200):
        response = env.client.get(f"/jobs/{job_id}", headers=env.headers)
        assert response.status_code == 200, response.text
        last = response.json()["nodes"]["rw"]
        if last["status"] in {"done", "failed"}:
            return last
        time.sleep(0.01)
    return last


def test_authenticated_film_flow_revises_narration_before_translation(
    film_contract_env: FilmContractEnvironment,
) -> None:
    """Guiguzi persists raw/revised text; Liuyong consumes the revised contract."""
    env = film_contract_env
    job_id = _create_film_job(env)
    _seed_asr_timeline(env, job_id)

    unauthenticated = env.client.post(
        f"/jobs/{job_id}/guiguzi/analyze",
        json={"items": []},
    )
    analyze_response = env.client.post(
        f"/jobs/{job_id}/guiguzi/analyze",
        headers=env.headers,
        json={"items": []},
    )

    assert unauthenticated.status_code == 401
    assert analyze_response.status_code == 200, analyze_response.text
    assert analyze_response.json()["status"] == "running"

    terminal = _poll_guiguzi(env, job_id)
    assert terminal["status"] == "done", terminal
    assert terminal["mode"] == "film_script_split"
    assert terminal["revision"] == {
        "status": "done",
        "corrected_count": 2,
        "narration_count": 2,
    }
    assert terminal["entity_glossary"] == ENTITY_GLOSSARY
    assert len(env.revision_agent_calls) == 1
    assert [
        segment["segment_key"]
        for segment in env.revision_agent_calls[0]
    ] == [
        "film-contract-source:seg-001",
        "film-contract-source:seg-003",
    ]

    segments = terminal["segments"]
    assert [
        {
            "segment_key": segment["segment_key"],
            "start_ms": segment["start_ms"],
            "end_ms": segment["end_ms"],
            "role": segment["role"],
        }
        for segment in segments
    ] == [
        {
            "segment_key": "film-contract-source:seg-001",
            "start_ms": 0,
            "end_ms": 2300,
            "role": "replaceable_narration",
        },
        {
            "segment_key": "film-contract-source:seg-002",
            "start_ms": 2300,
            "end_ms": 4100,
            "role": "preserved_original",
        },
        {
            "segment_key": "film-contract-source:seg-003",
            "start_ms": 4100,
            "end_ms": 6500,
            "role": "replaceable_narration",
        },
    ]
    assert [segment["source_text_raw"] for segment in segments] == [
        RAW_NARRATION_ONE,
        RAW_ORIGINAL_DIALOGUE,
        RAW_NARRATION_TWO,
    ]
    assert [segment["source_text"] for segment in segments] == [
        REVISED_NARRATION_ONE,
        RAW_ORIGINAL_DIALOGUE,
        REVISED_NARRATION_TWO,
    ]
    assert segments[1]["source_text"] == segments[1]["source_text_raw"]

    guiguzi_path = env.jobs_root / job_id / "guiguzi.json"
    persisted_guiguzi = json.loads(
        guiguzi_path.read_text(encoding="utf-8")
    )
    assert persisted_guiguzi["revision"] == terminal["revision"]
    assert persisted_guiguzi["entity_glossary"] == ENTITY_GLOSSARY
    assert persisted_guiguzi["segments"] == segments

    topics_response = env.client.post(
        f"/jobs/{job_id}/guiguzi/topics",
        headers=env.headers,
        json={
            "items": [],
            "analysis": {},
            "force": True,
        },
    )
    assert topics_response.status_code == 400, topics_response.text

    rw_response = env.client.post(
        f"/jobs/{job_id}/nodes/rw/run",
        headers=env.headers,
        json={"params": {"target_language": "en"}},
    )
    assert rw_response.status_code == 200, rw_response.text

    rw_node = _poll_rw_node(env, job_id)
    assert rw_node["status"] == "done", rw_node
    assert len(env.translation_agent_calls) == 1
    translation_call = env.translation_agent_calls[0]
    assert translation_call["target_language"] == "en"
    assert translation_call["entity_glossary"] == ENTITY_GLOSSARY
    assert [
        {
            "segment_key": segment["segment_key"],
            "source_text_raw": segment["source_text_raw"],
            "source_text": segment["source_text"],
        }
        for segment in translation_call["segments"]
    ] == [
        {
            "segment_key": "film-contract-source:seg-001",
            "source_text_raw": RAW_NARRATION_ONE,
            "source_text": REVISED_NARRATION_ONE,
        },
        {
            "segment_key": "film-contract-source:seg-003",
            "source_text_raw": RAW_NARRATION_TWO,
            "source_text": REVISED_NARRATION_TWO,
        },
    ]

    localized = rw_node["outputs"]["segments"]
    assert [
        {
            "segment_key": segment["segment_key"],
            "start_ms": segment["start_ms"],
            "end_ms": segment["end_ms"],
            "source_text_raw": segment["source_text_raw"],
            "source_text": segment["source_text"],
        }
        for segment in localized
    ] == [
        {
            "segment_key": "film-contract-source:seg-001",
            "start_ms": 0,
            "end_ms": 2300,
            "source_text_raw": RAW_NARRATION_ONE,
            "source_text": REVISED_NARRATION_ONE,
        },
        {
            "segment_key": "film-contract-source:seg-003",
            "start_ms": 4100,
            "end_ms": 6500,
            "source_text_raw": RAW_NARRATION_TWO,
            "source_text": REVISED_NARRATION_TWO,
        },
    ]
    assert all(
        segment["segment_key"] != "film-contract-source:seg-002"
        for segment in localized
    )

    localization_path = (
        env.jobs_root / job_id / "02_rw" / "film_localization.json"
    )
    persisted_localization = json.loads(
        localization_path.read_text(encoding="utf-8")
    )
    assert persisted_localization["segments"] == localized


def test_film_rw_rejects_legacy_raw_script_without_completed_revision(
    film_contract_env: FilmContractEnvironment,
) -> None:
    """A legacy raw-only Guiguzi artifact cannot silently enter translation."""
    env = film_contract_env
    job_id = _create_film_job(env)
    _seed_asr_timeline(env, job_id)
    guiguzi_path = env.jobs_root / job_id / "guiguzi.json"
    guiguzi_path.write_text(
        json.dumps(
            {
                "status": "done",
                "mode": "film_script_split",
                "segments": [
                    {
                        "id": "seg-001",
                        "source_segment_id": "seg-001",
                        "source_work_id": "film-contract-source",
                        "segment_key": "film-contract-source:seg-001",
                        "start_ms": 0,
                        "end_ms": 2300,
                        "source_text": RAW_NARRATION_ONE,
                        "language": "zh",
                        "confidence": 0.99,
                        "role": "replaceable_narration",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    unauthenticated = env.client.post(
        f"/jobs/{job_id}/nodes/rw/run",
        json={"params": {"target_language": "en"}},
    )
    response = env.client.post(
        f"/jobs/{job_id}/nodes/rw/run",
        headers=env.headers,
        json={"params": {"target_language": "en"}},
    )

    assert unauthenticated.status_code == 401
    assert response.status_code == 200, response.text
    rw_node = _poll_rw_node(env, job_id)
    assert rw_node["status"] == "failed", rw_node
    assert "revision" in str(rw_node["error"]).lower()
    assert env.translation_agent_calls == []
    assert not (
        env.jobs_root / job_id / "02_rw" / "film_localization.json"
    ).exists()


def test_deterministic_mixed_role_skips_optional_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ncds_opus_factory.commands import film_script_split

    segments = [
        {
            "segment_key": "film:mixed-dialogue",
            "source_text_raw": "go go go 啊啊",
            "source_text": "go go go 啊啊",
            "start_ms": 1000,
            "end_ms": 2000,
            "role": film_script_split.ROLE_ORIGINAL,
            "language": "en",
            "confidence": 0.72,
        }
    ]

    def unexpected_agent_call(_segments: list[dict[str, Any]]) -> list[Any]:
        raise AssertionError("deterministic mixed role must not call Agent")

    monkeypatch.setattr(
        film_script_split,
        "REVIEW_AGENT",
        unexpected_agent_call,
    )

    assert film_script_split.audit_ambiguous_segments(segments) == segments


def test_sparse_revision_output_expands_unchanged_rows_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ncds_opus_factory.commands import film_script_split

    calls: list[dict[str, Any]] = []

    def fake_call_opus(prompt: str, **kwargs: Any) -> str:
        calls.append({"prompt": prompt, **kwargs})
        if len(calls) == 1:
            return json.dumps(
                {
                    "context_summary": "一部动作电影。",
                    "entity_glossary": [],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reviewed_count": 2,
                "segments": [
                    {
                        "revision_id": 2,
                        "corrected_text": "校订后的第二句",
                    }
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(film_script_split, "is_opus_available", lambda: True)
    monkeypatch.setattr(film_script_split, "call_opus", fake_call_opus)

    progress: list[str] = []
    result = film_script_split._call_opus_film_text_revision_agent(
        [
            {
                "segment_key": "film:1",
                "source_text_raw": "第一句无需修改",
            },
            {
                "segment_key": "film:2",
                "source_text_raw": "第二句有错",
            },
        ],
        progress.append,
    )

    assert progress == [
        "提取人物术语（Agent 1/2）",
        "校订解说稿（Agent 2/2）",
    ]
    assert [call["effort"] for call in calls] == ["medium", "medium"]
    assert result["segments"] == [
        {
            "segment_key": "film:1",
            "corrected_text": "第一句无需修改",
        },
        {
            "segment_key": "film:2",
            "corrected_text": "校订后的第二句",
        },
    ]
