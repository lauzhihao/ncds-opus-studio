"""LINES step execution context shared by legacy runner and engine performer."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ncds_opus_core.templates import template_dir as _template_dir


@dataclass
class PipelineLinesRun:
    """Run legacy LINES from ``02_rw/draft.md`` into ``02_rw/episode.json``."""

    runner: Any
    job_id: str
    job_dir: Path
    pipeline_id: str
    call_opus_for_rw: Callable[[str, str, str], str]
    model_id: str

    async def run(self) -> dict[str, Any]:
        draft_path = self.job_dir / "02_rw" / "draft.md"
        if not draft_path.is_file():
            raise ValueError(
                "02_rw/draft.md missing；先在 RW 抽屉里选模型（用此模型 · 下一步）"
            )
        draft = draft_path.read_text(encoding="utf-8").strip()
        if not draft:
            raise ValueError("02_rw/draft.md 为空")

        system_prompt, user_prompt = _build_lines_prompt(draft)
        self._on_progress("调 opus 结构化为 beats…")
        raw = await asyncio.to_thread(
            self.call_opus_for_rw, user_prompt, system_prompt, self.model_id
        )
        parsed = _parse_lines_json(raw)
        episode, beats_count = _episode_from_lines_response(parsed, self.pipeline_id)

        ep_path = self.job_dir / "02_rw" / "episode.json"
        ep_path.write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")
        self._on_progress(f"完成：{beats_count} 条 beats（scenes 待分镜产出）")
        return {
            "episode_relpath": "02_rw/episode.json",
            "beats_count": beats_count,
        }

    def _on_progress(self, text: str) -> None:
        self.runner._push_progress(self.job_id, "lines", text)


def _parse_lines_json(raw: str) -> Any:
    """Parse opus JSON output, tolerating fenced code blocks."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        inner = re.match(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$", cleaned)
        if inner:
            cleaned = inner.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"opus 输出非法 JSON：{exc}；tail={cleaned[-300:]}") from exc


def _episode_from_lines_response(
    parsed: Any,
    pipeline_id: str,
) -> tuple[dict[str, Any], int]:
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
    return episode, len(norm_beats)


def _build_lines_prompt(draft_md: str) -> tuple[str, str]:
    """LINES 阶段：把 RW 定稿 markdown 文章结构化成逐句字幕 beats[]。

    只产脚本层（meta + beats），**不产 scenes / 分镜**——画面切分与简笔画设计
    交给下游独立的 storyboard（分镜）节点的 director agent。
    """
    system_prompt = (
        "你是 paper-card-talk 短视频脚本结构化助手。把给定文章拆成短视频的逐句字幕"
        "（beats）。只输出一个合法 JSON 对象，禁止代码块或任何额外文本。"
        "不要产出任何画面 / 分镜 / 图像描述——那是后续分镜环节的事。"
    )
    user_prompt = "\n".join([
        "把下面这篇文章结构化成 paper-card-talk 短视频的逐句字幕 JSON。",
        "",
        "【输出格式】只输出一个 JSON 对象，结构严格如下，不要代码块包裹、不要解释：",
        "{",
        '  "meta": { "title": "短标题（≤20字）", "subtitle": "", "tags": [] },',
        '  "beats": [',
        '    { "zh": "单句中文字幕", "en": "英文翻译（可空串）", "chapter": 整数或null }',
        "  ]",
        "}",
        "",
        "【beats 要求】",
        "- 把文章正文切成单句字幕，每句 10-30 字，朗朗上口、可朗读；",
        "- 全篇 30-80 条；不要把整段塞进一条；",
        "- 每个章节的首条 beat 标 chapter 编号（1..N），其余 beat 的 chapter 写 null；",
        "- 不要输出 scene 字段，也不要输出 scenes —— 画面切分交给下游分镜环节；",
        "- 只能改写、压缩、重组文章信息，不得编造文章未出现的人物 / 数据 / 平台。",
        "",
        "== 文章 ==",
        draft_md,
        "== 文章结束 ==",
    ])
    return system_prompt, user_prompt


def _load_template_episode(pipeline_id: str = "paper_card_talk_015") -> dict[str, Any]:
    """Read the template episode skeleton and keep render/audio/image config."""
    tpl = (
        _template_dir("paper_card_talk_015")
        / ".015-draft-assets" / "episode.json"
    )
    return json.loads(tpl.read_text(encoding="utf-8"))
