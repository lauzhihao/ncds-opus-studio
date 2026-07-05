"""1920x1080 横屏视频布局库。

这份库把横屏作品当作 motion presentation：导演 agent 只选模板和填槽位，
renderer 按固定 frame 渲染，避免让模型临场发明 HTML。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1080
FPS = 30


def frame(x: int, y: int, w: int, h: int) -> dict[str, int]:
    return {"x": x, "y": y, "w": w, "h": h}


SUBTITLE_FRAME = frame(260, 910, 1400, 116)

LANDSCAPE_LAYOUTS: dict[str, dict[str, Any]] = {
    "landscape_full_body_left_content_right": {
        "layout_id": "landscape_full_body_left_content_right",
        "name": "全景主播左半屏 + 右侧内容",
        "presenter_mode": "full_screen",
        "layout_family": "host-content-split",
        "use_when": "自然段开场、关键结论、需要主播建立存在感但仍要保留内容空间。",
        "presenter_frame": frame(0, 0, 920, 1080),
        "content_slots": {
            "main": frame(1000, 118, 790, 690),
            "headline": frame(1000, 96, 790, 148),
            "evidence": frame(1000, 292, 790, 430),
            "footer": frame(1000, 760, 790, 82),
        },
        "subtitle_frame": SUBTITLE_FRAME,
        "density_budget": {"level": "moderate", "max_blocks": 3, "max_text_tokens": 7},
        "motion": {"enter": "host_scale_in", "content_enter": "slide_from_right"},
    },
    "landscape_full_body_right_content_left": {
        "layout_id": "landscape_full_body_right_content_left",
        "name": "左侧内容 + 全景主播右半屏",
        "presenter_mode": "full_screen",
        "layout_family": "content-host-split",
        "use_when": "强调观点、数字或图表，同时让主播作为右侧讲解者承接。",
        "presenter_frame": frame(1000, 0, 920, 1080),
        "content_slots": {
            "main": frame(130, 118, 790, 690),
            "headline": frame(130, 96, 790, 148),
            "evidence": frame(130, 292, 790, 430),
            "footer": frame(130, 760, 790, 82),
        },
        "subtitle_frame": SUBTITLE_FRAME,
        "density_budget": {"level": "moderate", "max_blocks": 3, "max_text_tokens": 7},
        "motion": {"enter": "host_scale_in", "content_enter": "slide_from_left"},
    },
    "landscape_bust_left_metrics_right": {
        "layout_id": "landscape_bust_left_metrics_right",
        "name": "左侧上半身 + 右侧指标墙",
        "presenter_mode": "bust_top_half",
        "layout_family": "host-metric-wall",
        "use_when": "解释成本、规模、价格、百分比等数字密集信息。",
        "presenter_frame": frame(0, 0, 680, 900),
        "content_slots": {
            "main": frame(760, 110, 1000, 690),
            "headline": frame(760, 92, 1000, 128),
            "metrics": frame(760, 270, 1000, 250),
            "evidence": frame(760, 560, 1000, 210),
        },
        "subtitle_frame": SUBTITLE_FRAME,
        "density_budget": {"level": "dense", "max_blocks": 4, "max_text_tokens": 10},
        "motion": {"enter": "split_reveal", "content_enter": "metric_stagger"},
    },
    "landscape_bust_right_process_left": {
        "layout_id": "landscape_bust_right_process_left",
        "name": "左侧流程图 + 右侧上半身",
        "presenter_mode": "bust_top_half",
        "layout_family": "process-host-split",
        "use_when": "讲供应链、生产、物流、步骤、因果链。",
        "presenter_frame": frame(1240, 0, 680, 900),
        "content_slots": {
            "main": frame(130, 110, 1010, 690),
            "headline": frame(130, 92, 1010, 128),
            "process": frame(130, 286, 1010, 260),
            "evidence": frame(130, 586, 1010, 180),
        },
        "subtitle_frame": SUBTITLE_FRAME,
        "density_budget": {"level": "dense", "max_blocks": 4, "max_text_tokens": 10},
        "motion": {"enter": "split_reveal", "content_enter": "path_draw"},
    },
    "landscape_head_corner_content_grid": {
        "layout_id": "landscape_head_corner_content_grid",
        "name": "内容主画布 + 右下头像",
        "presenter_mode": "head_corner",
        "layout_family": "content-with-corner-avatar",
        "use_when": "信息密集页，主播退到角落，只保留口播陪伴感。",
        "presenter_frame": frame(1640, 690, 220, 220),
        "content_slots": {
            "main": frame(110, 96, 1500, 730),
            "headline": frame(110, 80, 1500, 130),
            "evidence": frame(110, 260, 1500, 360),
            "footer": frame(110, 660, 1260, 120),
        },
        "subtitle_frame": SUBTITLE_FRAME,
        "density_budget": {"level": "dense", "max_blocks": 5, "max_text_tokens": 13},
        "motion": {"enter": "content_first", "content_enter": "fade_up"},
    },
    "landscape_head_corner_metric_wall": {
        "layout_id": "landscape_head_corner_metric_wall",
        "name": "大数字指标墙 + 右下头像",
        "presenter_mode": "head_corner",
        "layout_family": "metric-led",
        "use_when": "连续展示 3-6 个数字、指标或对比结论。",
        "presenter_frame": frame(1640, 690, 220, 220),
        "content_slots": {
            "main": frame(110, 96, 1500, 720),
            "headline": frame(110, 76, 1500, 110),
            "metrics": frame(110, 240, 1500, 360),
            "footer": frame(110, 650, 1260, 120),
        },
        "subtitle_frame": SUBTITLE_FRAME,
        "density_budget": {"level": "dense", "max_blocks": 4, "max_text_tokens": 12},
        "motion": {"enter": "numbers_count", "content_enter": "metric_stagger"},
    },
    "landscape_content_only_two_column": {
        "layout_id": "landscape_content_only_two_column",
        "name": "纯内容双栏",
        "presenter_mode": "none",
        "layout_family": "two-column-content",
        "use_when": "主播暂退场，用两组观点/证据/对比解释复杂信息。",
        "presenter_frame": None,
        "content_slots": {
            "main": frame(120, 92, 1680, 720),
            "left": frame(120, 260, 800, 420),
            "right": frame(1000, 260, 800, 420),
            "headline": frame(120, 78, 1680, 128),
        },
        "subtitle_frame": SUBTITLE_FRAME,
        "density_budget": {"level": "dense", "max_blocks": 4, "max_text_tokens": 12},
        "motion": {"enter": "hard_cut", "content_enter": "column_reveal"},
    },
    "landscape_section_title": {
        "layout_id": "landscape_section_title",
        "name": "段落转场大标题",
        "presenter_mode": "none",
        "layout_family": "section-opener",
        "use_when": "自然段之间的转场、章节标题、强观点钉住。",
        "presenter_frame": None,
        "content_slots": {
            "main": frame(180, 210, 1560, 500),
            "headline": frame(180, 260, 1560, 220),
            "footer": frame(180, 560, 1560, 110),
        },
        "subtitle_frame": SUBTITLE_FRAME,
        "density_budget": {"level": "sparse", "max_blocks": 2, "max_text_tokens": 5},
        "motion": {"enter": "title_hit", "content_enter": "scale_fade"},
    },
}


def get_layout(layout_id: str) -> dict[str, Any]:
    if layout_id not in LANDSCAPE_LAYOUTS:
        raise KeyError(f"unknown landscape layout_id={layout_id}")
    return deepcopy(LANDSCAPE_LAYOUTS[layout_id])


def get_horizontal_video_layout_library() -> dict[str, Any]:
    return {
        "schema_version": "video_layout_library.v1",
        "library_id": "horizontal_video_1920x1080.v1",
        "canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT, "fps": FPS, "aspect_ratio": "16:9"},
        "principles": [
            "横屏按 motion presentation 处理：一页就是一个可播放分镜。",
            "全景主播在横屏里最多占 50% 宽度，另一半必须留给内容。",
            "只允许 full_screen / bust_top_half / head_corner / none 四类 presenter slot。",
            "所有字幕进入 subtitle_frame，renderer 必须保持单行不换行。",
            "agent 只能选择 layout_id 与填槽位，不生成自由 HTML。",
        ],
        "layouts": [deepcopy(layout) for layout in LANDSCAPE_LAYOUTS.values()],
    }


def choose_landscape_layout_id(
    *,
    presenter_mode: str,
    purpose: str,
    visual_block_types: list[str],
    scene_index: int,
) -> str:
    types = set(visual_block_types)
    if purpose in {"hook", "close"} and presenter_mode == "full_screen":
        return (
            "landscape_full_body_left_content_right"
            if scene_index % 2 == 0
            else "landscape_full_body_right_content_left"
        )
    if presenter_mode == "full_screen":
        return "landscape_full_body_left_content_right"
    if presenter_mode == "bust_top_half":
        if "process_map" in types:
            return "landscape_bust_right_process_left"
        return "landscape_bust_left_metrics_right"
    if presenter_mode == "head_corner":
        if "metric_card" in types:
            return "landscape_head_corner_metric_wall"
        return "landscape_head_corner_content_grid"
    if "comparison_bar" in types or "process_map" in types:
        return "landscape_content_only_two_column"
    return "landscape_section_title"


def frame_to_tuple(value: dict[str, int] | None) -> tuple[int, int, int, int] | None:
    if not value:
        return None
    return (
        int(value["x"]),
        int(value["y"]),
        int(value["x"] + value["w"]),
        int(value["y"] + value["h"]),
    )

