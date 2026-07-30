"""Film ASR audio policy across legacy and engine execution boundaries."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from ncds_opus_factory.commands import shenkuo
from ncds_opus_factory.common import tikhub_client
from ncds_opus_factory.server.engine import pipeline_performers_final as performers
from ncds_opus_factory.server.engine.instance_runner import InstanceRunner
from ncds_opus_factory.server.engine.instance_store import InstanceStore
from ncds_opus_factory.server.pipeline_runner import PipelineRunner


def test_film_audio_policy_persists_across_legacy_and_engine_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 runner/store 落盘：film 无音轨，非 film 继续产出音轨。"""
    calls: list[dict[str, Any]] = []

    def offline_detail(aweme_id: str) -> dict[str, Any]:
        return {
            "aweme_id": aweme_id,
            "desc": f"source {aweme_id}",
            "author": {"nickname": "fixture-author"},
            "statistics": {},
            "video": {},
        }

    def offline_collect(
        aweme_id: str,
        author_dir: Path,
        *,
        do_audio: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        work_dir = Path(author_dir) / aweme_id
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "transcript_clean.txt").write_text("影视解说原稿", encoding="utf-8")
        entry: dict[str, Any] = {
            "aweme_id": aweme_id,
            "platform": kwargs.get("platform", "douyin"),
            "text": "影视解说原稿",
            "status": {"download": "ok", "transcribe": "ok"},
        }
        if do_audio:
            audio_dir = work_dir / "audio"
            audio_dir.mkdir(exist_ok=True)
            audio = {}
            for stem in ("original", "vocals", "bgm"):
                output = audio_dir / f"{stem}.mp3"
                output.write_bytes(f"{stem}-fixture".encode())
                audio[stem] = str(output)
            entry["audio"] = audio
            entry["status"]["audio"] = "ok"
        calls.append(
            {
                "root": str(author_dir),
                "do_audio": do_audio,
                "top_comments": kwargs.get("top_comments"),
            }
        )
        return entry

    # 只替换外部元数据/下载采集边界；runner、performer、状态机与文件持久化均走生产实现。
    monkeypatch.setattr(tikhub_client, "fetch_one_video_detail", offline_detail)
    monkeypatch.setattr(performers, "_fetch_one_video_detail", offline_detail)
    monkeypatch.setattr(shenkuo, "collect_one", offline_collect)
    monkeypatch.setattr(performers, "_collect_one", offline_collect)

    async def exercise_legacy(domain: str) -> dict[str, Any]:
        jobs_dir = tmp_path / f"legacy-{domain}" / "video-jobs"
        runner = PipelineRunner(video_jobs_dir=jobs_dir)
        job = runner.create_job(
            "final_preview",
            f"legacy-{domain}",
            {"domain": domain, "urls": ["7312345678901234567"]},
        )
        await runner.run_node(job.job_id, "asr")
        task = runner._running_nodes[(job.job_id, "asr")]
        await task
        enrich = runner._enrich_tasks.get(job.job_id)
        if enrich is not None:
            await enrich

        state_path = jobs_dir / job.job_id / "pipeline_state.json"
        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        assert persisted["nodes"]["asr"]["status"] == "done"
        return persisted["nodes"]["asr"]["outputs"]["collected"][0]

    async def exercise_engine(domain: str) -> dict[str, Any]:
        root = tmp_path / f"engine-{domain}"
        store = InstanceStore(root / "instances")
        runner = InstanceRunner(
            store=store,
            registry={"final_asr": performers.run_asr_step},
        )
        instance = runner.create_instance("final_preview", inputs={"domain": domain})
        await runner.run_step(instance.meta.instance_id, "input")
        job_dir = root / "video-jobs" / instance.meta.instance_id
        step = await runner.run_step(
            instance.meta.instance_id,
            "asr",
            {"job_dir": str(job_dir), "urls": ["7312345678901234567"]},
        )
        assert step.status == "done"

        state_path = store.step_state_path(instance.meta.instance_id, "asr")
        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        assert persisted["status"] == "done"
        return persisted["outputs"]["collected"][0]

    async def exercise_contract() -> None:
        for domain in ("film", "finance"):
            legacy_entry = await exercise_legacy(domain)
            engine_entry = await exercise_engine(domain)
            for entry in (legacy_entry, engine_entry):
                if domain == "film":
                    assert "audio" not in entry
                else:
                    assert set(entry["audio"]) == {"original", "vocals", "bgm"}

    asyncio.run(exercise_contract())

    assert calls
    film_calls = [
        call for call in calls
        if "legacy-film" in call["root"] or "engine-film" in call["root"]
    ]
    finance_calls = [
        call for call in calls
        if "legacy-finance" in call["root"] or "engine-finance" in call["root"]
    ]
    assert film_calls and finance_calls
    assert all(call["do_audio"] is False and call["top_comments"] == 0 for call in film_calls)
    assert all(call["do_audio"] is True for call in finance_calls)
    assert sum(call["top_comments"] == 20 for call in finance_calls) == 2
    assert not list((tmp_path / "legacy-film").rglob("*.mp3"))
    assert not list((tmp_path / "engine-film").rglob("*.mp3"))
    assert list((tmp_path / "legacy-finance").rglob("*.mp3"))
    assert list((tmp_path / "engine-finance").rglob("*.mp3"))
