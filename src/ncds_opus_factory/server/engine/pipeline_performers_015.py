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

from ncds_opus_factory.server import storyboard_director
from ncds_opus_factory.server.pipeline_runner import (
    _build_lines_prompt,
    _call_opus_for_rw,
    _load_template_episode,
)


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


# slice-1 performer 表：仅含已真实复用的步骤；asr/rw/tts/image/render 的真实包装后续 slice 补。
PERFORMERS_015: dict[str, Callable[..., dict[str, Any]]] = {
    "lines": run_lines_step,
    "storyboard": run_storyboard_step,
}
