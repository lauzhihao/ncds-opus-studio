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
    """Single-stage film timeline classification."""

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
            raise ValueError("沈括采集结果为空，无法读取 timeline")
        old = runner._guiguzi_tasks.get(job_id)
        if old is not None and not old.done():
            old.cancel()
        doc = {
            "mode": "film_script_split",
            "stage": "splitting",
            "status": "running",
            "segments": None,
            "summary": None,
            "error": None,
            "progress": "正在按 timeline 分类解说与影视原声…",
            "updated_at": time.time(),
        }
        runner._write_guiguzi(job_id, doc)
        runner._guiguzi_tasks[job_id] = asyncio.create_task(
            self._run_split_background(runner, job_id, collected)
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
            "film_script_split 是单阶段分类，不支持 /guiguzi/topics"
        )

    async def _run_split_background(
        self,
        runner: Any,
        job_id: str,
        collected: list[dict[str, Any]],
    ) -> None:
        from ncds_opus_factory.commands.film_script_split import (
            classify_collected_timelines,
        )

        try:
            segments = await asyncio.to_thread(
                classify_collected_timelines,
                runner.video_jobs_dir / job_id,
                collected,
            )
            if not segments:
                raise ValueError("沈括未产出可分类的 asr.timeline.json")
            counts = {
                role: sum(
                    1 for segment in segments
                    if segment.get("role") == role
                )
                for role in (
                    "replaceable_narration",
                    "preserved_original",
                    "unknown",
                )
            }
            doc = {
                "mode": "film_script_split",
                "stage": "done",
                "status": "done",
                "segments": segments,
                "summary": counts,
                "error": None,
                "progress": "",
                "updated_at": time.time(),
            }
        except Exception as exc:  # noqa: BLE001 - failure is persisted for polling.
            doc = {
                "mode": "film_script_split",
                "stage": "failed",
                "status": "failed",
                "segments": None,
                "summary": None,
                "error": f"{type(exc).__name__}: {exc}",
                "progress": "",
                "updated_at": time.time(),
            }
            logger.warning(
                "[pipeline] film guiguzi split failed for %s: %s",
                job_id,
                exc,
            )
        runner._write_guiguzi(job_id, doc)
        runner._emit(job_id, {"type": "job_updated", "job_id": job_id})


DEFAULT_GUIGUZI_PROCESSOR = DefaultGuiguziProcessor()
FILM_GUIGUZI_PROCESSOR = FilmGuiguziProcessor()
