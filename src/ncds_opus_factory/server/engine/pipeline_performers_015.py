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

import json
import re
from pathlib import Path
from typing import Any, Callable

from ncds_opus_core.templates import template_dir as _template_dir
from ncds_opus_factory.commands import render_015
from ncds_opus_factory.server import storyboard_director
from ncds_opus_factory.server.pipeline_runner import (
    _build_lines_prompt,
    _call_opus_for_rw,
    _generate_scene_image,
    _load_template_episode,
    _read_episode,
    _rebuild_tts_items_015,
    _run_tts_gen_015,
)

# 外部副作用调用的 seam（subprocess / node / gpt-image）：默认走真实 helper，测试 monkeypatch。
_run_tts_gen = _run_tts_gen_015
_gen_scene_image = _generate_scene_image
_render_run = render_015.run


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


def _episode_path(job_dir: Path) -> Path:
    return job_dir / "02_rw" / "episode.json"


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
    raw = _opus_structure(user_prompt, system_prompt)
    try:
        parsed = _parse_opus_json(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"opus 输出非法 JSON：{exc}") from exc

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
    raw = _opus_structure(user_prompt, system_prompt)
    scene_by_beat, scenes = storyboard_director.parse_director_output(raw, beats_raw)

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


# 已真实复用的 015 performer。asr/rw（inputs.urls / profile / 4 模型 async）待下一 slice。
PERFORMERS_015: dict[str, Callable[..., dict[str, Any]]] = {
    "lines": run_lines_step,
    "storyboard": run_storyboard_step,
    "tts": run_tts_step,
    "image": run_image_step,
    "render": run_render_step,
}
