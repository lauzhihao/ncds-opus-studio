"""Domain-aware node strategy registry shared by pipeline facade and engine."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

StrategyT = TypeVar("StrategyT")
PipelineExecutor = Callable[["PipelineStrategyContext"], Awaitable[dict[str, Any]]]
PipelineSuccessHook = Callable[
    ["PipelineStrategyContext", dict[str, Any]],
    Awaitable[None],
]
EngineExecutor = Callable[["EngineStrategyContext"], dict[str, Any]]

WILDCARD_DOMAIN = "*"
DEFAULT_DOMAIN = "default"


def normalize_domain(domain: object) -> str:
    """Normalize a persisted domain key without treating unknown values as film."""
    value = str(domain or "").strip().lower()
    return value or DEFAULT_DOMAIN


def _normalize_node(node: object) -> str:
    value = str(node or "").strip().lower()
    if not value:
        raise ValueError("strategy node is empty")
    return value


class DomainStrategyRegistry(Generic[StrategyT]):
    """Resolve strategies by ``(node, domain)`` with wildcard/default fallback."""

    def __init__(self) -> None:
        self._strategies: dict[tuple[str, str], StrategyT] = {}

    def register(self, node: str, domain: str | None, strategy: StrategyT) -> None:
        node_key = _normalize_node(node)
        domain_key = (
            WILDCARD_DOMAIN
            if domain == WILDCARD_DOMAIN
            else normalize_domain(domain)
        )
        key = (node_key, domain_key)
        if key in self._strategies:
            raise ValueError(
                f"duplicate domain strategy registration: {node_key}/{domain_key}"
            )
        self._strategies[key] = strategy

    def resolve(self, node: str, domain: object = None) -> StrategyT:
        node_key = _normalize_node(node)
        domain_key = normalize_domain(domain)
        for key in (
            (node_key, domain_key),
            (node_key, WILDCARD_DOMAIN),
            (node_key, DEFAULT_DOMAIN),
        ):
            if key in self._strategies:
                return self._strategies[key]
        raise KeyError(f"no domain strategy registered: {node_key}/{domain_key}")


@dataclass(frozen=True)
class PipelineStrategyContext:
    runner: Any
    job_id: str
    node: str
    domain: str
    params: dict[str, Any]
    default_execute: Callable[[], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class EngineStrategyContext:
    node: str
    domain: str
    params: dict[str, Any]
    on_progress: Callable[[str], None]
    default_execute: Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class AsrCollectionPolicy:
    top_comments: int
    do_audio: bool
    do_frames: bool
    enrich: bool
    refresh: bool
    progress_message: str | None = None


@dataclass(frozen=True)
class PipelineNodeStrategy:
    """Execution adapters plus an optional facade-only successful-run hook."""

    name: str
    execute_pipeline: PipelineExecutor
    execute_engine: EngineExecutor
    after_pipeline_success: PipelineSuccessHook | None = None
    asr_policy: Callable[[dict[str, Any]], AsrCollectionPolicy] | None = None


class GuiguziProcessor(Protocol):
    """Domain-owned Guiguzi orchestration behind the unchanged route contract."""

    def analyze(
        self,
        runner: Any,
        job_id: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def topics(
        self,
        runner: Any,
        job_id: str,
        items: list[dict[str, Any]],
        analysis: dict[str, Any] | None,
        *,
        prompt: str | None,
        force: bool,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GuiguziNodeStrategy:
    name: str
    processor: GuiguziProcessor
    # Guiguzi historically only served the legacy HTTP route.  Film rebuild
    # promotes it into a synchronous engine step while retaining that route
    # processor; other domains safely fall back to their recipe performer.
    execute_engine: EngineExecutor | None = None
