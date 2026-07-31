"""Domain processors for Guiguzi while keeping its HTTP routes unchanged."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class DefaultGuiguziProcessor:
    """Adapter around the existing topic-discovery implementation."""

    def analyze(
        self,
        runner: Any,
        job_id: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return runner._analyze_guiguzi_default(job_id, items)

    def topics(
        self,
        runner: Any,
        job_id: str,
        items: list[dict[str, Any]],
        analysis: dict[str, Any] | None,
        *,
        prompt: str | None,
        force: bool,
    ) -> dict[str, Any]:
        return runner._generate_guiguzi_default(
            job_id,
            items,
            analysis,
            prompt=prompt,
            force=force,
        )


class FilmGuiguziProcessor:
    """Clean Shenkuo OCR cues into a narration-first film commentary."""

    def analyze(
        self,
        runner: Any,
        job_id: str,
        _items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            state = runner._load(job_id)
        except FileNotFoundError as exc:
            raise KeyError(job_id) from exc
        asr_node = state.nodes.get("asr")
        if asr_node is None or asr_node.status != "done":
            raise ValueError("请先完成沈括采集与 timeline 标准化")
        asr_outputs = asr_node.outputs or {}
        collected = list(
            asr_outputs.get("collected") or asr_outputs.get("items") or []
        )
        if not collected:
            raise ValueError("沈括采集结果为空，无法读取 film subtitles")
        if not any(
            isinstance(entry, dict)
            and isinstance(entry.get("film_source"), dict)
            and entry["film_source"].get("mode") == "film_subtitle_source"
            for entry in collected
        ):
            raise ValueError("沈括结果不是 film_subtitle_source，请重新采集")
        old = runner._guiguzi_tasks.get(job_id)
        if old is not None and not old.done():
            old.cancel()
        doc = {
            "mode": "film_commentary",
            "stage": "cleaning",
            "status": "running",
            "sources": None,
            "cues": None,
            "script": None,
            "entity_glossary": None,
            "qa": None,
            "error": None,
            "progress": "清洗影视解说字幕",
            "updated_at": time.time(),
        }
        runner._write_guiguzi(job_id, doc)
        runner._guiguzi_tasks[job_id] = asyncio.create_task(
            self._run_commentary_background(runner, job_id, collected)
        )
        return doc

    def topics(
        self,
        _runner: Any,
        _job_id: str,
        _items: list[dict[str, Any]],
        _analysis: dict[str, Any] | None,
        *,
        prompt: str | None,
        force: bool,
    ) -> dict[str, Any]:
        del prompt, force
        raise ValueError(
            "film_commentary 是单阶段语义清洗，不支持 /guiguzi/topics"
        )

    async def _run_commentary_background(
        self,
        runner: Any,
        job_id: str,
        collected: list[dict[str, Any]],
    ) -> None:
        from ncds_opus_factory.commands.film_commentary import (
            build_film_commentary,
        )

        try:
            result = await asyncio.to_thread(
                build_film_commentary,
                runner.video_jobs_dir / job_id,
                collected,
                on_progress=runner._guiguzi_progress(job_id),
            )
            doc = {
                "stage": "done",
                **result,
                "error": None,
                "progress": "",
                "updated_at": time.time(),
            }
        except Exception as exc:  # noqa: BLE001 - failure is persisted for polling.
            doc = {
                "mode": "film_commentary",
                "stage": "failed",
                "status": "failed",
                "sources": None,
                "cues": None,
                "script": None,
                "entity_glossary": None,
                "qa": None,
                "error": f"{type(exc).__name__}: {exc}",
                "progress": "",
                "updated_at": time.time(),
            }
            logger.warning(
                "[pipeline] film guiguzi commentary failed for %s: %s",
                job_id,
                exc,
            )
        runner._write_guiguzi(job_id, doc)
        runner._emit(job_id, {"type": "job_updated", "job_id": job_id})


DEFAULT_GUIGUZI_PROCESSOR = DefaultGuiguziProcessor()
FILM_GUIGUZI_PROCESSOR = FilmGuiguziProcessor()
