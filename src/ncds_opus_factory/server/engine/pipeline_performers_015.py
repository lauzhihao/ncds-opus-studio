"""015 纸卡口播 pipeline 的引擎 step-performer（E1-b2 slice-1）。

把 web ``PipelineRunner`` 的 015 节点编排逻辑**原样复用**成引擎可派发的 performer
（契约 ``run(on_progress, **params) -> dict``，保留 ``video-jobs/`` 文件系统布局）：引擎只管
编排+状态+事件，performer 只做该步实际工作，并通过共享的 ``02_rw/episode.json`` 与上下游耦合。

slice-1 范围（最小后端验证）：
- ``lines``：复用 shared lines 结构化算法与模型 fallback。
- ``storyboard``：真实复用 ``_execute_storyboard`` 的 opus 结构化算法
  （经 :func:`_opus_structure` 间接层，便于测试注入桩；不重写算法）。
- ``asr/rw/tts/image/render`` 的真实包装（asr=shenkuo.collect_one 快采；rw=多模型；
  tts/image/render=真实 helper/子进程），外部副作用经 seam 便于测试注入。

约定：performer 经 ``step_inputs`` 收到 ``job_dir``（= ``video-jobs/{job_id}``），读写其下 ``02_rw/``。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ncds_opus_core.common import cancel as _cancel
from ncds_opus_core.templates import template_dir as _template_dir

from ncds_opus_factory.commands import render_015, shenkuo
from ncds_opus_factory.common import tikhub_client
from ncds_opus_factory.server import storyboard_director
from ncds_opus_factory.server.domain_profiles import get_profile as _get_domain_profile
from ncds_opus_factory.server.pipeline_image_tasks import PipelineImageRun
from ncds_opus_factory.server.pipeline_lines_tasks import (
    _build_lines_prompt,
    _episode_from_lines_response,
    structure_lines_json_with_fallback,
)
from ncds_opus_factory.server.pipeline_media_helpers import (
    _generate_scene_image,
    _read_episode,
    _rebuild_tts_items_015,
    _run_tts_gen_015,
)
from ncds_opus_factory.server.pipeline_render_tasks import PipelineRenderRun
from ncds_opus_factory.server.pipeline_rw_helpers import (
    MODEL_CANDIDATES,
    _apply_rw_qc,
    _build_rw_prompt,
    _call_opus_for_rw,
    _invoke_rw_candidate,
    _ModelUnavailable,
    _rw_source_text,
)
from ncds_opus_factory.server.pipeline_rw_tasks import build_rw_draft
from ncds_opus_factory.server.pipeline_tts_tasks import PipelineTtsRun

# 外部副作用调用的 seam（subprocess / node / gpt-image）：默认走真实 helper，测试 monkeypatch。
_run_tts_gen = _run_tts_gen_015
_gen_scene_image = _generate_scene_image
_render_run = render_015.run

# asr seam：默认走沈括快采；测试 monkeypatch 替换。
_resolve_aweme_id = tikhub_client.resolve_aweme_id
_fetch_one_video_detail = tikhub_client.fetch_one_video_detail
_extract_meta = tikhub_client.extract_meta
_collect_one = shenkuo.collect_one

# rw seam：async 调用候选模型的间接层。
_invoke_rw = _invoke_rw_candidate


class _ProgressFacade:
    """Tiny runner-compatible facade for task contexts used by engine performers."""

    def __init__(self, on_progress: Callable[[str], None]) -> None:
        self._on_progress = on_progress

    def _push_progress(self, _job_id: str, _node_name: str, text: str) -> None:
        self._on_progress(text)


def _opus_structure(user_prompt: str, system_prompt: str, model_id: str = "claude-opus-4-8") -> str:
    """opus 结构化调用的间接层：默认走真实 opus 启动器；测试 monkeypatch 成桩。"""
    return _call_opus_for_rw(user_prompt, system_prompt, model_id)


def _parse_opus_json(raw: str) -> Any:
    """容忍 ```json ... ``` 包裹，解析 opus 输出为 Python 对象。"""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        inner = re.match(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$", cleaned)
        if inner:
            cleaned = inner.group(1).strip()
    return json.loads(cleaned)


def _opus_json_with_retry(
    user_prompt: str,
    system_prompt: str,
    on_progress: Callable[[str], None],
    *,
    parse: Callable[[str], Any] | None = None,
    model_id: str = "claude-opus-4-8",
    max_attempts: int = 3,
    retry_hint: str = "",
) -> Any:
    """调 opus 出结构化结果，解析失败就带纠正提示重试。

    opus 偶发在字符串值里塞未转义字符（典型：中文内容里的英文双引号），
    导致单行 JSON 解析在中段炸（``Expecting ',' delimiter``）。这里不做正则硬修
    （怕改坏中文正文），改用 LLM 自纠：把上次报错回传，要求只输出合法 JSON 并转义特殊字符。

    ``parse`` 默认 :func:`_parse_opus_json`（lines 用）；storyboard 传
    ``parse_director_output`` 的偏函数。``retry_hint`` 追加进纠正提示说明所需结构。
    只捕获 ``parse`` 抛的 ``ValueError``（含 ``json.JSONDecodeError``）/ ``RuntimeError`` 来重试；
    ``_opus_structure`` 自身的启动器错误（如 401）在 try 外，立即上抛、不重试。
    """
    parse = parse or _parse_opus_json
    last_exc: Exception | None = None
    last_raw = ""
    prompt = user_prompt
    for attempt in range(1, max_attempts + 1):
        last_raw = _opus_structure(prompt, system_prompt, model_id)
        try:
            return parse(last_raw)
        except (ValueError, RuntimeError) as exc:
            last_exc = exc
            if attempt < max_attempts:
                on_progress(f"opus 输出无法解析（第 {attempt}/{max_attempts} 次）：{exc}；带纠正提示重试…")
            # 纠正提示：始终基于原始 user_prompt 重建，避免多轮叠加污染。
            prompt = (
                user_prompt
                + "\n\n[严格要求] 上一次输出无法解析（报错："
                + f"{exc}）。只输出一个合法的 JSON 对象，禁止任何代码块/解释/前后缀；"
                + '字符串值内的英文双引号必须转义为 \\", 反斜杠转义为 \\\\, 换行转义为 \\n。'
                + (("\n" + retry_hint) if retry_hint else "")
            )
    raise RuntimeError(
        f"opus 输出无法解析（重试 {max_attempts} 次仍失败）：{last_exc}；tail={last_raw.strip()[-300:]}"
    )


def _episode_path(job_dir: Path) -> Path:
    return job_dir / "02_rw" / "episode.json"


# ---------------------------------------------------------------------------
# ASR performer：串行跑每条 URL → 沈括 collect_one 快采。
# web 画布的 ASR 节点固定走 legacy fast collect 路径；这里给 /instances 保留同口径 performer，
# 避免注册了另一套 video_pipeline 转写实现却在主链跑不到。
# ---------------------------------------------------------------------------
def run_asr_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    urls: list[str],
    shares: list[dict[str, Any]] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """ASR：串行处理每条媒体 URL，产出沈括快采 entry（文案/评论/元数据）。"""
    jd = Path(job_dir)
    if not urls:
        raise ValueError("urls is empty; need at least one media URL")

    collect_dir = jd / "01_collect"
    collect_dir.mkdir(parents=True, exist_ok=True)

    # shares：InputPanel 解析出的标题/作者，按 URL 对齐（可选）。
    shares_by_url: dict[str, dict[str, Any]] = {}
    for s in (shares or []):
        if isinstance(s, dict) and isinstance(s.get("url"), str):
            shares_by_url[s["url"]] = s

    collected: list[dict[str, Any]] = []
    for idx, url in enumerate(urls, start=1):
        on_progress(f"[{idx}/{len(urls)}] 解析作品链接")
        try:
            aweme_id = _resolve_aweme_id(url)
            if not aweme_id:
                raise RuntimeError(f"解析不出 aweme_id（仅支持抖音链接/口令）：{url}")

            share = shares_by_url.get(url) or {}
            meta: dict[str, Any] = {}
            try:
                extracted_meta = _extract_meta(_fetch_one_video_detail(aweme_id))
                meta = extracted_meta if isinstance(extracted_meta, dict) else {}
            except Exception as exc:
                on_progress(f"[{idx}/{len(urls)}] 元数据获取失败（不阻塞）：{exc}")
            if share.get("title") and not meta.get("desc"):
                meta["desc"] = str(share["title"])
            if share.get("author") and not meta.get("author"):
                meta["author"] = str(share["author"])

            def item_progress(text: str, i: int = idx, total: int = len(urls)) -> None:
                on_progress(f"[{i}/{total}] {text}")

            entry = _collect_one(
                aweme_id, collect_dir,
                meta=meta, on_progress=item_progress,
                do_audio=False, do_frames=False,
            )
            entry["index"] = idx
            entry["url"] = url
            entry.setdefault("error", None)
            collected.append(entry)
            on_progress(f"[{idx}/{len(urls)}] 采集完成（文案/评论/数据）")
        except _cancel.TaskCancelled:
            raise
        except Exception as exc:
            msg = str(exc)
            first_line = msg.splitlines()[0] if msg.splitlines() else "未知错误"
            on_progress(f"[{idx}/{len(urls)}] 失败：{first_line}")
            collected.append({"index": idx, "url": url, "aweme_id": "", "status": {}, "error": msg})
            continue

    succeeded = [it for it in collected if not it.get("error")]
    if not succeeded:
        raise RuntimeError(f"全部 {len(urls)} 个作品处理失败，详见各作品状态")

    return {"collected": collected, "items": collected, "collect_dir": str(collect_dir)}


# ---------------------------------------------------------------------------
# RW performer：多模型并行改写（按 MODEL_CANDIDATES），同步包装 asyncio 并发。
# 忠实复刻 PipelineRunner._execute_rw，做了以下替换：
#   - state.nodes["asr"].outputs.items → 参数 asr_items
#   - self.video_jobs_dir/job_id → Path(job_dir)
#   - self._push_progress → on_progress
#   - 删掉 push_model_progress/push_outputs_patch 增量进度，on_status 给 no-op；
#     关键进度经 on_progress 文本透出。
# 写作要求由作品垂类标签(domain)的 liuyong 写作方法独家驱动（「体裁 profile」已废）。
# 同步函数（引擎在 to_thread 里跑）：内部用 asyncio.run() 跑 gather 并发，
# 不改成串行，保留原版多模型真并发语义。
# ---------------------------------------------------------------------------
def run_rw_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    asr_items: list[dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    """RW：多模型并行改写（按 MODEL_CANDIDATES），产出 02_rw/{model_id}/draft.md。

    产物布局（与 PipelineRunner._execute_rw 完全对齐）：
      02_rw/{model_id}/draft.md     — 各模型 markdown 改写稿（仅 success）

    kwargs 中的 domain（由 instance_runner 从 instance inputs 透传）取该垂类的写作方法
    （domain_profiles.liuyong）驱动写作；其余未知 kwargs 忽略（向前兼容）。
    """
    jd = Path(job_dir)
    if not asr_items:
        raise ValueError("asr_items is empty; nothing to rewrite")

    rw_root = jd / "02_rw"
    rw_root.mkdir(parents=True, exist_ok=True)

    # 拼 sourceText：沈括采集的清洗稿 text（缺失回退 legacy article 文件）。
    source_text = _rw_source_text(asr_items, jd)
    if not source_text:
        raise RuntimeError("asr 采集文案全部为空，无法 rw")

    # domain 由 instance_runner 从 instance inputs 透传（task-2.2 已落地）；
    # 取 domain_profiles 的 liuyong 槽位作为该垂类的写作方法，独家驱动写作。
    # domain 为空或无 liuyong 时 domain_guidance=None → _build_rw_prompt 回退通用兜底。
    domain: str | None = kwargs.get("domain")
    domain_guidance: str | None = None
    if domain:
        dp = _get_domain_profile(domain)
        if dp is not None:
            domain_guidance = dp.get("liuyong")
    system_prompt, user_prompt = _build_rw_prompt(source_text, domain_guidance=domain_guidance)

    on_progress(f"{len(MODEL_CANDIDATES)} 模型并行启动；source={len(source_text)} 字")

    # 内部 async 函数：复用原版 asyncio.gather 并发语义，调 _invoke_rw seam。
    # on_status 给 no-op，performer 不需要模型级增量状态（引擎暂不支持）。
    async def _run() -> list[dict[str, Any]]:
        _noop_status: Callable[[str, str], None] = lambda *a: None  # noqa: E731

        async def run_one(cand: dict[str, str]) -> dict[str, Any]:
            try:
                res: Any = await _invoke_rw(
                    cand, user_prompt, system_prompt, on_progress, _noop_status
                )
            except BaseException as exc:  # noqa: BLE001
                res = exc
            draft = build_rw_draft(
                rw_root=rw_root,
                cand=cand,
                res=res,
                model_unavailable_cls=_ModelUnavailable,
                on_progress=on_progress,
            )
            if draft.get("status") == "success":
                # 柳永质检闸门：ai_taste 打回重写 + rubric 评分（与 _execute_rw 一致）。
                try:
                    draft.update(await asyncio.to_thread(_apply_rw_qc, rw_root / cand["id"], cand["id"], on_progress))
                except Exception as exc:  # noqa: BLE001 — 质检失败不拖垮出稿
                    on_progress(f"  [{cand['id']}] 质检异常（不影响稿件）: {exc}")
            return draft

        return list(await asyncio.gather(*[run_one(c) for c in MODEL_CANDIDATES]))

    # 同步包装：performer 在引擎的 to_thread 里跑，直接 asyncio.run() 启动事件循环。
    drafts_out = asyncio.run(_run())

    # 保持与原版 ordered_drafts() 相同的顺序（按 MODEL_CANDIDATES 顺序）。
    id_order = {c["id"]: i for i, c in enumerate(MODEL_CANDIDATES)}
    drafts_out.sort(key=lambda d: id_order.get(d["model_id"], 999))

    success_count = sum(1 for d in drafts_out if d.get("status") == "success")
    if success_count == 0:
        reasons = "; ".join(f"{d['model_id']}={d.get('reason')}" for d in drafts_out)
        raise RuntimeError(f"{len(MODEL_CANDIDATES)} 个模型全部失败：{reasons}")

    on_progress(f"完成：{success_count}/{len(MODEL_CANDIDATES)} 成功")

    return {
        "drafts": drafts_out,
        "selected_model_id": None,
        "candidate_count": len(drafts_out),
        "success_count": success_count,
    }


def run_lines_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    pipeline_id: str = "paper_card_talk_015",
    **_: Any,
) -> dict[str, Any]:
    """LINES：读 ``02_rw/draft.md`` → LLM fallback 结构化成 beats[] → 合模板骨架写 ``02_rw/episode.json``。

    复用 ``_build_lines_prompt`` + ``_episode_from_lines_response`` 的结构化算法，
    去掉 PipelineRunner 的状态管理（引擎接管）。
    scenes 留空 {} 交给下游 storyboard。
    """
    jd = Path(job_dir)
    draft_path = jd / "02_rw" / "draft.md"
    if not draft_path.is_file():
        raise ValueError("02_rw/draft.md missing；需先在 RW 选定稿模型")
    draft = draft_path.read_text(encoding="utf-8").strip()
    if not draft:
        raise ValueError("02_rw/draft.md 为空")

    system_prompt, user_prompt = _build_lines_prompt(draft)
    parsed = structure_lines_json_with_fallback(user_prompt, system_prompt, on_progress)
    episode, beats_count = _episode_from_lines_response(parsed, pipeline_id)

    ep_path = _episode_path(jd)
    ep_path.parent.mkdir(parents=True, exist_ok=True)
    ep_path.write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")
    on_progress(f"完成：{beats_count} 条 beats（scenes 待分镜产出）")
    return {"episode_relpath": "02_rw/episode.json", "beats_count": beats_count}


def run_storyboard_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """STORYBOARD：读 ``episode.beats`` → director agent 切子场景 → 回填 ``beats[].scene``
    + 写 ``scenes{}`` 到 ``02_rw/episode.json``。复用 ``_execute_storyboard`` 算法。

    kwargs 中的 domain（由 instance_runner 从 instance inputs 透传）用于取领域视觉调性；
    其余未知 kwargs 忽略（向前兼容）。
    """
    jd = Path(job_dir)
    ep_path = _episode_path(jd)
    if not ep_path.is_file():
        raise ValueError("episode.json not found; run lines first")
    ep = json.loads(ep_path.read_text(encoding="utf-8"))
    beats_raw = ep.get("beats") or []
    if not beats_raw:
        raise ValueError("episode.beats is empty; run lines first")

    beats_in = [
        {"index": i, "zh": str(b.get("zh") or ""), "en": str(b.get("en") or "")}
        for i, b in enumerate(beats_raw, start=1)
    ]
    image_cfg = ep.get("image") or {}
    style_bible = (
        str(image_cfg.get("sketchStylePrefix") or "").strip()
        or storyboard_director.DEFAULT_SKETCH_STYLE_PREFIX
    )
    container_guide = str(image_cfg.get("sketchContainerGuide") or "").strip()
    palette = str((ep.get("visual") or {}).get("palette") or "").strip()

    # domain 由 instance_runner 从 instance inputs 透传（task-2.2 已落地）；
    # 取 domain_profiles 的 wudaozi 槽位作为领域视觉调性，叠加在风格圣经之外（不替换）。
    # domain 为空或 profile 无 wudaozi 时 domain_image_style=None，行为与原来完全一致。
    domain: str | None = kwargs.get("domain")
    domain_image_style: str | None = None
    if domain:
        dp = _get_domain_profile(domain)
        if dp is not None:
            domain_image_style = dp.get("wudaozi")

    system_prompt, user_prompt = storyboard_director.build_director_prompt(
        ep.get("meta") or {},
        beats_in,
        style_bible=style_bible,
        container_guide=container_guide,
        palette=palette,
        domain_image_style=domain_image_style,
    )
    on_progress(f"调 director agent 分镜（{len(beats_in)} beats）…")
    scene_by_beat, scenes = _opus_json_with_retry(
        user_prompt, system_prompt, on_progress,
        parse=lambda raw: storyboard_director.parse_director_output(raw, beats_raw),
        retry_hint="JSON 必须含 scenes{} 与 sceneMap{} 两个键，scenes 的每个值含 prompt 字段。",
    )

    for i, b in enumerate(beats_raw, start=1):
        b["scene"] = scene_by_beat.get(i, b.get("scene") or "")
    ep["beats"] = beats_raw
    ep["scenes"] = scenes
    ep_path.write_text(json.dumps(ep, ensure_ascii=False, indent=2), encoding="utf-8")

    sketch_total = sum(len(s.get("sketches") or []) for s in scenes.values())
    groups = sorted({s.get("group") or sid for sid, s in scenes.items()})
    on_progress(f"完成：{len(groups)} 段 · {len(scenes)} 个子场景 · {sketch_total} 幅简笔画")
    return {
        "episode_relpath": "02_rw/episode.json",
        "scenes_count": len(scenes),
        "sketches_count": sketch_total,
        "groups_count": len(groups),     # 与 web StoryboardOutputs 契约对齐（types.ts 非可选）
        "beats_count": len(beats_raw),
    }


def run_tts_step(on_progress: Callable[[str], None], *, job_dir: str, **_: Any) -> dict[str, Any]:
    """TTS：按 ``02_rw/episode.json`` 的 beats[].scene spawn 015 tts_gen.py 整段合成
    + 写回字级时间戳 → 重建 beat 级 items。复用 ``_run_tts_gen_015`` / ``_rebuild_tts_items_015``。
    """
    jd = Path(job_dir)
    ep = _read_episode(jd)
    if ep is None:
        raise ValueError("episode.json not found; run rw/lines first")
    tts_gen = _template_dir("paper_card_talk_015") / ".015-draft-assets" / "tts_gen.py"
    return asyncio.run(PipelineTtsRun(
        runner=_ProgressFacade(on_progress),
        job_id=jd.name,
        job_dir=jd,
        episode=ep,
        tts_gen_script=tts_gen,
        run_tts_gen=_run_tts_gen,
        rebuild_tts_items=_rebuild_tts_items_015,
    ).run())


def run_image_step(on_progress: Callable[[str], None], *, job_dir: str, **_: Any) -> dict[str, Any]:
    """IMAGE：按 ``episode.scenes[].prompt`` 逐 scene 调 gpt-image-2 出容器图 + 简笔画 → WebP。
    复刻 ``_execute_image`` 编排（出场序去重、跳 ch* 章节卡、幂等、容器+简笔画分层），复用
    ``_generate_scene_image``。``job_id`` 仅供出图临时目录命名，取 job_dir 末段。
    """
    jd = Path(job_dir)
    job_id = jd.name
    ep = _read_episode(jd)
    if ep is None:
        raise ValueError("episode.json not found; run rw/lines first")
    return asyncio.run(PipelineImageRun(
        runner=_ProgressFacade(on_progress),
        job_id=job_id,
        job_dir=jd,
        episode=ep,
        generate_scene_image=_gen_scene_image,
    ).run())


def run_render_step(on_progress: Callable[[str], None], *, job_dir: str, **_: Any) -> dict[str, Any]:
    """RENDER：``render_015.run`` 出 1920x1080 MP4（依赖 episode + 04_tts/*.mp3 + 03_image/*.webp）。"""
    jd = Path(job_dir)
    return asyncio.run(PipelineRenderRun(
        runner=_ProgressFacade(on_progress),
        job_id=jd.name,
        job_dir=jd,
        render_run=_render_run,
    ).run())


# 015 recipe 各步 cmd → orchestration performer。键带 ``pct015_`` 前缀，与 build_full_registry
# 的 bare command（asr/rw/tts/wst/render_015）区分；引擎按 recipe 步骤的 cmd 字符串在合并 registry
# （build_full_registry ∪ PERFORMERS_015，见 server/state.py）里查表派发。
PERFORMERS_015: dict[str, Callable[..., dict[str, Any]]] = {
    "pct015_asr": run_asr_step,
    "pct015_rw": run_rw_step,
    "pct015_lines": run_lines_step,
    "pct015_storyboard": run_storyboard_step,
    "pct015_tts": run_tts_step,
    "pct015_image": run_image_step,
    "pct015_render": run_render_step,
}
