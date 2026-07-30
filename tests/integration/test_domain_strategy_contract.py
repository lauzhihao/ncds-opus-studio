"""Domain strategy contracts across API, auth, runners and file artifacts."""

from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

TIMELINE_SEGMENTS = [
    {
        "id": "seg-001",
        "start_ms": 0,
        "end_ms": 3200,
        "text": "最后一刻，侦探终于看穿了真相。",
        "words": [],
    },
    {
        "id": "seg-002",
        "start_ms": 3200,
        "end_ms": 5100,
        "text": "Don't move. I know who you are.",
        "words": [],
    },
]

CLASSIFIED_SEGMENTS = [
    {
        "id": "seg-001",
        "source_segment_id": "seg-001",
        "source_work_id": "film-source-1",
        "segment_key": "film-source-1:seg-001",
        "start_ms": 0,
        "end_ms": 3200,
        "source_text_raw": TIMELINE_SEGMENTS[0]["text"],
        "source_text": TIMELINE_SEGMENTS[0]["text"],
        "language": "zh",
        "confidence": 0.98,
        "role": "replaceable_narration",
    },
    {
        "id": "seg-002",
        "source_segment_id": "seg-002",
        "source_work_id": "film-source-1",
        "segment_key": "film-source-1:seg-002",
        "start_ms": 3200,
        "end_ms": 5100,
        "source_text_raw": TIMELINE_SEGMENTS[1]["text"],
        "source_text": TIMELINE_SEGMENTS[1]["text"],
        "language": "en",
        "confidence": 0.96,
        "role": "preserved_original",
    },
]

FILM_ENTITY_GLOSSARY: list[dict[str, Any]] = []
FILM_REVISION = {
    "status": "done",
    "corrected_count": 0,
    "narration_count": 1,
}

TRANSLATED_NARRATION = "At the final moment, the detective finally saw the truth."

LINES_RESPONSE = {
    "meta": {"title": "影视契约", "subtitle": "", "tags": ["film"]},
    "beats": [
        {
            "zh": TRANSLATED_NARRATION,
            "en": "",
            "chapter": 1,
        }
    ],
}

STORYBOARD_RESPONSE = {
    "visual": {
        "style": "paper_card_talk",
        "stage": {
            "background": {
                "prompt": "cinematic paper stage without text",
                "imageFit": "cover",
            },
            "palette": {},
            "shotRhythm": "one-shot-per-beat",
        },
        "shots": [
            {
                "beatIndex": 1,
                "shotId": "b001",
                "group": "g1",
                "intent": "detective reveals the truth",
                "layout": "center_icon",
                "transition": "replace",
                "motion": {"enter": "fade"},
                "emphasis": [],
                "assets": [
                    {
                        "id": "detective",
                        "prompt": "detective silhouette",
                        "pos": {"x": 50, "y": 50},
                        "size": 30,
                    }
                ],
            }
        ],
    }
}


@dataclass
class ContractEnvironment:
    client: TestClient
    state: Any
    headers: dict[str, str]
    jobs_root: Path
    external_llm_prompts: list[str]
    translation_agent_calls: list[dict[str, Any]]


@pytest.fixture()
def contract_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ContractEnvironment:
    state_root = tmp_path / "state"
    jobs_root = tmp_path / "video-jobs"
    monkeypatch.setenv("NOF_STATE_DIR", str(state_root / "tasks"))
    monkeypatch.setenv("NOF_VIDEO_JOBS_DIR", str(jobs_root))
    monkeypatch.setenv("NOF_INSTANCES_DIR", str(state_root / "instances"))
    monkeypatch.setenv("NOF_AUTH_DB", str(state_root / "auth.db"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "strategy-contract-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "strategy-contract-secret")
    monkeypatch.setenv("AUTH_SESSION_SECRET", "strategy-contract-session-secret")

    from ncds_opus_factory.commands import (
        film_localization,
        film_script_split,
        guiguzi,
    )
    from ncds_opus_factory.common import capabilities
    from ncds_opus_factory.server import access as access_mod
    from ncds_opus_factory.server import app as app_mod
    from ncds_opus_factory.server import pipeline_rw_helpers as rw_helpers
    from ncds_opus_factory.server import state as state_mod
    from ncds_opus_factory.server.auth import hash_session_token
    from ncds_opus_factory.server.engine import pipeline_performers_final as performers
    from ncds_opus_factory.server.routes import instances as instances_mod
    from ncds_opus_factory.server.routes import pipelines as pipelines_mod

    state_mod = importlib.reload(state_mod)
    importlib.reload(access_mod)
    importlib.reload(instances_mod)
    importlib.reload(pipelines_mod)
    app_mod = importlib.reload(app_mod)
    state_mod.PIPELINE_RUNNER._task_runner = None

    external_llm_prompts: list[str] = []
    translation_agent_calls: list[dict[str, Any]] = []

    def deterministic_llm_caller(prompt: str) -> str:
        external_llm_prompts.append(prompt)
        if '"hook_reason"' in prompt:
            return json.dumps(
                {
                    "hook_reason": "contract hook",
                    "audience": "contract audience",
                    "hooks": ["contract hook"],
                    "direction": "contract direction",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            [
                {
                    "index": 1,
                    "title": "默认领域仍可生成选题",
                    "angle": "兼容合同",
                    "why": "未知领域回退 default",
                    "potential": 90,
                }
            ],
            ensure_ascii=False,
        )

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
                "segment_id": str(
                    segment.get("segment_key") or segment.get("id") or ""
                ),
                "translated_text": TRANSLATED_NARRATION,
            }
            for segment in segments
        ]

    def deterministic_revision_agent(
        narration_segments: list[dict[str, Any]],
        _on_progress: Any,
    ) -> dict[str, Any]:
        return {
            "entity_glossary": FILM_ENTITY_GLOSSARY,
            "segments": [
                {
                    "segment_key": segment["segment_key"],
                    "corrected_text": segment["source_text_raw"],
                }
                for segment in narration_segments
            ],
        }

    def offline_detail(aweme_id: str) -> dict[str, Any]:
        return {
            "aweme_id": aweme_id,
            "desc": "film source",
            "author": {"nickname": "contract"},
            "statistics": {},
            "video": {},
        }

    def offline_download(
        _aweme_id: str,
        output_path: Path,
        **_kwargs: Any,
    ) -> None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"contract-video")

    def offline_transcribe(
        _video_path: Path,
        _on_progress: Any,
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], str]:
        utterances = [
            {
                "transcript": segment["source_text"],
                "start_time": segment["start_ms"],
                "end_time": segment["end_ms"],
                "confidence": segment["confidence"],
                "language": segment["language"],
            }
            for segment in CLASSIFIED_SEGMENTS
        ]
        return {
            "backend": "bcut",
            "rawResponse": {"utterances": utterances},
        }, " ".join(segment["text"] for segment in TIMELINE_SEGMENTS)

    monkeypatch.setattr(
        guiguzi,
        "_callers",
        lambda _timeout_seconds: {"opus": deterministic_llm_caller},
    )
    monkeypatch.setattr(
        rw_helpers,
        "MODEL_CANDIDATES",
        [{"id": "contract", "label": "Contract translator"}],
    )
    monkeypatch.setattr(
        film_localization,
        "TRANSLATION_AGENT",
        deterministic_translation_agent,
    )
    monkeypatch.setattr(
        film_script_split,
        "FILM_TEXT_REVISION_AGENT",
        deterministic_revision_agent,
    )
    monkeypatch.setattr(rw_helpers, "_apply_rw_qc", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(performers, "_fetch_one_video_detail", offline_detail)
    monkeypatch.setattr(capabilities, "fetch_and_download", offline_download)
    monkeypatch.setattr(capabilities, "transcribe", offline_transcribe)
    monkeypatch.setattr(
        capabilities,
        "clean_transcript",
        lambda raw, _on_progress: raw,
    )

    user = state_mod.AUTH_STORE.upsert_auth_user(
        provider="google",
        provider_sub="domain-strategy-contract",
        email="strategy-contract@example.com",
        name="Strategy Contract",
        picture_url=None,
    )
    session_value = "domain-strategy-contract-session"
    state_mod.AUTH_STORE.create_auth_session(
        user_id=user.id,
        session_hash=hash_session_token(session_value),
        expires_at="2099-01-01 00:00:00",
    )
    headers = {"Authorization": f"Bearer {session_value}"}

    with TestClient(app_mod.app, raise_server_exceptions=False) as client:
        yield ContractEnvironment(
            client=client,
            state=state_mod,
            headers=headers,
            jobs_root=jobs_root,
            external_llm_prompts=external_llm_prompts,
            translation_agent_calls=translation_agent_calls,
        )


def _create_job(
    env: ContractEnvironment,
    domain: str,
    *,
    target_language: str | None = None,
) -> str:
    inputs: dict[str, Any] = {"domain": domain}
    if target_language is not None:
        inputs["target_language"] = target_language
    response = env.client.post(
        "/jobs",
        headers=env.headers,
        json={
            "pipeline_id": "final_preview",
            "title": f"{domain} strategy contract",
            "inputs": inputs,
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["job_id"])


def _seed_film_source(env: ContractEnvironment, job_id: str) -> Path:
    timeline_path = env.jobs_root / job_id / "01_collect" / "asr.timeline.json"
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path.write_text(
        json.dumps({"version": 1, "segments": TIMELINE_SEGMENTS}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    job = env.state.PIPELINE_RUNNER._load(job_id)
    job.nodes["asr"].status = "done"
    job.nodes["asr"].outputs = {
        "collected": [
            {
                "index": 1,
                "aweme_id": "film-source-1",
                "text": " ".join(segment["text"] for segment in TIMELINE_SEGMENTS),
                "timeline": "01_collect/asr.timeline.json",
                "status": {"download": "ok", "transcribe": "ok"},
            }
        ],
        "timeline_path": "01_collect/asr.timeline.json",
    }
    env.state.PIPELINE_RUNNER._save(job)
    return timeline_path


def _poll_guiguzi(
    env: ContractEnvironment,
    job_id: str,
    terminal_statuses: set[str],
) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(100):
        response = env.client.get(
            f"/jobs/{job_id}/guiguzi",
            headers=env.headers,
        )
        if response.status_code == 200:
            last = response.json()
            if last.get("status") in terminal_statuses:
                return last
        time.sleep(0.01)
    return last


def _poll_node(
    env: ContractEnvironment,
    job_id: str,
    node_name: str,
) -> dict[str, Any]:
    node: dict[str, Any] = {}
    for _ in range(100):
        response = env.client.get(f"/jobs/{job_id}", headers=env.headers)
        assert response.status_code == 200, response.text
        node = response.json()["nodes"][node_name]
        if node["status"] in {"done", "failed"}:
            return node
        time.sleep(0.01)
    return node


def _run_instance_step(
    env: ContractEnvironment,
    instance_id: str,
    step_id: str,
    step_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = env.client.post(
        f"/instances/{instance_id}/steps/{step_id}/run",
        headers=env.headers,
        json={"step_inputs": step_inputs or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approve_instance_step(
    env: ContractEnvironment,
    instance_id: str,
    step_id: str,
) -> dict[str, Any]:
    response = env.client.post(
        f"/instances/{instance_id}/steps/{step_id}/approve",
        headers=env.headers,
        json={"decision": "approved", "reviewer": "system"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _assert_real_normalized_timeline(timeline: dict[str, Any]) -> None:
    """Raw backend utterances have no ids; the real normalizer must add them."""
    segments = timeline["segments"]
    assert [segment["id"] for segment in segments] == [
        "seg_0001",
        "seg_0002",
    ]
    assert [
        {
            "start_ms": segment["start_ms"],
            "end_ms": segment["end_ms"],
            "text": segment["text"],
            "words": segment["words"],
        }
        for segment in segments
    ] == [
        {
            "start_ms": segment["start_ms"],
            "end_ms": segment["end_ms"],
            "text": segment["text"],
            "words": segment["words"],
        }
        for segment in TIMELINE_SEGMENTS
    ]


def test_film_analyze_uses_timeline_and_persists_ordered_script_split(
    contract_env: ContractEnvironment,
) -> None:
    env = contract_env
    job_id = _create_job(env, "film", target_language="en")
    timeline_path = _seed_film_source(env, job_id)

    response = env.client.post(
        f"/jobs/{job_id}/guiguzi/analyze",
        headers=env.headers,
        json={"items": []},
    )
    assert response.status_code == 200, response.text

    terminal = _poll_guiguzi(env, job_id, {"done", "analyzed", "failed"})
    assert env.external_llm_prompts == []
    assert terminal["status"] == "done"
    assert terminal["mode"] == "film_script_split"
    segments = terminal["segments"]
    assert [segment["id"] for segment in segments] == ["seg-001", "seg-002"]
    assert [segment["start_ms"] for segment in segments] == [0, 3200]
    assert [segment["end_ms"] for segment in segments] == [3200, 5100]
    assert [segment["role"] for segment in segments] == [
        "replaceable_narration",
        "preserved_original",
    ]
    assert segments[0]["language"] == "zh"
    assert segments[1]["language"] == "en"
    assert [segment["source_text"] for segment in segments] == [
        segment["text"] for segment in TIMELINE_SEGMENTS
    ]
    assert all(0 <= segment["confidence"] <= 1 for segment in segments)

    assert timeline_path.is_file()

    persisted = json.loads(
        (env.jobs_root / job_id / "guiguzi.json").read_text(encoding="utf-8")
    )
    assert persisted["mode"] == "film_script_split"
    assert persisted["segments"] == segments


def test_film_topics_route_is_rejected(
    contract_env: ContractEnvironment,
) -> None:
    env = contract_env
    job_id = _create_job(env, "film")
    _seed_film_source(env, job_id)

    response = env.client.post(
        f"/jobs/{job_id}/guiguzi/topics",
        headers=env.headers,
        json={"items": [], "analysis": {}, "force": True},
    )
    assert response.status_code == 400, response.text


def test_film_rw_node_persists_window_preserving_localization(
    contract_env: ContractEnvironment,
) -> None:
    env = contract_env
    job_id = _create_job(env, "film", target_language="en")
    _seed_film_source(env, job_id)
    guiguzi_path = env.jobs_root / job_id / "guiguzi.json"
    guiguzi_path.write_text(
        json.dumps(
            {
                "status": "done",
                "mode": "film_script_split",
                "revision": FILM_REVISION,
                "entity_glossary": FILM_ENTITY_GLOSSARY,
                "segments": CLASSIFIED_SEGMENTS,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    response = env.client.post(
        f"/jobs/{job_id}/nodes/rw/run",
        headers=env.headers,
        json={"params": {"target_language": "en"}},
    )
    assert response.status_code == 200, response.text

    rw_node = _poll_node(env, job_id, "rw")
    assert rw_node["status"] == "done", rw_node
    outputs = rw_node["outputs"]
    assert outputs["mode"] == "film_localization"
    assert outputs["target_language"] == "en"
    assert outputs["entity_glossary"] == FILM_ENTITY_GLOSSARY
    assert outputs["source_revision"] == FILM_REVISION
    translated = outputs["segments"]
    assert len(translated) == 1
    assert {
        "segment_id",
        "start_ms",
        "end_ms",
        "source_text_raw",
        "source_text",
        "translated_text",
    } <= set(translated[0])
    assert translated[0]["segment_id"] == "seg-001"
    assert translated[0]["start_ms"] == TIMELINE_SEGMENTS[0]["start_ms"]
    assert translated[0]["end_ms"] == TIMELINE_SEGMENTS[0]["end_ms"]
    assert translated[0]["source_text_raw"] == TIMELINE_SEGMENTS[0]["text"]
    assert translated[0]["source_text"] == TIMELINE_SEGMENTS[0]["text"]
    assert translated[0]["translated_text"] == TRANSLATED_NARRATION
    assert all(segment["segment_id"] != "seg-002" for segment in translated)
    assert len(env.translation_agent_calls) == 1
    assert env.translation_agent_calls[0]["target_language"] == "en"
    assert env.translation_agent_calls[0]["entity_glossary"] == FILM_ENTITY_GLOSSARY
    assert [
        segment["id"]
        for segment in env.translation_agent_calls[0]["segments"]
    ] == ["seg-001"]

    artifact_path = env.jobs_root / job_id / "02_rw" / "film_localization.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["target_language"] == "en"
    assert artifact["segments"] == translated


def test_unknown_domain_keeps_default_guiguzi_two_stage_flow(
    contract_env: ContractEnvironment,
) -> None:
    env = contract_env
    job_id = _create_job(env, "future-unregistered-domain")
    items = [{"text": "默认原文", "comment": "默认评论"}]

    analyze = env.client.post(
        f"/jobs/{job_id}/guiguzi/analyze",
        headers=env.headers,
        json={"items": items},
    )
    assert analyze.status_code == 200, analyze.text
    analyzed = _poll_guiguzi(env, job_id, {"analyzed", "failed"})
    assert analyzed["status"] == "analyzed"
    assert analyzed.get("mode") != "film_script_split"

    topics = env.client.post(
        f"/jobs/{job_id}/guiguzi/topics",
        headers=env.headers,
        json={
            "items": items,
            "analysis": analyzed["analysis"],
            "force": True,
        },
    )
    assert topics.status_code == 200, topics.text
    done = _poll_guiguzi(env, job_id, {"done", "failed"})
    assert done["status"] == "done"
    assert done["topics"][0]["title"] == "默认领域仍可生成选题"


def test_instances_film_asr_uses_shared_strategy_and_persists_timeline(
    contract_env: ContractEnvironment,
    tmp_path: Path,
) -> None:
    env = contract_env
    created = env.client.post(
        "/instances",
        headers=env.headers,
        json={
            "recipe_id": "final_preview",
            "inputs": {"domain": "film"},
            "title": "direct instance film",
        },
    )
    assert created.status_code == 201, created.text
    instance_id = created.json()["instance_id"]
    job_dir = tmp_path / "direct-instance-job"

    started = env.client.post(
        f"/instances/{instance_id}/steps/input/run",
        headers=env.headers,
        json={},
    )
    assert started.status_code == 200, started.text
    asr = env.client.post(
        f"/instances/{instance_id}/steps/asr/run",
        headers=env.headers,
        json={
            "step_inputs": {
                "job_dir": str(job_dir),
                "urls": ["7312345678901234567"],
            }
        },
    )
    assert asr.status_code == 200, asr.text
    state = asr.json()
    assert state["status"] == "done", state

    collected = state["outputs"]["collected"]
    assert len(collected) == 1
    timeline_value = collected[0]["timeline"]
    timeline_path = Path(timeline_value)
    if not timeline_path.is_absolute():
        timeline_path = job_dir / timeline_path
    assert timeline_path.name == "asr.timeline.json"
    persisted = json.loads(timeline_path.read_text(encoding="utf-8"))
    _assert_real_normalized_timeline(persisted)


def test_instances_film_resolves_strategy_for_every_final_performer(
    contract_env: ContractEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP driver must resolve (node, domain), not bypass policy wrappers."""
    from ncds_opus_factory.server import pipeline_domain_strategies as strategies
    from ncds_opus_factory.server.engine import pipeline_performers_final as performers

    env = contract_env
    resolve_calls: list[tuple[str, str, str]] = []
    original_resolve = strategies.DOMAIN_STRATEGIES.resolve

    def recording_resolve(node: str, domain: object = None) -> Any:
        strategy = original_resolve(node, domain)
        resolve_calls.append(
            (
                node,
                strategies.normalize_domain(domain),
                str(strategy.name),
            )
        )
        return strategy

    def fake_lines_fallback(
        _user_prompt: str,
        _system_prompt: str,
        on_progress: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        on_progress("contract lines ready")
        return LINES_RESPONSE

    def fake_storyboard_fallback(
        _user_prompt: str,
        _system_prompt: str,
        on_progress: Any,
        *,
        parse: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        on_progress("contract storyboard ready")
        return parse(json.dumps(STORYBOARD_RESPONSE, ensure_ascii=False))

    def fake_generate_image(
        *,
        target: str | Path,
        **_kwargs: Any,
    ) -> list[Path]:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"contract-webp")
        return [path]

    def fake_tts(
        *,
        episode_path: str | Path,
        audio_dir: str | Path,
        on_line: Any,
        **_kwargs: Any,
    ) -> None:
        episode_file = Path(episode_path)
        episode = json.loads(episode_file.read_text(encoding="utf-8"))
        output_dir = Path(audio_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        for beat in episode["beats"]:
            scene = str(beat.get("scene") or "g1")
            audio_relpath = f"04_tts/scene-{scene}.mp3"
            (output_dir / f"scene-{scene}.mp3").write_bytes(b"contract-mp3")
            beat["audioFile"] = audio_relpath
            beat["audioStart"] = 0.0
            beat["audioEnd"] = 1.0
        episode_file.write_text(
            json.dumps(episode, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        on_line("contract tts ready")

    def fake_render(**kwargs: Any) -> dict[str, Any]:
        output_path = Path(kwargs["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"contract-mp4")
        return {
            "output_path": str(output_path),
            "video_size_bytes": output_path.stat().st_size,
            "workdir": kwargs["workdir"],
        }

    monkeypatch.setattr(
        strategies.DOMAIN_STRATEGIES,
        "resolve",
        recording_resolve,
    )
    monkeypatch.setattr(
        performers,
        "structure_lines_json_with_fallback",
        fake_lines_fallback,
    )
    monkeypatch.setattr(
        performers,
        "structure_json_with_model_fallback",
        fake_storyboard_fallback,
    )
    monkeypatch.setattr(performers, "_gen_scene_image", fake_generate_image)
    monkeypatch.setattr(performers, "_run_tts_gen", fake_tts)
    monkeypatch.setattr(performers, "_render_run", fake_render)

    created = env.client.post(
        "/instances",
        headers=env.headers,
        json={
            "recipe_id": "final_preview",
            "inputs": {"domain": "film", "target_language": "en"},
            "title": "film strategy platform contract",
        },
    )
    assert created.status_code == 201, created.text
    instance_id = str(created.json()["instance_id"])
    job_dir = tmp_path / "platform-film-job"

    assert _run_instance_step(env, instance_id, "input")["status"] == "done"
    asr_state = _run_instance_step(
        env,
        instance_id,
        "asr",
        {
            "job_dir": str(job_dir),
            "urls": ["7312345678901234567"],
        },
    )
    assert asr_state["status"] == "done", asr_state
    collected = asr_state["outputs"]["collected"]

    # Guiguzi is a separate agent route, not a final_preview recipe step. Seed
    # its real file boundary so the film RW strategy can consume it.
    (job_dir / "guiguzi.json").write_text(
        json.dumps(
            {
                "status": "done",
                "mode": "film_script_split",
                "revision": FILM_REVISION,
                "entity_glossary": FILM_ENTITY_GLOSSARY,
                "segments": CLASSIFIED_SEGMENTS,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    rw_state = _run_instance_step(
        env,
        instance_id,
        "rw",
        {
            "job_dir": str(job_dir),
            "asr_items": collected,
            "target_language": "en",
        },
    )
    assert rw_state["status"] == "awaiting_review", rw_state
    assert _approve_instance_step(env, instance_id, "rw")["status"] == "done"

    # Film RW intentionally persists localization rather than the legacy
    # selected draft. Supply the compatibility artifact required to execute
    # the unoverridden default lines performer.
    draft_path = job_dir / "02_rw" / "draft.md"
    draft_path.write_text(TRANSLATED_NARRATION, encoding="utf-8")
    lines_state = _run_instance_step(
        env,
        instance_id,
        "lines",
        {"job_dir": str(job_dir)},
    )
    assert lines_state["status"] == "awaiting_review", lines_state
    assert _approve_instance_step(env, instance_id, "lines")["status"] == "done"

    storyboard_state = _run_instance_step(
        env,
        instance_id,
        "storyboard",
        {"job_dir": str(job_dir)},
    )
    assert storyboard_state["status"] == "awaiting_review", storyboard_state
    assert (
        _approve_instance_step(env, instance_id, "storyboard")["status"]
        == "done"
    )

    image_state = _run_instance_step(
        env,
        instance_id,
        "image",
        {"job_dir": str(job_dir)},
    )
    assert image_state["status"] == "done", image_state
    tts_state = _run_instance_step(
        env,
        instance_id,
        "tts",
        {"job_dir": str(job_dir)},
    )
    assert tts_state["status"] == "done", tts_state

    preview_state = _run_instance_step(env, instance_id, "preview")
    assert preview_state["status"] == "awaiting_review", preview_state
    assert _approve_instance_step(env, instance_id, "preview")["status"] == "done"

    render_state = _run_instance_step(
        env,
        instance_id,
        "render",
        {"job_dir": str(job_dir)},
    )
    assert render_state["status"] == "done", render_state
    assert _run_instance_step(env, instance_id, "download")["status"] == "done"

    finalized = env.client.post(
        f"/instances/{instance_id}/finalize",
        headers=env.headers,
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "completed"

    expected_strategies = {
        "asr": "film:asr",
        "rw": "film:rw",
        "lines": "default:lines",
        "storyboard": "default:storyboard",
        "tts": "default:tts",
        "image": "default:image",
        "render": "default:render",
    }
    resolved = {
        node: name
        for node, domain, name in resolve_calls
        if domain == "film" and node in expected_strategies
    }
    assert resolved == expected_strategies

    timeline_value = collected[0]["timeline"]
    timeline_path = Path(timeline_value)
    if not timeline_path.is_absolute():
        timeline_path = job_dir / timeline_path
    _assert_real_normalized_timeline(
        json.loads(timeline_path.read_text(encoding="utf-8"))
    )
    localization = json.loads(
        (job_dir / "02_rw" / "film_localization.json").read_text(
            encoding="utf-8"
        )
    )
    assert [segment["segment_id"] for segment in localization["segments"]] == [
        "seg-001"
    ]
    episode = json.loads(
        (job_dir / "02_rw" / "episode.json").read_text(encoding="utf-8")
    )
    assert episode["visual"]["shots"][0]["shotId"] == "b001"
    assert (job_dir / "03_image" / "background.webp").is_file()
    assert (job_dir / "04_tts" / "scene-g1.mp3").is_file()
    assert (job_dir / "06_render" / "output.mp4").is_file()
