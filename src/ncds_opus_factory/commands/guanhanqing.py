"""/guanhanqing - 关汉卿：导演计划 worker。

职责边界：
- 不写剧本：输入已经完成的口播稿。
- 不生成素材、不渲染 HTML、不调用数字人链路。
- 只输出可被模板层消费的 director_plan.json。

默认用 agy 做导演大脑，失败时降级到 deepseek-v4-pro；确定性规则版保留为最终兜底和
测试基线。无论哪种大脑，输出都必须通过同一份 director_plan.v1 校验。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

from ncds_opus_factory.commands.horizontal_video_layouts import (
    CANVAS_HEIGHT as LANDSCAPE_CANVAS_HEIGHT,
    CANVAS_WIDTH as LANDSCAPE_CANVAS_WIDTH,
    choose_landscape_layout_id,
    get_horizontal_video_layout_library,
    get_layout,
)
from ncds_opus_factory.common.agy_cli import call_agy
from ncds_opus_factory.common.deepseek_cli import call_deepseek

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT_ROOT = ROOT / "state" / "director_jobs"
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("NOF_GUANHANQING_TIMEOUT", "900"))
DEFAULT_SELF_REPAIR_ATTEMPTS = int(os.getenv("NOF_GUANHANQING_SELF_REPAIR_ATTEMPTS", "1"))

DEFAULT_FPS = 30
DEFAULT_MAX_SUBTITLE_CHARS = 18
CHARS_PER_SECOND = 5.8
DEFAULT_ORIENTATION = "portrait"

DIGITAL_HUMAN_MODES = ("full_screen", "bust_top_half", "head_corner")
VISUAL_BLOCK_TYPES = {
    "title_card",
    "metric_card",
    "comparison_bar",
    "process_map",
    "keyword_card",
    "timeline_card",
}
ORIENTATIONS = ("portrait", "landscape")
ORIENTATION_SPECS: dict[str, dict[str, Any]] = {
    "portrait": {
        "width": 720,
        "height": 1280,
        "aspect_ratio": "9:16",
        "layout_policy": "portrait_vertical_stack",
        "safe_areas": {
            "subtitle_bottom": {"x": 22, "y": 1032, "w": 676, "h": 132},
            "avatar_right_bottom": {"x": 500, "y": 790, "w": 190, "h": 220},
            "host_top_half": {"x": 0, "y": 0, "w": 720, "h": 690},
            "content_main": {"x": 44, "y": 128, "w": 632, "h": 820},
        },
        "compositions": {
            "full_screen": "全景全屏",
            "bust_top_half": "上半身上半屏",
            "head_corner": "右下角圆形头像",
        },
        "layout_slots": {
            "full_screen": "full_screen",
            "bust_top_half": "host_top_half",
            "head_corner": "avatar_right_bottom",
        },
        "rendering_notes": [
            "竖屏优先纵向信息流，主内容在 content_main 内上下推进。",
            "full_screen 只在自然段切换和关键结论处独占画面。",
            "bust_top_half 占上半屏，下半屏承接卡片、数字和流程图。",
            "head_corner 固定右下角圆形头像，鼻子居中，不压单行字幕。",
        ],
    },
    "landscape": {
        "width": LANDSCAPE_CANVAS_WIDTH,
        "height": LANDSCAPE_CANVAS_HEIGHT,
        "aspect_ratio": "16:9",
        "layout_policy": "landscape_split_stage",
        "safe_areas": {
            "subtitle_bottom": {"x": 260, "y": 910, "w": 1400, "h": 120},
            "avatar_right_bottom": {"x": 1650, "y": 730, "w": 220, "h": 250},
            "host_left_panel": {"x": 0, "y": 0, "w": 920, "h": 1080},
            "host_right_panel": {"x": 1000, "y": 0, "w": 920, "h": 1080},
            "host_bust_left": {"x": 0, "y": 0, "w": 680, "h": 900},
            "host_bust_right": {"x": 1240, "y": 0, "w": 680, "h": 900},
            "content_main": {"x": 110, "y": 96, "w": 1500, "h": 730},
        },
        "compositions": {
            "full_screen": "横屏全景半屏主讲区",
            "bust_top_half": "横屏上半身分栏主讲区",
            "head_corner": "右下角圆形头像",
        },
        "layout_slots": {
            "full_screen": "host_left_panel",
            "bust_top_half": "host_bust_left",
            "head_corner": "avatar_right_bottom",
        },
        "rendering_notes": [
            "横屏优先 motion presentation：每个分镜是一页 1920x1080 可播放幻灯片。",
            "full_screen 在横屏里表示全景主播半屏舞台，最多占 50% 宽度，另一半留给内容。",
            "bust_top_half 使用左/右分栏主讲区，按内容类型切换主播侧。",
            "head_corner 用于信息密集段，头像靠右下但避开 subtitle_bottom。",
            "卡片和图表横向排布，减少竖屏式上下堆叠；所有元素来自 layout_library 槽位。",
        ],
    },
}

ProgressFn = Callable[[str], None]
BrainKind = str
OrientationKind = str


def _noop(_text: str) -> None:
    return None


@dataclass(frozen=True)
class ScriptDoc:
    title: str
    body: str
    paragraphs: list[str]


@dataclass(frozen=True)
class CueDraft:
    text: str
    paragraph_index: int


@dataclass(frozen=True)
class TimedCue:
    start: float
    end: float
    text: str
    paragraph_index: int


def _build_job_id() -> str:
    return f"GHQ_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


def _orientation_spec(orientation: OrientationKind) -> dict[str, Any]:
    if orientation not in ORIENTATION_SPECS:
        raise ValueError("orientation 只能是 portrait / landscape")
    return ORIENTATION_SPECS[orientation]


def _canvas_for_orientation(orientation: OrientationKind) -> dict[str, Any]:
    spec = _orientation_spec(orientation)
    return {
        "width": spec["width"],
        "height": spec["height"],
        "fps": DEFAULT_FPS,
        "aspect_ratio": spec["aspect_ratio"],
        "safe_areas": spec["safe_areas"],
    }


def _design_script_for_orientation(orientation: OrientationKind) -> dict[str, Any]:
    spec = _orientation_spec(orientation)
    design_script = {
        "orientation": orientation,
        "layout_policy": spec["layout_policy"],
        "mode_to_slot": spec["layout_slots"],
        "mode_to_composition": spec["compositions"],
        "rendering_notes": spec["rendering_notes"],
    }
    if orientation == "landscape":
        library = get_horizontal_video_layout_library()
        design_script["layout_library"] = {
            "library_id": library["library_id"],
            "schema_version": library["schema_version"],
            "canvas": library["canvas"],
            "layout_ids": [layout["layout_id"] for layout in library["layouts"]],
            "principles": library["principles"],
        }
    return design_script


def _read_docx_text(path: Path) -> str:
    """用 stdlib 读取 docx 文本，避免给 worker 增加 python-docx 依赖。"""
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for p in root.findall(".//w:p", ns):
        parts = [node.text or "" for node in p.findall(".//w:t", ns)]
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def read_script(script_path: str | Path | None = None, script_text: str | None = None) -> ScriptDoc:
    """读取口播稿，返回标题、正文、自然段。

    第一行很短时视为标题；否则整篇都视为正文。支持纯文本/Markdown/docx。
    """
    if script_text is not None:
        raw = script_text
    elif script_path is not None:
        path = Path(script_path)
        if path.suffix.lower() == ".docx":
            raw = _read_docx_text(path)
        else:
            raw = path.read_text(encoding="utf-8")
    else:
        raise ValueError("需要 script_path 或 script_text 之一")

    lines = [ln.strip() for ln in raw.replace("\r\n", "\n").splitlines()]
    nonempty = [ln for ln in lines if ln]
    if not nonempty:
        raise ValueError("脚本为空")

    title = ""
    body_lines = nonempty
    first = nonempty[0].lstrip("#").strip()
    if len(_normalize(first)) <= 24 and len(nonempty) >= 2:
        title = first
        body_lines = nonempty[1:]

    paragraphs = [ln for ln in body_lines if ln and not ln.startswith("#")]
    body = "\n".join(paragraphs).strip()
    if not body:
        raise ValueError("脚本正文为空")
    return ScriptDoc(title=title, body=body, paragraphs=paragraphs)


_PUNCT_NORMALIZE_RE = re.compile(r"[\s，。、；：！？“”‘’（）()\[\]【】—…·.,!?;:\"'《》<>-]+")
_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")
_SOFT_SPLIT_RE = re.compile(r"(?<=[，,、：:])")
_NUMBER_RE = re.compile(r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万亿两几]+)(?:[%％]|元|块|毛|家|杯|斤|公斤|公里|吨|年|万|亿|成)?")
_DISPLAY_METRIC_RE = re.compile(
    r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万亿两几]+)"
    r"(?:[%％]|元|块|毛|家|杯|斤|公斤|公里|吨|年|万|亿|成|倍|半|分之一|多|千|千万)"
)
_WEAK_METRIC_ITEMS = {"一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "一杯", "一家", "一年", "四块"}


def _normalize(text: str) -> str:
    return _PUNCT_NORMALIZE_RE.sub("", text or "")


def _clean_subtitle_text(text: str) -> str:
    return re.sub(r"\s+", "", text).strip("，。；：、,.!?！？")


def _split_long_text(text: str, max_chars: int) -> list[str]:
    text = _clean_subtitle_text(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    for part in _SOFT_SPLIT_RE.split(text):
        part = _clean_subtitle_text(part)
        if not part:
            continue
        if len(part) <= max_chars:
            pieces.append(part)
            continue
        for i in range(0, len(part), max_chars):
            pieces.append(part[i:i + max_chars])
    return pieces


def build_cue_drafts(paragraphs: list[str], max_chars: int = DEFAULT_MAX_SUBTITLE_CHARS) -> list[CueDraft]:
    cues: list[CueDraft] = []
    for pi, paragraph in enumerate(paragraphs):
        for sentence in _SPLIT_RE.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            for piece in _split_long_text(sentence, max_chars):
                cues.append(CueDraft(text=piece, paragraph_index=pi))
    return cues


def _cue_weight(text: str) -> float:
    return max(4.0, float(len(_normalize(text))))


def estimate_duration_seconds(cues: list[CueDraft]) -> float:
    return round(sum(_cue_weight(c.text) for c in cues) / CHARS_PER_SECOND + len(cues) * 0.08, 3)


def assign_cue_timing(cues: list[CueDraft], duration_seconds: float | None = None) -> list[TimedCue]:
    if not cues:
        raise ValueError("没有可用字幕 cue")
    duration = float(duration_seconds) if duration_seconds else estimate_duration_seconds(cues)
    weights = [_cue_weight(c.text) for c in cues]
    total = sum(weights)

    timed: list[TimedCue] = []
    cursor = 0.0
    for i, cue in enumerate(cues):
        if i == len(cues) - 1:
            end = duration
        else:
            end = cursor + duration * weights[i] / total
        timed.append(TimedCue(
            start=round(cursor, 3),
            end=round(max(end, cursor + 0.2), 3),
            text=cue.text,
            paragraph_index=cue.paragraph_index,
        ))
        cursor = end
    return timed


def _paragraph_groups(cues: list[TimedCue]) -> list[list[TimedCue]]:
    groups: list[list[TimedCue]] = []
    current_idx: int | None = None
    for cue in cues:
        if current_idx != cue.paragraph_index:
            groups.append([])
            current_idx = cue.paragraph_index
        groups[-1].append(cue)
    return groups


def _time_groups(cues: list[TimedCue], target_count: int) -> list[list[TimedCue]]:
    groups: list[list[TimedCue]] = []
    target_count = max(1, target_count)
    per_group = max(1, math.ceil(len(cues) / target_count))
    for i in range(0, len(cues), per_group):
        groups.append(cues[i:i + per_group])
    return groups


def group_cues_into_scenes(cues: list[TimedCue], duration_seconds: float) -> list[list[TimedCue]]:
    paragraph_groups = [g for g in _paragraph_groups(cues) if g]
    if 2 <= len(paragraph_groups) <= 8:
        return paragraph_groups
    target = min(8, max(3, round(duration_seconds / 16)))
    return _time_groups(cues, target)


def _scene_text(cues: list[TimedCue]) -> str:
    return "".join(c.text for c in cues)


def _scene_kicker_title(text: str, index: int, total: int) -> tuple[str, str]:
    if index == 0:
        return "开场钩子", "为什么值得看？"
    if index == total - 1:
        return "最终答案", "把结论钉住"
    if any(k in text for k in ("采购", "产地", "柠檬", "安岳")):
        return "产地采购", "拿到源头价格"
    if any(k in text for k in ("工厂", "生产", "代工", "中间商")):
        return "自建工厂", "跳过中间环节"
    if any(k in text for k in ("物流", "冷链", "仓库", "车队")):
        return "履约系统", "把交付成本压低"
    if any(k in text for k in ("越南", "印尼", "美国", "洛杉矶", "海外", "全球")):
        return "海外验证", "低价模式出海"
    if any(k in text for k in ("加盟", "区域保护", "高密度", "路口", "复购")):
        return "加盟密度", "用规模换复购"
    if any(k in text for k in ("雪王", "IP", "神曲", "联名", "表情包", "高级感")):
        return "品牌钩子", "低价也能有记忆点"
    if any(k in text for k in ("成本", "两毛", "便宜", "价格")):
        compact = _normalize(text)
        return "成本模型", compact[:12] or "把成本拆开"
    compact = _normalize(text)
    return "关键段落", compact[:12] or f"段落{index + 1}"


def _scene_purpose(index: int, total: int, text: str) -> str:
    if index == 0:
        return "hook"
    if index == total - 1:
        return "close"
    if any(k in text for k in ("不是", "凭什么", "但", "却", "只")):
        return "reveal"
    return "body"


def _keywords(text: str, max_n: int = 6) -> list[str]:
    candidates = [
        "成本", "供应链", "采购", "产地", "工厂", "冷链", "物流", "中间商",
        "利润", "门店", "价格", "自营", "自建", "规模", "品牌",
    ]
    out = [kw for kw in candidates if kw in text]
    for num in _NUMBER_RE.findall(text):
        if num and num not in out:
            out.append(num)
        if len(out) >= max_n:
            break
    return out[:max_n]


def _metrics(text: str) -> list[str]:
    seen: list[str] = []
    for match in _NUMBER_RE.findall(text):
        compact = _normalize(match)
        if compact in _WEAK_METRIC_ITEMS:
            continue
        if len(compact) <= 2 and not re.search(r"\d|%|％", compact):
            continue
        if match and match not in seen:
            seen.append(match)
        if len(seen) >= 4:
            break
    return seen


def _visual_blocks(scene_id: str, text: str, title: str, purpose: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{
        "id": f"{scene_id}_title",
        "type": "title_card",
        "priority": 1,
        "content": {"title": title, "keywords": _keywords(text, 4)},
        "motion": {"enter": "slide_up", "emphasis": "scale_pulse" if purpose in {"hook", "reveal"} else "none"},
    }]

    metrics = _metrics(text)
    if metrics:
        blocks.append({
            "id": f"{scene_id}_metrics",
            "type": "metric_card",
            "priority": 2,
            "content": {"items": metrics},
            "motion": {"enter": "pop", "stagger": 0.12},
        })
    if any(k in text for k in ("比", "不到", "更低", "便宜", "十几", "两三", "几毛")):
        blocks.append({
            "id": f"{scene_id}_compare",
            "type": "comparison_bar",
            "priority": 3,
            "content": {"left_label": "普通做法", "right_label": "主角做法"},
            "motion": {"enter": "wipe", "emphasis": "number_count"},
        })
    if any(k in text for k in ("供应链", "采购", "工厂", "冷链", "物流", "仓库", "门店")):
        nodes = _keywords(text, 5)
        if len(nodes) < 2:
            nodes = [title, "源头成本", "规模化执行"]
        blocks.append({
            "id": f"{scene_id}_process",
            "type": "process_map",
            "priority": 4,
            "content": {"nodes": nodes},
            "motion": {"enter": "draw_path", "emphasis": "node_hit"},
        })
    if len(blocks) == 1:
        blocks.append({
            "id": f"{scene_id}_keywords",
            "type": "keyword_card",
            "priority": 2,
            "content": {"items": _keywords(text, 5) or [title]},
            "motion": {"enter": "fade_up", "emphasis": "keyword_flash"},
        })
    return blocks


def _visual_block_slot(block_type: str, orientation: OrientationKind) -> str:
    if orientation == "landscape":
        return "content_main"
    if block_type == "title_card":
        return "content_main"
    if block_type in {"metric_card", "comparison_bar", "process_map"}:
        return "content_main"
    return "content_main"


def _visual_block_variant(block_type: str, orientation: OrientationKind) -> str:
    variants = {
        "title_card": "wide_title" if orientation == "landscape" else "stacked_title",
        "metric_card": "metric_row" if orientation == "landscape" else "metric_stack",
        "comparison_bar": "horizontal_compare" if orientation == "landscape" else "vertical_compare",
        "process_map": "left_to_right_flow" if orientation == "landscape" else "top_to_bottom_flow",
        "keyword_card": "keyword_grid" if orientation == "landscape" else "keyword_stack",
        "timeline_card": "horizontal_timeline" if orientation == "landscape" else "vertical_timeline",
    }
    return variants.get(block_type, "default")


def _enrich_visual_blocks_for_scene(scene: dict[str, Any], *, orientation: OrientationKind) -> None:
    """给 renderer 补齐稳定的布局/时间提示，避免 HTML 层猜测。"""
    blocks = scene.get("visual_blocks") or []
    if not isinstance(blocks, list):
        return
    scene_start = float(scene.get("start", 0.0))
    scene_end = float(scene.get("end", scene_start))
    scene_duration = max(0.2, scene_end - scene_start)
    avoid = scene.get("safe_areas", {}).get("avoid") or ["subtitle_bottom"]

    sorted_blocks = sorted(
        [block for block in blocks if isinstance(block, dict)],
        key=lambda block: int(block.get("priority") or 99),
    )
    step = min(1.2, max(0.45, scene_duration / max(4, len(sorted_blocks) + 2)))
    for idx, block in enumerate(sorted_blocks):
        btype = str(block.get("type") or "")
        start = round(min(scene_end - 0.2, scene_start + 0.35 + idx * step), 3)
        end = round(min(scene_end, max(start + 1.2, scene_start + scene_duration * 0.72)), 3)
        default_layout = {
            "slot": _visual_block_slot(btype, orientation),
            "variant": _visual_block_variant(btype, orientation),
            "avoid": avoid,
            "orientation": orientation,
        }
        existing_layout = block.get("layout_hint") if isinstance(block.get("layout_hint"), dict) else {}
        block["layout_hint"] = {**default_layout, **existing_layout}

        default_timing = {
            "anchor": "scene",
            "start": start,
            "end": end,
            "sync": "subtitle_or_audio_cue",
        }
        existing_timing = block.get("timing_hint") if isinstance(block.get("timing_hint"), dict) else {}
        block["timing_hint"] = {**default_timing, **existing_timing}


def _shot_payload(
    scene_id: str,
    shot_index: int,
    start: float,
    end: float,
    mode: str,
    purpose: str,
    orientation: OrientationKind,
) -> dict[str, Any]:
    spec = _orientation_spec(orientation)
    return {
        "id": f"{scene_id}_shot_{shot_index:02d}",
        "start": round(start, 3),
        "end": round(end, 3),
        "digital_human_mode": mode,
        "composition": spec["compositions"][mode],
        "layout_slot": spec["layout_slots"][mode],
        "purpose": purpose,
    }


def _shot_plan(
    scene_id: str,
    start: float,
    end: float,
    *,
    orientation: OrientationKind,
) -> list[dict[str, Any]]:
    duration = max(0.0, end - start)
    if duration <= 0:
        return []
    if duration <= 3.2:
        return [_shot_payload(scene_id, 1, start, end, "full_screen", "anchor", orientation)]

    full_end = start + min(4.2, max(2.4, duration * 0.22))
    bust_end = full_end + min(5.8, max(2.4, duration * 0.28))
    shots = [_shot_payload(scene_id, 1, start, min(full_end, end), "full_screen", "scene_anchor", orientation)]
    if bust_end < end - 0.5:
        shots.append(_shot_payload(scene_id, 2, full_end, bust_end, "bust_top_half", "explain", orientation))
        shots.append(_shot_payload(scene_id, 3, bust_end, end, "head_corner", "content_focus", orientation))
    else:
        shots.append(_shot_payload(scene_id, 2, full_end, end, "bust_top_half", "explain", orientation))
    return shots


def _audio_cues(scene_id: str, cues: list[TimedCue]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cue in cues:
        text = cue.text
        if _metrics(text) or any(k in text for k in ("凭什么", "不是", "只", "不到", "成本", "利润")):
            out.append({
                "id": f"{scene_id}_audio_{len(out) + 1:02d}",
                "at": round(cue.start, 3),
                "type": "hit",
                "target": text[:12],
                "intensity": "medium",
            })
        if len(out) >= 4:
            break
    return out


def _safe_areas_for_scene(shots: list[dict[str, Any]], *, orientation: OrientationKind) -> dict[str, Any]:
    modes = {shot.get("digital_human_mode") for shot in shots if isinstance(shot, dict)}
    avoid: list[str] = ["subtitle_bottom"]
    if "head_corner" in modes:
        avoid.append("avatar_right_bottom")
    if "bust_top_half" in modes:
        slot = _orientation_spec(orientation)["layout_slots"]["bust_top_half"]
        avoid.append(slot)
    return {"avoid": avoid}


def _visual_block_types(scene: dict[str, Any]) -> list[str]:
    return [
        str(block.get("type"))
        for block in scene.get("visual_blocks") or []
        if isinstance(block, dict) and block.get("type")
    ]


def _shot_at_time(shots: list[dict[str, Any]], t: float) -> dict[str, Any] | None:
    for shot in shots:
        if float(shot.get("start", 0.0)) <= t < float(shot.get("end", 0.0)):
            return shot
    return shots[-1] if shots else None


def _apply_landscape_layouts(scenes: list[dict[str, Any]]) -> None:
    """给横屏分镜绑定有限布局库，renderer 不再猜测画面结构。"""
    for scene_index, scene in enumerate(scenes):
        block_types = _visual_block_types(scene)
        purpose = str(scene.get("purpose") or "body")
        shots = [shot for shot in scene.get("digital_human_shots") or [] if isinstance(shot, dict)]

        layout_sequence: list[dict[str, Any]] = []
        for shot in shots:
            mode = str(shot.get("digital_human_mode") or "")
            layout_id = choose_landscape_layout_id(
                presenter_mode=mode,
                purpose=purpose,
                visual_block_types=block_types,
                scene_index=scene_index,
            )
            layout = get_layout(layout_id)
            shot["layout_id"] = layout_id
            shot["layout_family"] = layout["layout_family"]
            shot["presenter_frame"] = layout.get("presenter_frame")
            shot["content_slots"] = layout["content_slots"]
            shot["subtitle_frame"] = layout["subtitle_frame"]
            shot["density_budget"] = layout["density_budget"]
            layout_sequence.append({
                "shot_id": shot["id"],
                "layout_id": layout_id,
                "start": shot["start"],
                "end": shot["end"],
            })

        for block in scene.get("visual_blocks") or []:
            if not isinstance(block, dict):
                continue
            timing = block.get("timing_hint") if isinstance(block.get("timing_hint"), dict) else {}
            mid = (float(timing.get("start", scene.get("start", 0))) + float(timing.get("end", scene.get("end", 0)))) / 2
            active = _shot_at_time(shots, mid) or (shots[-1] if shots else None)
            layout_hint = block.get("layout_hint") if isinstance(block.get("layout_hint"), dict) else {}
            block["layout_hint"] = {
                **layout_hint,
                "layout_id": active.get("layout_id") if active else "landscape_head_corner_content_grid",
                "content_slot": _preferred_content_slot(str(block.get("type") or "")),
                "orientation": "landscape",
            }
        scene["layout_sequence"] = layout_sequence


def _preferred_content_slot(block_type: str) -> str:
    if block_type == "metric_card":
        return "metrics"
    if block_type == "process_map":
        return "process"
    if block_type == "title_card":
        return "headline"
    if block_type in {"comparison_bar", "keyword_card", "timeline_card"}:
        return "evidence"
    return "main"


def _frame_range(start: float, end: float, fps: int = DEFAULT_FPS) -> dict[str, Any]:
    frame_start = max(0, int(math.floor(start * fps)))
    frame_end = max(frame_start + 1, int(math.ceil(end * fps)) - 1)
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "frame_start": frame_start,
        "frame_end": frame_end,
    }


def _timeline_clip(
    clip_id: str,
    start: float,
    end: float,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clip = {"id": clip_id, **_frame_range(start, end)}
    if payload:
        clip.update(payload)
    return clip


def _active_clip_id(clips: list[dict[str, Any]], t: float) -> str | None:
    for clip in clips:
        if float(clip["start"]) <= t < float(clip["end"]):
            return str(clip["id"])
    return str(clips[-1]["id"]) if clips else None


def build_timeline(plan: dict[str, Any]) -> dict[str, Any]:
    """把 scenes/shots/subtitles 规整成 renderer 可直接播放的时间轴。"""
    duration = float(plan.get("source", {}).get("duration_seconds") or 0.0)
    fps = int(plan.get("canvas", {}).get("fps") or DEFAULT_FPS)
    tracks: dict[str, list[dict[str, Any]]] = {
        "scenes": [],
        "presenter": [],
        "visuals": [],
        "subtitles": [],
        "audio_cues": [],
    }

    for scene in plan.get("scenes") or []:
        scene_id = str(scene.get("id"))
        scene_start = float(scene.get("start", 0.0))
        scene_end = float(scene.get("end", scene_start))
        tracks["scenes"].append(_timeline_clip(
            scene_id,
            scene_start,
            scene_end,
            payload={
                "scene_id": scene_id,
                "purpose": scene.get("purpose"),
                "layout_sequence": scene.get("layout_sequence") or [],
            },
        ))
        for shot in scene.get("digital_human_shots") or []:
            tracks["presenter"].append(_timeline_clip(
                str(shot.get("id")),
                float(shot.get("start", scene_start)),
                float(shot.get("end", scene_end)),
                payload={
                    "scene_id": scene_id,
                    "shot_id": shot.get("id"),
                    "digital_human_mode": shot.get("digital_human_mode"),
                    "layout_id": shot.get("layout_id"),
                    "layout_slot": shot.get("layout_slot"),
                    "presenter_frame": shot.get("presenter_frame"),
                    "content_slots": shot.get("content_slots"),
                    "subtitle_frame": shot.get("subtitle_frame"),
                },
            ))
        for block in scene.get("visual_blocks") or []:
            timing = block.get("timing_hint") if isinstance(block.get("timing_hint"), dict) else {}
            start = float(timing.get("start", scene_start))
            end = float(timing.get("end", scene_end))
            tracks["visuals"].append(_timeline_clip(
                str(block.get("id")),
                start,
                end,
                payload={
                    "scene_id": scene_id,
                    "block_id": block.get("id"),
                    "block_type": block.get("type"),
                    "layout_hint": block.get("layout_hint"),
                },
            ))
        for cue_index, cue in enumerate(scene.get("subtitle_cues") or []):
            tracks["subtitles"].append(_timeline_clip(
                f"{scene_id}_subtitle_{cue_index + 1:02d}",
                float(cue.get("start", scene_start)),
                float(cue.get("end", scene_end)),
                payload={"scene_id": scene_id, "text": cue.get("text")},
            ))
        for cue in scene.get("audio_cues") or []:
            at = float(cue.get("at", scene_start))
            tracks["audio_cues"].append(_timeline_clip(
                str(cue.get("id") or f"{scene_id}_audio"),
                at,
                min(scene_end, at + 0.25),
                payload={
                    "scene_id": scene_id,
                    "type": cue.get("type"),
                    "target": cue.get("target"),
                    "intensity": cue.get("intensity"),
                },
            ))

    total_frames = max(1, int(math.ceil(duration * fps)))
    second_count = max(1, int(math.ceil(duration)))
    second_map: list[dict[str, Any]] = []
    for second in range(second_count):
        start = float(second)
        end = min(duration, second + 1.0)
        probe_t = min(duration - 0.001, start + 0.001) if duration > 0 else start
        second_map.append({
            "second": second,
            "start": round(start, 3),
            "end": round(end, 3),
            "frame_start": second * fps,
            "frame_end": min(total_frames - 1, (second + 1) * fps - 1),
            "scene_clip": _active_clip_id(tracks["scenes"], probe_t),
            "presenter_clip": _active_clip_id(tracks["presenter"], probe_t),
            "visual_clips": [
                str(clip["id"])
                for clip in tracks["visuals"]
                if float(clip["start"]) <= probe_t < float(clip["end"])
            ],
            "subtitle_clip": _active_clip_id(tracks["subtitles"], probe_t),
        })

    return {
        "schema_version": "timeline.v1",
        "timebase": "seconds_and_frames",
        "fps": fps,
        "duration_seconds": round(duration, 3),
        "total_frames": total_frames,
        "tracks": tracks,
        "second_map": second_map,
    }


def _finalize_plan_layout_and_timeline(plan: dict[str, Any]) -> None:
    orientation = plan.get("source", {}).get("orientation", DEFAULT_ORIENTATION)
    if orientation == "landscape":
        _apply_landscape_layouts(plan.get("scenes") or [])
        plan["layout_library"] = get_horizontal_video_layout_library()
    plan["timeline"] = build_timeline(plan)


def build_director_plan(
    doc: ScriptDoc,
    *,
    duration_seconds: float | None = None,
    max_subtitle_chars: int = DEFAULT_MAX_SUBTITLE_CHARS,
    style: str = "information_dense",
    orientation: OrientationKind = DEFAULT_ORIENTATION,
) -> dict[str, Any]:
    canvas = _canvas_for_orientation(orientation)
    design_script = _design_script_for_orientation(orientation)
    cue_drafts = build_cue_drafts(doc.paragraphs, max_subtitle_chars)
    timed_cues = assign_cue_timing(cue_drafts, duration_seconds)
    duration = timed_cues[-1].end
    scene_groups = group_cues_into_scenes(timed_cues, duration)

    scenes: list[dict[str, Any]] = []
    for i, group in enumerate(scene_groups):
        scene_id = f"scene_{i + 1:03d}"
        start, end = group[0].start, group[-1].end
        text = _scene_text(group)
        kicker, title = _scene_kicker_title(text, i, len(scene_groups))
        purpose = _scene_purpose(i, len(scene_groups), text)
        shots = _shot_plan(scene_id, start, end, orientation=orientation)
        scene = {
            "id": scene_id,
            "start": start,
            "end": end,
            "purpose": purpose,
            "kicker": kicker,
            "title": title,
            "summary": text[:60],
            "keywords": _keywords(text),
            "digital_human_shots": shots,
            "subtitle_cues": [
                {"start": cue.start, "end": cue.end, "text": cue.text}
                for cue in group
            ],
            "visual_blocks": _visual_blocks(scene_id, text, title, purpose),
            "motion": {
                "scene_enter": "cut" if i == 0 else "push_up",
                "pace": "fast" if len(group) >= 6 else "medium",
                "keyword_emphasis": "scale_flash",
            },
            "audio_cues": _audio_cues(scene_id, group),
            "safe_areas": _safe_areas_for_scene(shots, orientation=orientation),
        }
        _enrich_visual_blocks_for_scene(scene, orientation=orientation)
        scenes.append(scene)

    plan: dict[str, Any] = {
        "schema_version": "director_plan.v1",
        "agent": {
            "id": "guanhanqing",
            "name": "关汉卿",
            "role": "导演 / 分镜 / 视觉调度 worker",
        },
        "source": {
            "title": doc.title,
            "script_chars": len(_normalize(doc.body)),
            "duration_seconds": duration,
            "style": style,
            "orientation": orientation,
        },
        "canvas": canvas,
        "design_script": design_script,
        "digital_human": {
            "allowed_modes": list(DIGITAL_HUMAN_MODES),
            "mode_policy": "mutually_exclusive",
            "rules": [
                "full_screen is used for scene anchors and paragraph turns",
                "bust_top_half is used for guided explanation",
                "head_corner is used when information density should dominate",
            ],
            "orientation_policy": design_script["mode_to_composition"],
        },
        "subtitle": {
            "policy": "single_line",
            "max_chars": max_subtitle_chars,
            "line_break_allowed": False,
        },
        "scenes": scenes,
    }
    _finalize_plan_layout_and_timeline(plan)
    plan["qc"] = validate_director_plan(plan)
    plan["self_check"] = self_check_director_plan(plan)
    return plan


_DIRECTOR_SYSTEM_PROMPT = """你是关汉卿，一个短视频导演计划 worker。
你不改写口播稿，不生成 HTML，不渲染视频。你的唯一产物是 director_plan.v1 JSON。
你要把给定脚本、时间骨架和约束，整理成可执行的导演计划：场景、数字人模式、字幕、视觉块、动效、音效 cue 与安全区。
"""


def _json_for_prompt(data: Any, max_chars: int = 18000) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...TRUNCATED..."


def _build_director_prompt(doc: ScriptDoc, base_plan: dict[str, Any]) -> str:
    orientation = base_plan.get("source", {}).get("orientation", DEFAULT_ORIENTATION)
    canvas = base_plan.get("canvas", {})
    return (
        "请基于【口播稿】和【规则骨架】输出完整 director_plan.v1 JSON 对象。\n\n"
        "硬规则：\n"
        "1. 只能输出 JSON 对象，不要 Markdown，不要解释。\n"
        "2. 顶层必须包含 schema_version、agent、source、canvas、design_script、digital_human、subtitle、scenes。\n"
        "3. digital_human_shots 的 digital_human_mode 只能是 full_screen / bust_top_half / head_corner。\n"
        "4. 三种数字人模式在同一时间内必须互斥，不允许小全身居中、不允许右下角半身卡片。\n"
        "5. subtitle_cues 必须单行，text 不得包含换行，长度不得超过 subtitle.max_chars。\n"
        "6. scenes 和 shots 时间段必须递增、不重叠，且 subtitle_cues 必须落在所在 scene 内。\n"
        "7. visual_blocks.type 只能使用 title_card / metric_card / comparison_bar / process_map / keyword_card / timeline_card。\n"
        "8. 必须保留规则骨架中的 orientation、canvas、safe_areas、layout_library 和 timeline 语义。\n"
        "9. 可以优化 title、kicker、summary、keywords、visual_blocks、motion、audio_cues；除非必要，不要大幅改动字幕时间。\n"
        "10. timeline 由系统规整生成；你可以保留或粗略修改，但最终必须能按 start/end/frame_start/frame_end 播放。\n\n"
        "画幅约束：\n"
        f"- orientation: {orientation}\n"
        f"- canvas: {canvas.get('width')}x{canvas.get('height')} {canvas.get('aspect_ratio')}\n"
        "- portrait 使用竖屏纵向信息流：full_screen 全屏转场，bust_top_half 上半屏讲解，head_corner 右下角圆形头像。\n"
        "- landscape 使用 1920x1080 横屏布局库：full_screen 是半屏全景主播，不得超过 50% 宽度；bust_top_half 左右分栏；head_corner 右下头像。\n\n"
        "导演偏好：\n"
        "- 多用信息密度高的卡片、对比条、数字强调、流程图。\n"
        "- 关键转场用 full_screen；解释段用 bust_top_half；信息密集段用 head_corner。\n"
        "- 给关键词、数字、转折句安排 audio_cues 和 motion。\n\n"
        "自检门禁：\n"
        "- scene title/kicker 必须体现本段独特主题，不要多段重复“把成本拆开/成本模型”。\n"
        "- metric_card.items 必须是 renderer 可直接展示的指标，如“门店: 6万家”“直采价: <1元/斤”，不要输出“一”“四”“一年”这类碎片。\n"
        "- 每个 visual_block 必须具备清晰 content；layout_hint 和 timing_hint 可沿用规则骨架结构。\n"
        "- 输出前先按上述规则自检，失败就自己改到通过。\n\n"
        "【口播稿】\n"
        f"{doc.body}\n\n"
        "【规则骨架 JSON】\n"
        f"{_json_for_prompt(base_plan)}\n"
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("模型输出不是 JSON 对象")
    return data


def _metric_item_is_renderable(item: Any) -> bool:
    text = str(item or "").strip()
    compact = _normalize(text)
    if not compact or compact in _WEAK_METRIC_ITEMS:
        return False
    if len(compact) <= 2 and not re.search(r"\d|%|％", compact):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return len(compact) >= 2
    if ":" in text or "：" in text:
        return len(compact) >= 4 and bool(_DISPLAY_METRIC_RE.search(text) or re.search(r"\d|%|％", text))
    return bool(_DISPLAY_METRIC_RE.search(text)) and (len(compact) >= 3 or bool(re.search(r"\d", compact)))


def _count_values(items: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        if not item:
            continue
        counts[item] = counts.get(item, 0) + 1
    return counts


def self_check_director_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """导演计划质量门禁：面向 renderer 的可执行性，而不是 JSON 结构合法性。"""
    errors: list[str] = []
    warnings: list[str] = []
    scenes = plan.get("scenes") or []
    orientation = plan.get("source", {}).get("orientation", DEFAULT_ORIENTATION)
    safe_area_names = set((plan.get("canvas", {}).get("safe_areas") or {}).keys()) | {"full_screen"}

    titles: list[str] = []
    kickers: list[str] = []
    for scene in scenes:
        sid = str(scene.get("id") or "scene")
        title = str(scene.get("title") or "").strip()
        kicker = str(scene.get("kicker") or "").strip()
        title_key = _normalize(title)
        kicker_key = _normalize(kicker)
        titles.append(title_key)
        kickers.append(kicker_key)

        if not title_key:
            errors.append(f"{sid}: title is empty")
        if not kicker_key:
            warnings.append(f"{sid}: kicker is empty")
        if len(_normalize(str(scene.get("summary") or ""))) < 8:
            warnings.append(f"{sid}: summary too short")

        start = float(scene.get("start", 0.0))
        end = float(scene.get("end", start))
        if orientation == "landscape":
            for shot in scene.get("digital_human_shots") or []:
                if not isinstance(shot, dict):
                    continue
                layout_id = str(shot.get("layout_id") or "")
                if not layout_id:
                    errors.append(f"{sid}/{shot.get('id')}: missing layout_id")
                    continue
                try:
                    layout = get_layout(layout_id)
                except KeyError:
                    errors.append(f"{sid}/{shot.get('id')}: unknown layout_id={layout_id}")
                    continue
                if shot.get("digital_human_mode") == "full_screen":
                    presenter_frame = layout.get("presenter_frame") or {}
                    if int(presenter_frame.get("w", 9999)) > LANDSCAPE_CANVAS_WIDTH // 2:
                        errors.append(f"{sid}/{shot.get('id')}: full_screen presenter exceeds half width")

        blocks = scene.get("visual_blocks") or []
        if not isinstance(blocks, list) or not blocks:
            errors.append(f"{sid}: missing visual_blocks")
            continue

        for block in blocks:
            if not isinstance(block, dict):
                errors.append(f"{sid}: visual block is not object")
                continue
            bid = str(block.get("id") or "block")
            btype = str(block.get("type") or "")
            content = block.get("content") if isinstance(block.get("content"), dict) else {}

            layout = block.get("layout_hint") if isinstance(block.get("layout_hint"), dict) else {}
            timing = block.get("timing_hint") if isinstance(block.get("timing_hint"), dict) else {}
            slot = str(layout.get("slot") or "")
            if not slot:
                errors.append(f"{sid}/{bid}: missing layout_hint.slot")
            elif slot not in safe_area_names:
                errors.append(f"{sid}/{bid}: unknown layout slot={slot}")
            if layout.get("orientation") and layout.get("orientation") != orientation:
                errors.append(f"{sid}/{bid}: layout orientation mismatch")
            if "variant" not in layout:
                errors.append(f"{sid}/{bid}: missing layout_hint.variant")

            try:
                ts = float(timing.get("start"))
                te = float(timing.get("end"))
            except (TypeError, ValueError):
                errors.append(f"{sid}/{bid}: missing timing_hint start/end")
            else:
                if ts < start - 0.05 or te > end + 0.05 or te <= ts:
                    errors.append(f"{sid}/{bid}: timing_hint out of scene range")

            if btype == "title_card":
                if not _normalize(str(content.get("title") or "")):
                    errors.append(f"{sid}/{bid}: title_card missing content.title")
            elif btype == "metric_card":
                items = content.get("items") if isinstance(content.get("items"), list) else []
                if not items:
                    errors.append(f"{sid}/{bid}: metric_card missing items")
                for item in items:
                    if not _metric_item_is_renderable(item):
                        errors.append(f"{sid}/{bid}: weak metric item={str(item)[:20]}")
            elif btype == "comparison_bar":
                left = _normalize(str(content.get("left_label") or ""))
                right = _normalize(str(content.get("right_label") or ""))
                if not left or not right or left == right:
                    errors.append(f"{sid}/{bid}: comparison_bar labels invalid")
            elif btype == "process_map":
                nodes = content.get("nodes") if isinstance(content.get("nodes"), list) else []
                nodes = [str(node).strip() for node in nodes if str(node).strip()]
                if len(nodes) < 2:
                    errors.append(f"{sid}/{bid}: process_map needs at least two nodes")

    scene_count = len(scenes)
    max_title_repeat = max(2, scene_count // 2)
    for title, count in _count_values(titles).items():
        if count > max_title_repeat:
            errors.append(f"scene title repeated too often({count}): {title[:16]}")
    max_kicker_repeat = max(3, scene_count // 2)
    for kicker, count in _count_values(kickers).items():
        if count > max_kicker_repeat:
            errors.append(f"scene kicker repeated too often({count}): {kicker[:16]}")

    timeline = plan.get("timeline") if isinstance(plan.get("timeline"), dict) else {}
    if not timeline:
        errors.append("missing timeline")
    else:
        if timeline.get("schema_version") != "timeline.v1":
            errors.append("timeline schema_version invalid")
        fps = int(timeline.get("fps") or 0)
        if fps <= 0:
            errors.append("timeline fps invalid")
        tracks = timeline.get("tracks") if isinstance(timeline.get("tracks"), dict) else {}
        for track_name in ("scenes", "presenter", "visuals", "subtitles"):
            clips = tracks.get(track_name)
            if not isinstance(clips, list) or not clips:
                errors.append(f"timeline missing track={track_name}")
                continue
            previous_start = -1
            for clip in clips:
                try:
                    frame_start = int(clip.get("frame_start"))
                    frame_end = int(clip.get("frame_end"))
                    start = float(clip.get("start"))
                    end = float(clip.get("end"))
                except (TypeError, ValueError):
                    errors.append(f"timeline/{track_name}: invalid clip range")
                    continue
                if frame_end < frame_start or end <= start:
                    errors.append(f"timeline/{track_name}/{clip.get('id')}: invalid range")
                if track_name in {"scenes", "presenter"} and frame_start < previous_start:
                    errors.append(f"timeline/{track_name}/{clip.get('id')}: out of order")
                previous_start = frame_start

    return {
        "verdict": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "scene_count": scene_count,
    }


def _normalize_model_plan(model_plan: dict[str, Any], base_plan: dict[str, Any], brain: str) -> dict[str, Any]:
    """把模型输出规整成完整 director_plan.v1，缺失的基础约束从规则骨架继承。"""
    if not isinstance(model_plan.get("scenes"), list) or not model_plan["scenes"]:
        raise ValueError("模型输出缺 scenes")

    plan = dict(base_plan)
    for key in ("schema_version", "agent", "source", "design_script", "digital_human", "subtitle", "scenes"):
        if key in model_plan:
            plan[key] = model_plan[key]

    plan["schema_version"] = "director_plan.v1"
    plan["agent"] = {
        **base_plan["agent"],
        **(plan.get("agent") if isinstance(plan.get("agent"), dict) else {}),
        "id": "guanhanqing",
        "name": "关汉卿",
    }
    plan["source"] = {
        **base_plan["source"],
        **(plan.get("source") if isinstance(plan.get("source"), dict) else {}),
    }
    plan["source"]["orientation"] = base_plan["source"].get("orientation", DEFAULT_ORIENTATION)
    plan["canvas"] = base_plan["canvas"]
    plan["design_script"] = {
        **base_plan["design_script"],
        **(plan.get("design_script") if isinstance(plan.get("design_script"), dict) else {}),
    }
    plan["design_script"]["orientation"] = plan["source"]["orientation"]
    for key in ("layout_policy", "mode_to_slot", "mode_to_composition"):
        plan["design_script"][key] = base_plan["design_script"][key]
    plan["digital_human"] = {
        **base_plan["digital_human"],
        **(plan.get("digital_human") if isinstance(plan.get("digital_human"), dict) else {}),
    }
    plan["digital_human"]["allowed_modes"] = list(DIGITAL_HUMAN_MODES)
    plan["digital_human"]["mode_policy"] = "mutually_exclusive"
    plan["subtitle"] = {
        **base_plan["subtitle"],
        **(plan.get("subtitle") if isinstance(plan.get("subtitle"), dict) else {}),
        "policy": "single_line",
        "line_break_allowed": False,
    }
    orientation = plan["source"]["orientation"]
    spec = _orientation_spec(orientation)
    for scene in plan.get("scenes") or []:
        shots = scene.get("digital_human_shots") or []
        for shot in shots:
            mode = shot.get("digital_human_mode")
            if mode in DIGITAL_HUMAN_MODES:
                shot["composition"] = spec["compositions"][mode]
                shot["layout_slot"] = spec["layout_slots"][mode]
        scene["safe_areas"] = _safe_areas_for_scene(shots, orientation=orientation)
        _enrich_visual_blocks_for_scene(scene, orientation=orientation)
    _finalize_plan_layout_and_timeline(plan)
    plan["generation"] = {
        "brain": brain,
        "fallback": False,
    }
    plan["qc"] = validate_director_plan(plan)
    if plan["qc"]["verdict"] != "pass":
        raise ValueError("模型导演计划未通过校验: " + "; ".join(plan["qc"]["errors"][:5]))
    plan["self_check"] = self_check_director_plan(plan)
    return plan


def _call_director_brain(
    brain: str,
    prompt: str,
    *,
    timeout_seconds: int,
) -> str:
    if brain == "agy":
        return call_agy(_DIRECTOR_SYSTEM_PROMPT + "\n\n" + prompt, timeout_seconds=timeout_seconds)
    if brain == "deepseek":
        return call_deepseek(
            prompt,
            system_prompt=_DIRECTOR_SYSTEM_PROMPT,
            timeout_seconds=timeout_seconds,
        )
    raise ValueError(f"未知 director brain: {brain}")


def _build_self_repair_prompt(
    doc: ScriptDoc,
    base_plan: dict[str, Any],
    failed_plan: dict[str, Any],
) -> str:
    self_check = failed_plan.get("self_check") or {}
    issues = (self_check.get("errors") or [])[:16]
    return (
        "你刚才输出的 director_plan.v1 自检未通过。"
        "这是关汉卿自己的质量门禁，请自行返工到通过。"
        "请只输出修复后的完整 JSON 对象，不要 Markdown，不要解释。\n\n"
        "必须修复以下问题：\n"
        + "\n".join(f"- {issue}" for issue in issues)
        + "\n\n"
        "返工规则：\n"
        "1. 不改口播稿，不删 subtitle_cues，不大幅改字幕时间。\n"
        "2. 必须保留 orientation、canvas、design_script、digital_human.allowed_modes。\n"
        "3. 每个 scene 的 title/kicker 要能区分段落主题，避免连续重复。\n"
        "4. metric_card.items 必须是可直接渲染的指标，不能输出“一”“四”“一年”“一杯”这类碎片。\n"
        "5. 每个 visual_block 都要让 renderer 能画：content 完整，layout_hint/timing_hint 可保留或优化。\n"
        "6. 保持 full_screen / bust_top_half / head_corner 三种数字人模式互斥。\n\n"
        "【口播稿】\n"
        f"{doc.body}\n\n"
        "【不可改变的规则骨架】\n"
        f"{_json_for_prompt(base_plan, max_chars=10000)}\n\n"
        "【需要返工的当前 JSON】\n"
        f"{_json_for_prompt(failed_plan, max_chars=22000)}\n"
    )


def build_director_plan_with_brain(
    doc: ScriptDoc,
    *,
    duration_seconds: float | None = None,
    max_subtitle_chars: int = DEFAULT_MAX_SUBTITLE_CHARS,
    style: str = "information_dense",
    orientation: OrientationKind = DEFAULT_ORIENTATION,
    brain: BrainKind = "auto",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """默认 agy -> deepseek，最终规则版兜底；所有输出共用同一校验器。"""
    base_plan = build_director_plan(
        doc,
        duration_seconds=duration_seconds,
        max_subtitle_chars=max_subtitle_chars,
        style=style,
        orientation=orientation,
    )
    base_plan["generation"] = {"brain": "rule", "fallback": False}
    if brain == "rule":
        return base_plan

    if brain in ("auto", "agy"):
        candidates = ["agy", "deepseek"]
    elif brain == "deepseek":
        candidates = ["deepseek"]
    else:
        raise ValueError("brain 只能是 auto / agy / deepseek / rule")

    prompt = _build_director_prompt(doc, base_plan)
    failures: list[dict[str, str]] = []
    for candidate in candidates:
        try:
            on_progress(f"关汉卿导演大脑启动: {candidate}")
            raw = _call_director_brain(candidate, prompt, timeout_seconds=timeout_seconds)
            plan = _normalize_model_plan(_extract_json_object(raw), base_plan, candidate)
            repair_attempts = 0
            while plan.get("self_check", {}).get("verdict") != "pass" and repair_attempts < DEFAULT_SELF_REPAIR_ATTEMPTS:
                repair_attempts += 1
                issues = plan.get("self_check", {}).get("errors") or []
                on_progress(f"关汉卿自检返工: {candidate} attempt={repair_attempts} errors={len(issues)}")
                repair_prompt = _build_self_repair_prompt(doc, base_plan, plan)
                raw = _call_director_brain(candidate, repair_prompt, timeout_seconds=timeout_seconds)
                plan = _normalize_model_plan(_extract_json_object(raw), base_plan, candidate)

            if plan.get("self_check", {}).get("verdict") != "pass":
                issues = plan.get("self_check", {}).get("errors") or []
                raise ValueError("自检未通过: " + "; ".join(issues[:5]))

            plan["generation"]["self_repair_attempts"] = repair_attempts
            plan["generation"]["self_check_repaired"] = repair_attempts > 0
            on_progress(f"关汉卿导演大脑通过: {candidate}")
            return plan
        except Exception as exc:  # noqa: BLE001 - 单个模型失败要自动降级
            failures.append({"brain": candidate, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
            on_progress(f"关汉卿导演大脑失败: {candidate} {type(exc).__name__}")

    base_plan["generation"] = {"brain": "rule", "fallback": True, "failures": failures}
    return base_plan


def validate_director_plan(plan: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    scenes = plan.get("scenes") or []
    allowed_modes = set(plan.get("digital_human", {}).get("allowed_modes") or DIGITAL_HUMAN_MODES)
    allowed_visual_types = VISUAL_BLOCK_TYPES
    max_chars = int(plan.get("subtitle", {}).get("max_chars") or DEFAULT_MAX_SUBTITLE_CHARS)
    orientation = plan.get("source", {}).get("orientation", DEFAULT_ORIENTATION)
    if orientation not in ORIENTATIONS:
        errors.append(f"illegal orientation={orientation}")
    else:
        spec = _orientation_spec(orientation)
        canvas = plan.get("canvas") if isinstance(plan.get("canvas"), dict) else {}
        if canvas.get("width") != spec["width"] or canvas.get("height") != spec["height"]:
            errors.append(f"canvas mismatch for orientation={orientation}")
        if canvas.get("aspect_ratio") != spec["aspect_ratio"]:
            errors.append(f"aspect_ratio mismatch for orientation={orientation}")

    prev_end = 0.0
    for si, scene in enumerate(scenes):
        sid = scene.get("id") or f"scene#{si + 1}"
        start = float(scene.get("start", 0))
        end = float(scene.get("end", 0))
        if end <= start:
            errors.append(f"{sid}: end <= start")
        if si and start < prev_end - 0.05:
            errors.append(f"{sid}: overlaps previous scene")
        prev_end = max(prev_end, end)

        shots = scene.get("digital_human_shots") or []
        shot_prev = start
        if not shots:
            errors.append(f"{sid}: missing digital_human_shots")
        for shot in shots:
            mode = shot.get("digital_human_mode")
            if mode not in allowed_modes:
                errors.append(f"{sid}: illegal digital_human_mode={mode}")
            ss, se = float(shot.get("start", 0)), float(shot.get("end", 0))
            if ss < start - 0.05 or se > end + 0.05 or se <= ss:
                errors.append(f"{sid}: invalid shot range {ss}-{se}")
            if ss < shot_prev - 0.05:
                errors.append(f"{sid}: overlapping shots")
            shot_prev = se
            if orientation == "landscape":
                layout_id = str(shot.get("layout_id") or "")
                if not layout_id:
                    errors.append(f"{sid}: missing shot layout_id")
                else:
                    try:
                        layout = get_layout(layout_id)
                    except KeyError:
                        errors.append(f"{sid}: unknown layout_id={layout_id}")
                    else:
                        expected_mode = layout.get("presenter_mode")
                        if expected_mode != mode:
                            errors.append(f"{sid}: layout {layout_id} does not match mode={mode}")
                        presenter_frame = layout.get("presenter_frame") or {}
                        if mode == "full_screen" and int(presenter_frame.get("w", 9999)) > spec["width"] // 2:
                            errors.append(f"{sid}: full_screen presenter frame exceeds half width")

        subtitles = scene.get("subtitle_cues") or []
        if not subtitles:
            warnings.append(f"{sid}: no subtitles")
        for cue in subtitles:
            text = str(cue.get("text") or "")
            if "\n" in text or "\r" in text:
                errors.append(f"{sid}: subtitle contains line break")
            if len(text) > max_chars:
                errors.append(f"{sid}: subtitle too long({len(text)}>{max_chars}): {text[:20]}")
            cs, ce = float(cue.get("start", 0)), float(cue.get("end", 0))
            if cs < start - 0.05 or ce > end + 0.05 or ce <= cs:
                errors.append(f"{sid}: invalid subtitle cue range {cs}-{ce}")

        for block in scene.get("visual_blocks") or []:
            btype = block.get("type")
            if btype not in allowed_visual_types:
                errors.append(f"{sid}: illegal visual block type={btype}")

    timeline = plan.get("timeline") if isinstance(plan.get("timeline"), dict) else None
    if not timeline:
        errors.append("missing timeline")
    else:
        if timeline.get("schema_version") != "timeline.v1":
            errors.append("timeline schema_version must be timeline.v1")
        fps = int(timeline.get("fps") or 0)
        if fps != int(plan.get("canvas", {}).get("fps") or DEFAULT_FPS):
            errors.append("timeline fps mismatch")
        expected_frames = max(1, int(math.ceil(float(plan.get("source", {}).get("duration_seconds") or 0) * max(1, fps))))
        if int(timeline.get("total_frames") or 0) != expected_frames:
            errors.append("timeline total_frames mismatch")
        tracks = timeline.get("tracks") if isinstance(timeline.get("tracks"), dict) else {}
        for track_name in ("scenes", "presenter", "visuals", "subtitles", "audio_cues"):
            if track_name not in tracks:
                errors.append(f"timeline missing track={track_name}")
                continue
            clips = tracks[track_name]
            if not isinstance(clips, list):
                errors.append(f"timeline track is not list={track_name}")
                continue
            for clip in clips:
                try:
                    frame_start = int(clip.get("frame_start"))
                    frame_end = int(clip.get("frame_end"))
                    start = float(clip.get("start"))
                    end = float(clip.get("end"))
                except (TypeError, ValueError):
                    errors.append(f"timeline/{track_name}: invalid clip range")
                    continue
                if end <= start or frame_end < frame_start:
                    errors.append(f"timeline/{track_name}/{clip.get('id')}: invalid clip range")

    return {
        "verdict": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "scene_count": len(scenes),
    }


def write_director_plan(plan: dict[str, Any], out_path: str | Path | None = None) -> Path:
    if out_path is None:
        out = DEFAULT_OUT_ROOT / _build_job_id() / "director_plan.json"
    else:
        out = Path(out_path)
        if out.suffix.lower() != ".json":
            out = out / "director_plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def run(
    script_path: str | Path | None = None,
    script_text: str | None = None,
    out_path: str | Path | None = None,
    duration_seconds: float | None = None,
    max_subtitle_chars: int = DEFAULT_MAX_SUBTITLE_CHARS,
    style: str = "information_dense",
    orientation: OrientationKind = DEFAULT_ORIENTATION,
    brain: BrainKind = "auto",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """输入口播稿，输出导演计划 JSON。"""
    doc = read_script(script_path=script_path, script_text=script_text)
    on_progress(f"guanhanqing start: chars={len(_normalize(doc.body))} orientation={orientation}")
    plan = build_director_plan_with_brain(
        doc,
        duration_seconds=duration_seconds,
        max_subtitle_chars=max_subtitle_chars,
        style=style,
        orientation=orientation,
        brain=brain,
        timeout_seconds=timeout_seconds,
        on_progress=on_progress,
    )
    out = write_director_plan(plan, out_path)
    active_brain = plan.get("generation", {}).get("brain", brain)
    on_progress(f"guanhanqing done: brain={active_brain} scenes={len(plan['scenes'])} qc={plan['qc']['verdict']}")
    return {
        "out_path": str(out),
        "plan": plan,
        "qc": plan["qc"],
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ncds_opus_factory.commands.guanhanqing",
        description="关汉卿: 输入口播稿, 输出 director_plan.json",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--script", help="口播稿路径(.txt/.md/.docx)")
    src.add_argument("--text", help="直接传入口播稿文本")
    parser.add_argument("--out", default=None, help="输出 JSON 路径或目录")
    parser.add_argument("--duration", type=float, default=None, help="目标音频/视频时长秒数")
    parser.add_argument("--max-subtitle-chars", type=int, default=DEFAULT_MAX_SUBTITLE_CHARS)
    parser.add_argument("--style", default="information_dense")
    parser.add_argument("--orientation", choices=ORIENTATIONS, default=DEFAULT_ORIENTATION)
    parser.add_argument("--brain", choices=("auto", "agy", "deepseek", "rule"), default="auto")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", flush=True)

    result = run(
        script_path=args.script,
        script_text=args.text,
        out_path=args.out,
        duration_seconds=args.duration,
        max_subtitle_chars=args.max_subtitle_chars,
        style=args.style,
        orientation=args.orientation,
        brain=args.brain,
        timeout_seconds=args.timeout,
        on_progress=on_progress,
    )
    print(json.dumps({
        "out_path": result["out_path"],
        "qc": result["qc"]["verdict"],
        "scene_count": result["qc"]["scene_count"],
        "orientation": result["plan"].get("source", {}).get("orientation"),
        "brain": result["plan"].get("generation", {}).get("brain"),
    }, ensure_ascii=False))
    return 0 if result["qc"]["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
