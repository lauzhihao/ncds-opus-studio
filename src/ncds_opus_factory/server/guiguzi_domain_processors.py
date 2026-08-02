"""Domain processors for Guiguzi while keeping its HTTP routes unchanged."""

from __future__ import annotations

from typing import Any


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


DEFAULT_GUIGUZI_PROCESSOR = DefaultGuiguziProcessor()
