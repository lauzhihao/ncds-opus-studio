"""015 纸卡口播 pipeline 的引擎 step-performer（E1-b2 slice-1）。

把 web ``PipelineRunner`` 的 015 节点编排逻辑**原样复用**成引擎可派发的 performer
（契约 ``run(on_progress, **params) -> dict``，保留 ``video-jobs/`` 文件系统布局）：引擎只管
编排+状态+事件，performer 只做该步实际工作，并通过共享的 ``02_rw/episode.json`` 与上下游耦合。

slice-1 范围（最小后端验证）：
- ``lines`` / ``storyboard``：真实复用 ``_execute_lines`` / ``_execute_storyboard`` 的 opus
  结构化算法（经 :func:`_opus_structure` 间接层，便于测试注入桩；不重写算法）。
- ``asr/rw/tts/image/render`` 的真实包装（含 video_pipeline/tts_gen/render.mjs 子进程、4 模型 rw）
  需抽 PipelineRunner 实例方法 / 真实外部依赖，留后续 slice；其 e2e 编排已由集成测试用桩验证。

约定：performer 经 ``step_inputs`` 收到 ``job_dir``（= ``video-jobs/{job_id}``），读写其下 ``02_rw/``。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from ncds_opus_core.templates import template_dir as _template_dir
from ncds_opus_factory.commands import render_015
from ncds_opus_factory.server import storyboard_director
from ncds_opus_factory.server.pipeline_runner import (
    DEFAULT_RW_PROFILE,
    MODEL_CANDIDATES,
    _ModelUnavailable,
    _build_lines_prompt,
    _build_rw_prompt,
    _call_opus_for_rw,
    _generate_scene_image,
    _invoke_rw_candidate,
    _load_template_episode,
    _asr_stage_label,
    _polish_transcript_with_opus,
    _read_episode,
    _rebuild_tts_items_015,
    _run_tts_gen_015,
    _run_video_pipeline,
)

# 外部副作用调用的 seam（subprocess / node / gpt-image）：默认走真实 helper，测试 monkeypatch。
_run_tts_gen = _run_tts_gen_015
_gen_scene_image = _generate_scene_image
_render_run = render_015.run

# asr seam：默认指向真实 helper，测试 monkeypatch 替换。
# 命名风格仿照上面的 _run_tts_gen / _gen_scene_image。
_run_video_pipeline_fn = _run_video_pipeline
_polish_transcript = _polish_transcript_with_opus

# rw seam：async 调用候选模型的间接层。
_invoke_rw = _invoke_rw_candidate


def _opus_structure(user_prompt: str, system_prompt: str, model_id: str = "claude-opus-4-7") -> str:
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
    model_id: str = "claude-opus-4-7",
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
# ASR performer：串行跑每条 URL → video_pipeline.py 转写 + opus polish 成文章。
# 忠实复刻 PipelineRunner._execute_asr，做了以下替换：
#   - state.inputs["urls"] → 参数 urls
#   - self.video_jobs_dir/job_id → Path(job_dir)
#   - 跨 job 下载缓存从 self.video_jobs_dir/"_downloads" → Path(job_dir).parent/"_downloads"
#   - self._push_progress → on_progress
#   - 删掉 push_items()/push_outputs_patch 增量 mid-run 进度（引擎暂不支持增量 outputs）；
#     改成关键状态通过 on_progress 文本透出。
# ---------------------------------------------------------------------------
def run_asr_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    urls: list[str],
    shares: list[dict[str, Any]] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """ASR：串行处理每条媒体 URL，产出 transcript + opus 整理稿 article.md。

    产物布局（与 PipelineRunner._execute_asr 完全对齐）：
      01_asr/{idx}/deliverables/result.json    — video_pipeline.py 产出
      01_asr/{idx}/article.md                  — opus polish 后的文章（或 fallback 到 transcript）
    """
    jd = Path(job_dir)
    if not urls:
        raise ValueError("urls is empty; need at least one media URL")

    asr_root = jd / "01_asr"
    asr_root.mkdir(parents=True, exist_ok=True)

    # 定位 video_pipeline.py：复用 core 的 repo_root() + NOF_VIDEO_PIPELINE_SCRIPT 兜底。
    from ncds_opus_core.common.paths import repo_root as _repo_root

    env_script = os.getenv("NOF_VIDEO_PIPELINE_SCRIPT")
    pipeline_script = (
        Path(env_script)
        if env_script
        else _repo_root() / "skills" / "video-pipeline" / "scripts" / "video_pipeline.py"
    )
    if not pipeline_script.is_file():
        raise RuntimeError(f"video_pipeline.py not found at {pipeline_script}")

    # shares：InputPanel 解析出的标题/作者，按 URL 对齐（可选）。
    shares_by_url: dict[str, dict[str, Any]] = {}
    for s in (shares or []):
        if isinstance(s, dict) and isinstance(s.get("url"), str):
            shares_by_url[s["url"]] = s

    # 跨 job 下载缓存：parent = video-jobs/，_downloads 与各 job 目录平级。
    downloads_cache = jd.parent / "_downloads"
    downloads_cache.mkdir(parents=True, exist_ok=True)

    items: list[dict[str, Any]] = []
    for idx, url in enumerate(urls, start=1):
        on_progress(f"[{idx}/{len(urls)}] 准备中")
        try:
            item_dir = asr_root / str(idx)
            item_dir.mkdir(parents=True, exist_ok=True)

            url_md5 = hashlib.md5(url.encode("utf-8")).hexdigest()
            url_md5_short = url_md5[:12]
            cache_dir = downloads_cache / url_md5
            cache_dir.mkdir(parents=True, exist_ok=True)

            # 幂等 stamp：URL 变了（用户在 INPUT 节点改了链接）→ 清掉旧产物重新链入缓存。
            stamp_path = item_dir / ".url-stamp"
            existing_stamp = (
                stamp_path.read_text(encoding="utf-8").strip()
                if stamp_path.is_file() else ""
            )
            if existing_stamp and existing_stamp != url_md5_short:
                on_progress(f"[{idx}/{len(urls)}] URL 已变更，清掉旧产物")
                shutil.rmtree(item_dir)
                item_dir.mkdir(parents=True, exist_ok=True)
            stamp_path.write_text(url_md5_short, encoding="utf-8")

            # 缓存→raw/ symlink：若全局缓存里已有此 URL 对应的 mp4，链进 item_dir/raw/。
            # video_pipeline.py 的 download_video fast-path 看到 raw/ 已有 mp4 就跳过下载。
            raw_dir = item_dir / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            cached_mp4s = sorted(cache_dir.glob("*.mp4"))
            if cached_mp4s:
                on_progress(
                    f"[{idx}/{len(urls)}] 命中下载缓存，复用 {cached_mp4s[0].name}（跳过下载）"
                )
                for mp4 in cached_mp4s:
                    link = raw_dir / mp4.name
                    if not link.exists():
                        link.symlink_to(mp4.resolve())

            on_progress(f"[{idx}/{len(urls)}] 启动 video_pipeline.py")

            # on_line 闭包：把 video_pipeline.py 的 stdout 行转成阶段进度文本透出。
            def on_line(line: str, i: int = idx, total: int = len(urls)) -> None:
                lbl = _asr_stage_label(line)
                if lbl:
                    on_progress(f"[{i}/{total}] 阶段：{lbl}")
                on_progress(f"[{i}/{total}] {line}")

            _run_video_pipeline_fn(
                pipeline_script=pipeline_script,
                url=url,
                output_dir=item_dir,
                on_line=on_line,
            )

            # 下载完成后，把 raw/ 里新产出的真 mp4 迁移到全局缓存，原位留 symlink。
            # 下次同 URL（含其他 job）的 cache_dir.glob("*.mp4") 就能命中。
            for mp4 in raw_dir.glob("*.mp4"):
                if mp4.is_symlink():
                    continue
                cache_target = cache_dir / mp4.name
                if not cache_target.exists():
                    shutil.move(str(mp4), str(cache_target))
                else:
                    mp4.unlink()
                mp4.symlink_to(cache_target.resolve())

            result_json = item_dir / "deliverables" / "result.json"
            if not result_json.is_file():
                raise RuntimeError(f"video_pipeline 未产出 result.json: {result_json}")
            result = json.loads(result_json.read_text(encoding="utf-8"))

            transcript_abs = result.get("rawTranscriptPath") or result.get("transcript")
            if not transcript_abs or not Path(transcript_abs).is_file():
                raise RuntimeError(f"transcript 缺失或不存在: {transcript_abs}")

            # 文章整理：调本机 opus polish 原始 transcript 成 markdown 文章。
            # 同 PipelineRunner._execute_asr，幂等命中则跳过 opus；失败时 fallback 到 transcript 路径。
            article_path = item_dir / "article.md"
            share = shares_by_url.get(url) or {}
            try:
                polished = _polish_transcript(
                    transcript_path=Path(transcript_abs),
                    output_path=article_path,
                    title_hint=str(share.get("title") or share.get("author") or ""),
                )
                on_progress(f"[{idx}/{len(urls)}] " + (
                    "调 opus 整理成文章" if polished else "文章已是最新,跳过 opus"))
                article_abs = str(article_path)
            except Exception as exc:
                on_progress(
                    f"[{idx}/{len(urls)}] opus polish 失败：{exc}；fallback 到原始 transcript"
                )
                article_abs = transcript_abs

            items.append({
                "index": idx,
                "url": url,
                "title": str(share.get("title") or ""),
                "author": str(share.get("author") or ""),
                "transcript_relpath": str(Path(transcript_abs).resolve().relative_to(jd)),
                "article_relpath": str(Path(article_abs).resolve().relative_to(jd)),
                "error": None,
            })
            on_progress(f"[{idx}/{len(urls)}] 完成")

        except Exception as exc:
            msg = str(exc)
            first_line = msg.splitlines()[0] if msg.splitlines() else "未知错误"
            on_progress(f"[{idx}/{len(urls)}] 失败：{first_line}")
            sh = shares_by_url.get(url) or {}
            items.append({
                "index": idx,
                "url": url,
                "title": str(sh.get("title") or ""),
                "author": str(sh.get("author") or ""),
                "transcript_relpath": "",
                "article_relpath": "",
                "error": msg,
            })
            continue

    succeeded = [it for it in items if not it.get("error")]
    if not succeeded:
        raise RuntimeError(f"全部 {len(urls)} 个作品处理失败，详见各作品状态")

    return {"items": items, "asr_dir": str(asr_root)}


# ---------------------------------------------------------------------------
# RW performer：4 模型并行改写，同步包装 asyncio 并发。
# 忠实复刻 PipelineRunner._execute_rw，做了以下替换：
#   - state.nodes["asr"].outputs.items → 参数 asr_items
#   - state.node_configs["rw"]["profile"] → 参数 profile（缺省 DEFAULT_RW_PROFILE）
#   - self.video_jobs_dir/job_id → Path(job_dir)
#   - self._push_progress → on_progress
#   - 删掉 push_model_progress/push_outputs_patch 增量进度，on_status 给 no-op；
#     关键进度经 on_progress 文本透出。
# 同步函数（引擎在 to_thread 里跑）：内部用 asyncio.run() 跑 gather 并发，
# 不改成串行，保留原版 4 模型真并发语义。
# ---------------------------------------------------------------------------
def run_rw_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    asr_items: list[dict[str, Any]],
    profile: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """RW：4 模型并行改写，产出 02_rw/{model_id}/draft.md。

    产物布局（与 PipelineRunner._execute_rw 完全对齐）：
      02_rw/{model_id}/draft.md     — 各模型 markdown 改写稿（仅 success）
    """
    jd = Path(job_dir)
    if not asr_items:
        raise ValueError("asr_items is empty; nothing to rewrite")

    rw_root = jd / "02_rw"
    rw_root.mkdir(parents=True, exist_ok=True)

    # 拼 sourceText：asr 各条 article（opus 整理后的文章）拼起来。
    sections: list[str] = []
    for it in asr_items:
        relpath = it.get("article_relpath") or it.get("transcript_relpath")
        if not relpath:
            continue
        p = jd / relpath
        if not p.is_file():
            continue
        sections.append(
            f"## 来源 {it.get('index')} - {it.get('title') or ''}\n\n"
            f"{p.read_text(encoding='utf-8').strip()}"
        )
    source_text = "\n\n---\n\n".join(sections).strip()
    if not source_text:
        raise RuntimeError("asr 文章稿全部为空，无法 rw")

    effective_profile = profile if profile is not None else DEFAULT_RW_PROFILE
    system_prompt, user_prompt = _build_rw_prompt(effective_profile, source_text)

    on_progress(f"4 模型并行启动；source={len(source_text)} 字")

    # make_draft：把单模型结果（成功文本 / 异常）转成 draft dict，成功时写盘。
    # 内部实现与原版 _execute_rw 的 make_draft 闭包完全对齐。
    def make_draft(cand: dict[str, str], res: Any) -> dict[str, Any]:
        mid, label = cand["id"], cand["label"]
        if isinstance(res, _ModelUnavailable):
            return {
                "model_id": mid, "label": label, "status": "failed",
                "reason": f"模型不可用：{res}", "draft_relpath": None, "episode_relpath": None,
            }
        if isinstance(res, BaseException):
            return {
                "model_id": mid, "label": label, "status": "failed",
                "reason": str(res), "draft_relpath": None, "episode_relpath": None,
            }
        raw_text = (res or "").strip()
        # 去掉 ```json / ``` 包裹（与原版一致）。
        if raw_text.startswith("```"):
            inner = re.match(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$", raw_text)
            if inner:
                raw_text = inner.group(1).strip()
        if not raw_text:
            return {
                "model_id": mid, "label": label, "status": "failed",
                "reason": "模型输出为空", "draft_relpath": None, "episode_relpath": None,
            }
        model_dir = rw_root / mid
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "draft.md").write_text(raw_text + "\n", encoding="utf-8")
        on_progress(f"模型 {mid} draft 写盘完成（{len(raw_text)} 字）")
        return {
            "model_id": mid, "label": label, "status": "success", "reason": None,
            "draft_relpath": f"02_rw/{mid}/draft.md", "episode_relpath": None,
        }

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
            return make_draft(cand, res)

        return list(await asyncio.gather(*[run_one(c) for c in MODEL_CANDIDATES]))

    # 同步包装：performer 在引擎的 to_thread 里跑，直接 asyncio.run() 启动事件循环。
    drafts_out = asyncio.run(_run())

    # 保持与原版 ordered_drafts() 相同的顺序（按 MODEL_CANDIDATES 顺序）。
    id_order = {c["id"]: i for i, c in enumerate(MODEL_CANDIDATES)}
    drafts_out.sort(key=lambda d: id_order.get(d["model_id"], 999))

    success_count = sum(1 for d in drafts_out if d.get("status") == "success")
    if success_count == 0:
        reasons = "; ".join(f"{d['model_id']}={d.get('reason')}" for d in drafts_out)
        raise RuntimeError(f"4 个模型全部失败：{reasons}")

    on_progress(f"完成：{success_count}/{len(MODEL_CANDIDATES)} 成功")

    return {
        "drafts": drafts_out,
        "selected_model_id": None,
        "candidate_count": len(drafts_out),
        "success_count": success_count,
        "profile": effective_profile,
    }


def run_lines_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    pipeline_id: str = "paper_card_talk_015",
    **_: Any,
) -> dict[str, Any]:
    """LINES：读 ``02_rw/draft.md`` → opus 结构化成 beats[] → 合模板骨架写 ``02_rw/episode.json``。

    复用 ``_execute_lines`` 的算法（``_build_lines_prompt`` + :func:`_opus_structure`
    + ``_load_template_episode``），去掉 PipelineRunner 的状态管理（引擎接管）。
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
    on_progress("调 opus 结构化为 beats…")
    parsed = _opus_json_with_retry(user_prompt, system_prompt, on_progress)

    beats = parsed.get("beats") if isinstance(parsed, dict) else None
    meta_in = parsed.get("meta") if isinstance(parsed, dict) else None
    if not isinstance(beats, list) or not beats:
        raise RuntimeError("结构化结果缺 beats[] 或为空")

    norm_beats: list[dict[str, Any]] = []
    for b in beats:
        if not isinstance(b, dict):
            continue
        zh = str(b.get("zh") or "").strip()
        if not zh:
            continue
        norm_beats.append({
            "zh": zh,
            "en": str(b.get("en") or ""),
            "scene": "",
            "chapter": b.get("chapter") if isinstance(b.get("chapter"), int) else None,
        })
    if not norm_beats:
        raise RuntimeError("beats 全部为空")

    episode = _load_template_episode(pipeline_id)
    episode["beats"] = norm_beats
    episode["scenes"] = {}
    if isinstance(meta_in, dict):
        meta = dict(episode.get("meta") or {})
        if meta_in.get("title"):
            meta["title"] = str(meta_in["title"])
        if meta_in.get("subtitle"):
            meta["subtitle"] = str(meta_in["subtitle"])
        if isinstance(meta_in.get("tags"), list):
            meta["tags"] = [str(t) for t in meta_in["tags"]]
        episode["meta"] = meta

    ep_path = _episode_path(jd)
    ep_path.parent.mkdir(parents=True, exist_ok=True)
    ep_path.write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")
    on_progress(f"完成：{len(norm_beats)} 条 beats（scenes 待分镜产出）")
    return {"episode_relpath": "02_rw/episode.json", "beats_count": len(norm_beats)}


def run_storyboard_step(
    on_progress: Callable[[str], None],
    *,
    job_dir: str,
    **_: Any,
) -> dict[str, Any]:
    """STORYBOARD：读 ``episode.beats`` → director agent 切子场景 → 回填 ``beats[].scene``
    + 写 ``scenes{}`` 到 ``02_rw/episode.json``。复用 ``_execute_storyboard`` 算法。
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

    system_prompt, user_prompt = storyboard_director.build_director_prompt(
        ep.get("meta") or {},
        beats_in,
        style_bible=style_bible,
        container_guide=container_guide,
        palette=palette,
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
    beats_raw = ep.get("beats") or []
    if not beats_raw:
        raise ValueError("episode.beats is empty; nothing to synthesize")

    out_dir = jd / "04_tts"
    out_dir.mkdir(parents=True, exist_ok=True)
    tts_gen = _template_dir("paper_card_talk_015") / ".015-draft-assets" / "tts_gen.py"
    if not tts_gen.is_file():
        raise RuntimeError(f"tts_gen.py not found: {tts_gen}")
    ep_path = jd / "02_rw" / "episode.json"
    on_progress(f"按 scene 整段合成（{len(beats_raw)} beats）…")
    _run_tts_gen(script=tts_gen, episode_path=ep_path, audio_dir=out_dir, on_line=on_progress)

    ep2 = json.loads(ep_path.read_text(encoding="utf-8"))
    items = _rebuild_tts_items_015(ep2)
    scene_files = {it["audio_relpath"] for it in items if it.get("audio_relpath")}
    on_progress(f"完成：{len(scene_files)} 段 scene 音频 · {len(items)} beats")
    return {
        "items": items, "audio_dir": str(out_dir), "mode": "segmented",
        "scene_count": len(scene_files), "audio_count": len(scene_files),
    }


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

    beats = ep.get("beats") or []
    scenes_def = ep.get("scenes") or {}
    image_cfg = ep.get("image") or {}

    seen: set[str] = set()
    scene_order: list[str] = []
    for b in beats:
        sid = b.get("scene")
        if sid and sid not in seen:
            seen.add(sid)
            scene_order.append(sid)
    eligible = [sid for sid in scene_order if not sid.startswith("ch")]
    if not eligible:
        raise ValueError("no image-eligible scenes (all are chapter cards or no scenes)")

    size = image_cfg.get("size") or "1536x1024"
    quality = image_cfg.get("quality") or "auto"
    no_text_hint = image_cfg.get("noTextHint") or ""
    sketch_size = image_cfg.get("sketchSize") or "1024x1024"
    sketch_prefix = str(image_cfg.get("sketchStylePrefix") or "").strip()

    out_dir = jd / "03_image"
    out_dir.mkdir(parents=True, exist_ok=True)
    on_progress(f"image 开始：{len(eligible)} 个场景 · {size} {quality}")

    items: list[dict[str, Any]] = []
    ok = sk = fail = 0
    sketch_ok = sketch_fail = 0
    n_scenes = len(scene_order)
    for i, sid in enumerate(scene_order, start=1):
        sc = scenes_def.get(sid) or {}
        prompt = str(sc.get("prompt") or "").strip()
        if sid.startswith("ch"):
            items.append({"scene_id": sid, "prompt": prompt, "image_relpath": None,
                          "skipped_reason": "chapter card", "sketches": []})
            continue
        if not prompt:
            items.append({"scene_id": sid, "prompt": "", "image_relpath": None,
                          "skipped_reason": "empty prompt", "sketches": []})
            fail += 1
            continue

        target = out_dir / f"{sid}.webp"
        container_rel: str | None = None
        container_err: str | None = None
        if target.is_file():
            container_rel = f"03_image/{sid}.webp"
            sk += 1
            on_progress(f"[{i}/{n_scenes}] {sid} 容器图已存在，跳过")
        else:
            full_prompt = f"{prompt} {no_text_hint}".strip() if no_text_hint else prompt
            on_progress(f"[{i}/{n_scenes}] {sid} 容器图生成中…")
            try:
                _gen_scene_image(scene_id=sid, prompt=full_prompt, size=size,
                                 quality=quality, target=target, job_id=job_id)
                container_rel = f"03_image/{sid}.webp"
                ok += 1
            except Exception as exc:  # noqa: BLE001
                on_progress(f"[{i}/{n_scenes}] {sid} 容器图失败: {exc}")
                container_err = str(exc)
                fail += 1

        sketches_def = sc.get("sketches") or []
        sketch_items: list[dict[str, Any]] = []
        for n, skd in enumerate(sketches_def, start=1):
            sp = str((skd or {}).get("prompt") or "").strip()
            if not sp:
                continue
            srel = f"03_image/{sid}-sk{n}.webp"
            stgt = out_dir / f"{sid}-sk{n}.webp"
            if stgt.is_file():
                sketch_items.append({"index": n, "prompt": sp, "image_relpath": srel})
                continue
            sfull = " ".join(p for p in (sketch_prefix, sp, no_text_hint) if p)
            on_progress(f"[{i}/{n_scenes}] {sid} 简笔画 {n}/{len(sketches_def)} 生成中…")
            try:
                _gen_scene_image(scene_id=f"{sid}-sk{n}", prompt=sfull, size=sketch_size,
                                 quality=quality, target=stgt, job_id=job_id)
                sketch_items.append({"index": n, "prompt": sp, "image_relpath": srel})
                sketch_ok += 1
            except Exception as exc:  # noqa: BLE001
                on_progress(f"[{i}/{n_scenes}] {sid} 简笔画 {n} 失败: {exc}")
                sketch_items.append({"index": n, "prompt": sp, "image_relpath": None,
                                     "error": str(exc)})
                sketch_fail += 1

        item: dict[str, Any] = {"scene_id": sid, "prompt": prompt,
                                "image_relpath": container_rel, "sketches": sketch_items}
        if container_err:
            item["error"] = container_err
        items.append(item)

    if ok == 0 and fail > 0:
        raise RuntimeError(f"all {fail} scene image generations failed")

    on_progress(
        f"image 完成：容器 ok={ok} skipped={sk} failed={fail} · "
        f"简笔画 ok={sketch_ok} failed={sketch_fail}"
    )
    return {
        "items": items, "pictures_dir": str(out_dir), "pictures_count": ok + sk,
        "ok": ok, "skipped": sk, "failed": fail,
        "sketch_ok": sketch_ok, "sketch_failed": sketch_fail,
    }


def run_render_step(on_progress: Callable[[str], None], *, job_dir: str, **_: Any) -> dict[str, Any]:
    """RENDER：``render_015.run`` 出 1920x1080 MP4（依赖 episode + 04_tts/*.mp3 + 03_image/*.webp）。"""
    jd = Path(job_dir)
    episode_path = jd / "02_rw" / "episode.json"
    if not episode_path.is_file():
        raise ValueError("02_rw/episode.json missing; select an rw model first")
    audio_dir = jd / "04_tts"
    if not audio_dir.is_dir() or not any(audio_dir.glob("*.mp3")):
        raise ValueError("04_tts/*.mp3 missing; run tts first")
    picture_dir = jd / "03_image"
    out_dir = jd / "06_render"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "output.mp4"

    on_progress("启动 render_015（scene 整段合音）")
    result = _render_run(
        episode_path=str(episode_path),
        audio_dir=str(audio_dir),
        output_path=str(out_path),
        picture_dir=str(picture_dir) if picture_dir.is_dir() else None,
        workdir=str(out_dir / "_render_workdir"),
        cleanup_workdir=True,
        on_progress=on_progress,
    )
    return {
        "video_relpath": f"06_render/{out_path.name}",
        "output_path": result.get("output_path", str(out_path)),
        "video_size_bytes": result.get("video_size_bytes"),
        "workdir": result.get("workdir"),
    }


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
