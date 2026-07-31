"""Registered domain policies for final_preview production nodes."""

from __future__ import annotations

from typing import Any

from ncds_opus_factory.server.domain_strategy import (
    WILDCARD_DOMAIN,
    AsrCollectionPolicy,
    DomainStrategyRegistry,
    EngineStrategyContext,
    GuiguziNodeStrategy,
    PipelineNodeStrategy,
    PipelineStrategyContext,
    normalize_domain,
)
from ncds_opus_factory.server.guiguzi_domain_processors import (
    DEFAULT_GUIGUZI_PROCESSOR,
    FILM_GUIGUZI_PROCESSOR,
)

RUNNABLE_NODES = (
    "asr",
    "rw",
    "lines",
    "storyboard",
    "tts",
    "image",
    "preview",
    "render",
)
_TRANSCRIPT_ONLY_MODES = {
    "transcript_only",
    "text_only",
    "transcript-only",
    "text-only",
}


def _explicit_transcript_only(inputs: dict[str, Any]) -> bool:
    mode = str(
        inputs.get("collect_mode")
        or inputs.get("shenkuo_collect_mode")
        or ""
    ).strip().lower()
    return mode in _TRANSCRIPT_ONLY_MODES or inputs.get("transcript_only") is True


def _default_asr_policy(inputs: dict[str, Any]) -> AsrCollectionPolicy:
    transcript_only = _explicit_transcript_only(inputs)
    return AsrCollectionPolicy(
        top_comments=0 if transcript_only else 20,
        do_audio=True,
        do_frames=False,
        enrich=not transcript_only,
        refresh=not transcript_only,
        progress_message=(
            "沈括纯文案模式：跳过评论和抠图，保留音轨采集"
            if transcript_only
            else None
        ),
    )


def _film_asr_policy(_inputs: dict[str, Any]) -> AsrCollectionPolicy:
    return AsrCollectionPolicy(
        top_comments=0,
        do_audio=False,
        do_frames=False,
        enrich=False,
        refresh=False,
        progress_message="沈括影视采集模式：跳过评论、抠图和音轨提取",
    )


async def _run_default_pipeline(
    context: PipelineStrategyContext,
) -> dict[str, Any]:
    return await context.default_execute()


def _run_default_engine(context: EngineStrategyContext) -> dict[str, Any]:
    return context.default_execute()


async def _run_film_asr_pipeline(
    context: PipelineStrategyContext,
) -> dict[str, Any]:
    from ncds_opus_factory.commands.film_subtitles import (
        collect_film_subtitles,
    )

    state = context.runner._load(context.job_id)
    inputs = state.inputs
    shares = list(inputs.get("shares") or [])
    urls = [
        str(value).strip()
        for value in (inputs.get("urls") or [])
        if str(value).strip()
    ]
    if not urls:
        urls = [
            str(share["url"]).strip()
            for share in shares
            if isinstance(share, dict)
            and isinstance(share.get("url"), str)
            and share["url"].strip()
        ]
    return await context.runner._run_in_thread_cancellable(
        collect_film_subtitles,
        context.runner._cancel_flag(context.job_id, context.node),
        context.runner.video_jobs_dir / context.job_id,
        urls,
        shares,
        on_progress=lambda text: context.runner._push_progress(
            context.job_id,
            context.node,
            text,
        ),
    )


def _run_film_asr_engine(context: EngineStrategyContext) -> dict[str, Any]:
    from ncds_opus_factory.commands.film_subtitles import (
        collect_film_subtitles,
    )

    return collect_film_subtitles(
        context.params["job_dir"],
        list(context.params.get("urls") or []),
        list(context.params.get("shares") or []),
        on_progress=context.on_progress,
    )


async def _spawn_default_asr_enrich(
    context: PipelineStrategyContext,
    _outputs: dict[str, Any],
) -> None:
    strategy = pipeline_strategy("asr", context.domain)
    state = context.runner._load(context.job_id)
    policy = (
        strategy.asr_policy(state.inputs)
        if strategy.asr_policy is not None
        else None
    )
    if policy is not None and policy.enrich:
        context.runner._spawn_asr_enrich(context.job_id)


async def _run_film_rw_pipeline(
    context: PipelineStrategyContext,
) -> dict[str, Any]:
    from ncds_opus_factory.commands.film_localization import localize_film_script

    target_language = str(context.params.get("target_language") or "en").strip().lower()
    return await context.runner._run_in_thread_cancellable(
        localize_film_script,
        context.runner._cancel_flag(context.job_id, context.node),
        context.runner.video_jobs_dir / context.job_id,
        target_language=target_language,
        on_progress=lambda text: context.runner._push_progress(
            context.job_id,
            context.node,
            text,
        ),
    )


def _run_film_rw_engine(context: EngineStrategyContext) -> dict[str, Any]:
    from ncds_opus_factory.commands.film_localization import localize_film_script

    target_language = str(
        context.params.get("target_language") or "en"
    ).strip().lower()
    return localize_film_script(
        context.params["job_dir"],
        target_language=target_language,
        on_progress=context.on_progress,
    )


DOMAIN_STRATEGIES: DomainStrategyRegistry[Any] = DomainStrategyRegistry()

for _node in RUNNABLE_NODES:
    DOMAIN_STRATEGIES.register(
        _node,
        WILDCARD_DOMAIN,
        PipelineNodeStrategy(
            name=f"default:{_node}",
            execute_pipeline=_run_default_pipeline,
            execute_engine=_run_default_engine,
            after_pipeline_success=(
                _spawn_default_asr_enrich if _node == "asr" else None
            ),
            asr_policy=_default_asr_policy if _node == "asr" else None,
        ),
    )

# film 沈括走共享字幕采集器；OCR 是真源，ASR sidecar 仅供鬼谷子对齐。
DOMAIN_STRATEGIES.register(
    "asr",
    "film",
    PipelineNodeStrategy(
        name="film:asr",
        execute_pipeline=_run_film_asr_pipeline,
        execute_engine=_run_film_asr_engine,
        asr_policy=_film_asr_policy,
    ),
)
DOMAIN_STRATEGIES.register(
    "rw",
    "film",
    PipelineNodeStrategy(
        name="film:rw",
        execute_pipeline=_run_film_rw_pipeline,
        execute_engine=_run_film_rw_engine,
    ),
)
DOMAIN_STRATEGIES.register(
    "guiguzi",
    WILDCARD_DOMAIN,
    GuiguziNodeStrategy(
        name="default:guiguzi",
        processor=DEFAULT_GUIGUZI_PROCESSOR,
    ),
)
DOMAIN_STRATEGIES.register(
    "guiguzi",
    "film",
    GuiguziNodeStrategy(
        name="film:guiguzi",
        processor=FILM_GUIGUZI_PROCESSOR,
    ),
)


def pipeline_strategy(node: str, domain: object) -> PipelineNodeStrategy:
    strategy = DOMAIN_STRATEGIES.resolve(node, normalize_domain(domain))
    if not isinstance(strategy, PipelineNodeStrategy):
        raise TypeError(f"registered strategy for {node} is not executable")
    return strategy


def guiguzi_strategy(domain: object) -> GuiguziNodeStrategy:
    strategy = DOMAIN_STRATEGIES.resolve("guiguzi", normalize_domain(domain))
    if not isinstance(strategy, GuiguziNodeStrategy):
        raise TypeError("registered guiguzi strategy is invalid")
    return strategy
