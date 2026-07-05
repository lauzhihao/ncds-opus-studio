#!/usr/bin/env python3
"""Standalone demo: render a shot-plan driven digital presenter template."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


CANVAS_W = 720
CANVAS_H = 1280
FPS = 25
DEFAULT_DURATION = 53.32


@dataclass(frozen=True)
class Scene:
    start: float
    end: float
    key: str
    shot_type: str
    presenter_mode: str
    presenter_anchor: str
    kicker: str
    title: str
    lines: tuple[str, ...]
    keyword: str = ""


@dataclass(frozen=True)
class Subtitle:
    start: float
    end: float
    text: str


# 这版刻意模仿样片的结构：少量大画面主播，更多素材主画面 + 小窗主播。
SCENES = (
    Scene(
        0.00,
        4.20,
        "opening",
        "host_full_keyword",
        "host_full",
        "center",
        "Presenter Layer",
        "数字人不是主画面",
        ("它负责可信口播", "内容层负责信息密度"),
        "6月25日",
    ),
    Scene(
        4.20,
        8.26,
        "thesis",
        "host_full_with_broll",
        "host_full",
        "center",
        "A-roll Contract",
        "口播只做叙事锚点",
        ("不让数字人硬撑全片", "把观点句留给大画面"),
        "Agentic Video",
    ),
    Scene(
        8.26,
        12.80,
        "engine",
        "brand_broll_pip",
        "pip_circle",
        "top_left",
        "Production Instance",
        "Web 和 App 指向同一个实例",
        ("内容视角负责修改", "决策视角负责放行"),
    ),
    Scene(
        12.80,
        16.53,
        "engine_map",
        "brand_broll_pip",
        "pip_bottom",
        "bottom_right",
        "One Engine",
        "任务ID和作品ID合一",
        ("同一条生产链", "两种观察视角"),
    ),
    Scene(
        16.53,
        21.00,
        "runtime",
        "broll_pip_circle",
        "pip_circle",
        "top_left",
        "Runtime",
        "Queue -> Worker",
        ("请求入队", "唯一执行体消费"),
    ),
    Scene(
        21.00,
        25.61,
        "worker",
        "keyword_card",
        "pip_card",
        "bottom_right",
        "Execution",
        "重启 server 不打断任务",
        ("worker 才是真正执行者", "状态通过事件落盘"),
        "唯一执行体",
    ),
    Scene(
        25.61,
        30.00,
        "progress",
        "brand_broll_pip",
        "pip_bottom",
        "bottom_right",
        "Progress",
        "on_progress(text)",
        ("命令只报告进度", "展示层自由消费"),
    ),
    Scene(
        30.00,
        35.30,
        "events",
        "broll_pip_circle",
        "pip_circle",
        "top_left",
        "Events",
        "SSE / events.jsonl / noop",
        ("同一份进度", "不同端各取所需"),
    ),
    Scene(
        35.30,
        40.50,
        "capabilities",
        "host_full_with_assets",
        "large_side",
        "right",
        "Capabilities",
        "能力不是堆在画面里",
        ("ASR / 改写 / 分镜", "配音 / 渲染 / 质检"),
    ),
    Scene(
        40.50,
        46.63,
        "agents",
        "brand_broll_pip",
        "pip_bottom",
        "bottom_right",
        "Agent Chain",
        "沈括到伯牙是一条链",
        ("采集、选题、编剧", "美术、声音、渲染"),
    ),
    Scene(
        46.63,
        50.20,
        "verdict",
        "host_full_keyword",
        "host_full",
        "center",
        "Verdict",
        "数字人适合做主持层",
        ("角落讲解为主", "关键论断再切大画面"),
        "可行",
    ),
    Scene(
        50.20,
        53.32,
        "final",
        "final_stinger",
        "large_side",
        "right",
        "Shot Plan",
        "下一步接入模板参数",
        ("presenter_source", "content_timeline", "material_tracks"),
        "SHOT PLAN",
    ),
)


SUBTITLES = (
    Subtitle(0.00, 4.20, "你好，我是 OpusStudio 的数字人讲解员。"),
    Subtitle(4.20, 8.26, "这是一条由 HeyGen 生成的口播 POC。"),
    Subtitle(8.26, 12.80, "OpusStudio 正在把内容视角的 Web 工作台，"),
    Subtitle(12.80, 16.53, "和决策视角的 App，统一到同一个生产引擎。"),
    Subtitle(16.53, 21.00, "在这个流程里，任务先进入队列，"),
    Subtitle(21.00, 25.61, "再由 worker 执行，形成稳定的生产实例。"),
    Subtitle(25.61, 30.00, "过程通过进度回调实时更新。"),
    Subtitle(30.00, 35.30, "前端专注展示状态，后端专注稳定编排。"),
    Subtitle(35.30, 40.50, "ASR、改写、分镜、配音、渲染这些能力，"),
    Subtitle(40.50, 46.63, "都可以被同一个 agent 流程调度起来。"),
    Subtitle(46.63, 53.32, "这条视频验证：数字人口播可以接入最终交付链路。"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compose a digital presenter demo video.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("outputs/heygen-poc/opus-studio-heygen-poc-captioned.mp4"),
        help="HeyGen presenter source video.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/heygen-poc/opus-studio-heygen-poc-presenter-template.mp4"),
        help="Rendered vertical social video.",
    )
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION)
    parser.add_argument("--crf", type=int, default=20)
    parser.add_argument(
        "--shot-plan-output",
        type=Path,
        default=None,
        help="Optional JSON sidecar path. Defaults to output name with .shot_plan.json.",
    )
    return parser.parse_args()


def load_font(name: str, size: int) -> ImageFont.ImageFont:
    candidates = [
        Path("/Users/liuzhihao/Library/Fonts") / name,
        Path("/System/Library/Fonts") / name,
        Path("/Library/Fonts") / name,
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


FONTS = {
    "tiny": load_font("SourceHanSansCN-Regular.otf", 19),
    "small": load_font("SourceHanSansCN-Medium.otf", 24),
    "body": load_font("SourceHanSansCN-Regular.otf", 31),
    "body_bold": load_font("SourceHanSansCN-Bold.otf", 34),
    "kicker": load_font("SourceHanSansCN-Bold.otf", 26),
    "title": load_font("SourceHanSansCN-Heavy.otf", 50),
    "headline": load_font("SourceHanSansCN-Heavy.otf", 62),
    "keyword": load_font("SourceHanSansCN-Heavy.otf", 78),
    "subtitle": load_font("SourceHanSansCN-Bold.otf", 36),
    "mono": load_font("SourceHanSansCN-Regular.otf", 25),
}


def active(items: tuple[Scene, ...] | tuple[Subtitle, ...], t: float):
    for item in items:
        if item.start <= t < item.end:
            return item
    return items[-1]


def scene_phase(scene: Scene, t: float) -> float:
    span = max(scene.end - scene.start, 0.1)
    return min(max((t - scene.start) / span, 0.0), 1.0)


def fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        test = current + char
        if draw.textlength(test, font=font) <= max_width or not current:
            current = test
            continue
        lines.append(current)
        current = char
    if current:
        lines.append(current)
    return lines


def draw_text(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    *,
    stroke: int = 0,
    anchor: str | None = None,
) -> None:
    draw.text(pos, text, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0, 220), anchor=anchor)


def rounded_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    radius: int = 18,
    fill: tuple[int, int, int, int] = (17, 24, 39, 228),
    outline: tuple[int, int, int, int] = (255, 255, 255, 38),
    width: int = 1,
) -> None:
    shadow = (box[0] + 6, box[1] + 10, box[2] + 6, box[3] + 10)
    draw.rounded_rectangle(shadow, radius=radius, fill=(0, 0, 0, 76))
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def cover_crop(img: Image.Image, box: tuple[int, int, int, int], size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(img.crop(box), size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.25))


def draw_gradient(canvas: Image.Image, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> None:
    draw = ImageDraw.Draw(canvas)
    for i in range(CANVAS_H):
        a = i / CANVAS_H
        color = tuple(int(top[idx] * (1 - a) + bottom[idx] * a) for idx in range(3))
        draw.line((0, i, CANVAS_W, i), fill=(*color, 255))


def draw_brand_header(draw: ImageDraw.ImageDraw, scene: Scene) -> None:
    draw.rounded_rectangle((32, 30, 206, 70), radius=19, fill=(17, 24, 39, 226), outline=(244, 180, 74, 130))
    draw_text(draw, (52, 38), "OpusStudio", FONTS["small"], (255, 250, 236, 255))
    draw.rounded_rectangle((488, 34, 688, 68), radius=17, fill=(255, 255, 255, 22), outline=(255, 255, 255, 40))
    draw_text(draw, (588, 41), scene.shot_type, FONTS["tiny"], (226, 232, 240, 230), anchor="ma")


def draw_brand_skin(draw: ImageDraw.ImageDraw, scene: Scene, t: float) -> None:
    phase = scene_phase(scene, t)
    draw.rectangle((0, 0, CANVAS_W, 222), fill=(20, 27, 36, 250))
    for x in range(-80, CANVAS_W, 86):
        offset = int(phase * 26)
        draw.line((x + offset, 0, x + 164 + offset, 222), fill=(255, 255, 255, 14), width=1)
    for y in range(30, 212, 38):
        draw.line((0, y, CANVAS_W, y), fill=(255, 255, 255, 10), width=1)
    formulas = ("step.draft -> review", "cmd = registry[key]", "presenter_source + content_timeline")
    for idx, text in enumerate(formulas):
        draw_text(draw, (258, 44 + idx * 46), text, FONTS["tiny"], (148, 163, 184, 150))
    draw_brand_header(draw, scene)


def draw_scene_title(draw: ImageDraw.ImageDraw, scene: Scene, box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, _ = box
    draw_text(draw, (x1 + 24, y1 + 22), scene.kicker, FONTS["kicker"], (244, 180, 74, 255))
    title_lines = fit_text(draw, scene.title, FONTS["title"], x2 - x1 - 48)
    for idx, line in enumerate(title_lines[:2]):
        draw_text(draw, (x1 + 24, y1 + 66 + idx * 58), line, FONTS["title"], (255, 255, 255, 255), stroke=1)
    base_y = y1 + 196
    for idx, line in enumerate(scene.lines):
        draw_text(draw, (x1 + 28, base_y + idx * 42), line, FONTS["body"], (226, 232, 240, 248))


def draw_warm_host_background(draw: ImageDraw.ImageDraw, scene: Scene, t: float) -> None:
    phase = scene_phase(scene, t)
    for i in range(CANVAS_H):
        a = i / CANVAS_H
        r = int(226 * (1 - a) + 242 * a)
        g = int(179 * (1 - a) + 208 * a)
        b = int(92 * (1 - a) + 145 * a)
        draw.line((0, i, CANVAS_W, i), fill=(r, g, b, 255))

    for idx in range(7):
        cx = 62 + idx * 108
        cy = 980 + int(math.sin(phase * math.pi * 2 + idx) * 16)
        draw.rounded_rectangle((cx, cy, cx + 64, cy + 180), radius=28, fill=(255, 255, 255, 28))

    if scene.keyword:
        y = 112 if scene.key != "opening" else 880
        fill = (179, 32, 44, 255) if scene.key in {"verdict", "final"} else (255, 255, 255, 255)
        stroke = 3 if scene.key in {"verdict", "final"} else 2
        draw_text(draw, (54, y), scene.keyword, FONTS["keyword"], fill, stroke=stroke)

    card = (38, 110, 682, 304) if scene.key != "opening" else (38, 100, 682, 300)
    rounded_panel(draw, card, radius=20, fill=(17, 24, 39, 214), outline=(255, 255, 255, 70))
    draw_scene_title(draw, scene, card)
    draw_broll_strip(draw, scene, t)


def draw_broll_strip(draw: ImageDraw.ImageDraw, scene: Scene, t: float) -> None:
    phase = scene_phase(scene, t)
    y1, y2 = 936, 1094
    draw.rounded_rectangle((34, y1, 686, y2), radius=22, fill=(15, 23, 42, 226), outline=(255, 255, 255, 44))
    if scene.key in {"opening", "thesis", "verdict", "final"}:
        for idx in range(9):
            x = 58 + idx * 72
            h = 34 + int(68 * abs(math.sin(phase * math.pi * 2 + idx * 0.7)))
            color = ((56, 189, 248, 210), (244, 180, 74, 220), (103, 232, 163, 210))[idx % 3]
            draw.rounded_rectangle((x, y2 - 22 - h, x + 38, y2 - 22), radius=8, fill=color)
        draw_text(draw, (58, y1 + 22), "content layer", FONTS["kicker"], (255, 250, 236, 255))
        draw_text(draw, (58, y1 + 62), "B-roll / cards / keywords / subtitles", FONTS["small"], (203, 213, 225, 245))


def draw_main_content_panel(draw: ImageDraw.ImageDraw, scene: Scene, t: float) -> None:
    phase = scene_phase(scene, t)
    panel = (34, 252, 686, 790)
    rounded_panel(draw, panel, radius=22, fill=(13, 18, 28, 242), outline=(255, 255, 255, 36))
    if scene.key in {"engine", "engine_map"}:
        draw_engine_visual(draw, panel, phase)
    elif scene.key in {"runtime", "worker"}:
        draw_runtime_visual(draw, panel, phase)
    elif scene.key in {"progress", "events"}:
        draw_events_visual(draw, panel, phase)
    elif scene.key == "agents":
        draw_agent_chain_visual(draw, panel, phase)
    else:
        draw_data_visual(draw, panel, phase)

    text_box = (38, 820, 682, 1014)
    rounded_panel(draw, text_box, radius=20, fill=(245, 248, 252, 238), outline=(255, 255, 255, 80))
    draw_text(draw, (66, 850), scene.kicker, FONTS["kicker"], (30, 41, 59, 255))
    title_lines = fit_text(draw, scene.title, FONTS["title"], 580)
    for idx, line in enumerate(title_lines[:2]):
        draw_text(draw, (66, 896 + idx * 54), line, FONTS["title"], (15, 23, 42, 255))


def draw_engine_visual(draw: ImageDraw.ImageDraw, panel: tuple[int, int, int, int], phase: float) -> None:
    x1, y1, x2, _ = panel
    labels = ("collect", "script", "visual", "voice", "render")
    colors = ((103, 232, 163, 255), (56, 189, 248, 255), (244, 180, 74, 255), (244, 114, 182, 255), (255, 255, 255, 255))
    for idx, label in enumerate(labels):
        x = x1 + 44 + idx * 112
        y = y1 + 138 + int(math.sin(phase * math.pi * 2 + idx) * 8)
        draw.rounded_rectangle((x, y, x + 82, y + 82), radius=18, fill=(30, 41, 59, 255), outline=colors[idx], width=3)
        draw_text(draw, (x + 41, y + 28), label, FONTS["tiny"], colors[idx], anchor="ma")
        if idx < len(labels) - 1:
            draw.line((x + 88, y + 41, x + 110, y + 41), fill=(148, 163, 184, 220), width=3)
            draw.polygon(((x + 112, y + 41), (x + 102, y + 34), (x + 102, y + 48)), fill=(148, 163, 184, 220))
    draw_text(draw, (x1 + 44, y1 + 56), "Production Instance", FONTS["title"], (255, 255, 255, 255), stroke=1)
    draw_text(draw, (x1 + 48, y1 + 300), "same instance, different views", FONTS["body"], (203, 213, 225, 240))


def draw_runtime_visual(draw: ImageDraw.ImageDraw, panel: tuple[int, int, int, int], phase: float) -> None:
    x1, y1, _, _ = panel
    boxes = (("QUEUE", 54), ("WORKER", 258), ("OUTPUT", 462))
    for idx, (label, xoff) in enumerate(boxes):
        x = x1 + xoff
        y = y1 + 150
        color = ((103, 232, 163, 255), (244, 180, 74, 255), (56, 189, 248, 255))[idx]
        draw.rounded_rectangle((x, y, x + 148, y + 100), radius=22, fill=(24, 34, 51, 255), outline=color, width=3)
        draw_text(draw, (x + 74, y + 34), label, FONTS["body_bold"], (255, 255, 255, 255), anchor="ma")
        if idx < 2:
            pulse = int(50 + 100 * abs(math.sin(phase * math.pi * 2)))
            draw.line((x + 156, y + 50, x + 194, y + 50), fill=(148, 163, 184, pulse), width=4)
    draw_text(draw, (x1 + 48, y1 + 60), "Redis queue + nof-worker", FONTS["title"], (255, 255, 255, 255), stroke=1)
    draw_text(draw, (x1 + 52, y1 + 328), "server is producer, worker is executor", FONTS["body"], (203, 213, 225, 240))


def draw_events_visual(draw: ImageDraw.ImageDraw, panel: tuple[int, int, int, int], phase: float) -> None:
    x1, y1, _, _ = panel
    draw_text(draw, (x1 + 46, y1 + 58), "on_progress(text)", FONTS["title"], (255, 255, 255, 255), stroke=1)
    code = ("queued", "running: render", "draft_ready", "completed")
    for idx, item in enumerate(code):
        y = y1 + 142 + idx * 70
        alpha = 120 + int(100 * abs(math.sin(phase * math.pi * 2 + idx)))
        draw.rounded_rectangle((x1 + 54, y, x1 + 596, y + 44), radius=12, fill=(30, 41, 59, 235), outline=(103, 232, 163, alpha))
        draw_text(draw, (x1 + 78, y + 8), f"event.{idx + 1}: {item}", FONTS["mono"], (226, 232, 240, 255))


def draw_agent_chain_visual(draw: ImageDraw.ImageDraw, panel: tuple[int, int, int, int], phase: float) -> None:
    x1, y1, _, _ = panel
    names = ("沈括", "鬼谷子", "柳永", "吴道子", "伯牙", "render")
    for idx, name in enumerate(names):
        col = idx % 3
        row = idx // 3
        x = x1 + 54 + col * 186
        y = y1 + 118 + row * 156
        color = ((103, 232, 163, 255), (56, 189, 248, 255), (244, 180, 74, 255))[idx % 3]
        draw.rounded_rectangle((x, y, x + 144, y + 96), radius=20, fill=(30, 41, 59, 255), outline=color, width=3)
        draw_text(draw, (x + 72, y + 28), name, FONTS["body_bold"], (255, 255, 255, 255), anchor="ma")
    draw_text(draw, (x1 + 46, y1 + 52), "Agent chain", FONTS["title"], (255, 255, 255, 255), stroke=1)
    x = x1 + 48 + int(520 * phase)
    draw.rounded_rectangle((x, y1 + 426, x + 48, y1 + 446), radius=10, fill=(244, 180, 74, 230))


def draw_data_visual(draw: ImageDraw.ImageDraw, panel: tuple[int, int, int, int], phase: float) -> None:
    x1, y1, x2, y2 = panel
    for idx in range(11):
        x = x1 + 40 + idx * 54
        draw.rounded_rectangle((x, y1 + 86, x + 34, y2 - 64), radius=10, fill=(30, 41, 59, 255), outline=(56, 189, 248, 95))
        for row in range(8):
            alpha = 80 + int(120 * abs(math.sin(phase * math.pi * 2 + idx + row)))
            draw.rectangle((x + 8, y1 + 112 + row * 44, x + 26, y1 + 124 + row * 44), fill=(103, 232, 163, alpha))
    draw_text(draw, (x1 + 46, y1 + 46), "material tracks", FONTS["title"], (255, 255, 255, 255), stroke=1)


def draw_capabilities_scene(draw: ImageDraw.ImageDraw, scene: Scene, t: float) -> None:
    phase = scene_phase(scene, t)
    draw_gradient_layer = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw_gradient(draw_gradient_layer, (18, 30, 48), (41, 28, 52))
    draw.bitmap((0, 0), draw_gradient_layer, fill=None)
    draw_brand_header(draw, scene)
    draw_text(draw, (38, 110), "能力不是堆在画面里", FONTS["headline"], (255, 255, 255, 255), stroke=2)
    draw_text(draw, (38, 184), "而是被编排", FONTS["headline"], (255, 255, 255, 255), stroke=2)
    items = (("ASR", "转写"), ("RW", "改写"), ("WST", "分镜"), ("TTS", "配音"), ("VID", "渲染"), ("QA", "质检"))
    for idx, (name, desc) in enumerate(items):
        col = idx % 2
        row = idx // 2
        x = 42 + col * 276
        y = 326 + row * 114 + int(math.sin(phase * math.pi * 2 + idx) * 5)
        rounded_panel(draw, (x, y, x + 246, y + 82), radius=18, fill=(17, 24, 39, 226))
        draw_text(draw, (x + 24, y + 18), name, FONTS["body_bold"], (103, 232, 163, 255))
        draw_text(draw, (x + 112, y + 20), desc, FONTS["body"], (226, 232, 240, 255))


def draw_base_scene(scene: Scene, t: float) -> Image.Image:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (12, 14, 19, 255))
    draw = ImageDraw.Draw(canvas)

    if scene.key == "capabilities":
        draw_capabilities_scene(draw, scene, t)
    elif scene.shot_type.startswith("host_full") or scene.shot_type == "final_stinger":
        draw_warm_host_background(draw, scene, t)
        draw_brand_header(draw, scene)
    elif scene.shot_type == "keyword_card":
        draw_gradient(canvas, (28, 32, 42), (70, 54, 34))
        draw_brand_skin(draw, scene, t)
        draw_text(draw, (48, 304), scene.keyword, FONTS["keyword"], (244, 180, 74, 255), stroke=3)
        draw_main_content_panel(draw, scene, t)
    else:
        draw_gradient(canvas, (10, 16, 27), (31, 41, 55))
        draw_brand_skin(draw, scene, t)
        draw_main_content_panel(draw, scene, t)

    return canvas


def presenter_frame(src: Image.Image, mode: str) -> tuple[Image.Image | None, tuple[int, int]]:
    if mode == "none":
        return None, (0, 0)
    if mode == "host_full":
        crop = cover_crop(src, (390, 0, 1160, 650), (478, 616))
        return crop, (121, 312)
    if mode == "large_side":
        crop = cover_crop(src, (420, 0, 1140, 650), (286, 392))
        return crop, (404, 386)
    if mode == "pip_circle":
        crop = cover_crop(src, (500, 0, 1080, 610), (172, 172))
        return crop, (52, 92)
    if mode == "pip_bottom":
        crop = cover_crop(src, (500, 0, 1080, 610), (176, 212))
        return crop, (484, 784)
    crop = cover_crop(src, (500, 0, 1080, 610), (190, 230))
    return crop, (478, 796)


def paste_presenter(canvas: Image.Image, src: Image.Image, mode: str, anchor: str) -> None:
    presenter, default_pos = presenter_frame(src, mode)
    if presenter is None:
        return

    pos = default_pos
    if mode == "pip_circle" and anchor == "top_right":
        pos = (496, 92)
    elif mode in {"pip_card", "pip_bottom"} and anchor == "bottom_left":
        pos = (58, 784)
    elif mode == "large_side" and anchor == "left":
        pos = (34, 386)

    x, y = pos
    w, h = presenter.size
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    if mode == "pip_circle":
        draw.ellipse((x + 6, y + 8, x + w + 6, y + h + 8), fill=(0, 0, 0, 90))
        mask = Image.new("L", presenter.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, w, h), fill=255)
        clipped = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        clipped.paste(presenter.convert("RGBA"), pos, mask)
        border = ImageDraw.Draw(clipped)
        border.ellipse((x, y, x + w, y + h), outline=(255, 247, 218, 230), width=4)
    else:
        radius = 28 if mode != "host_full" else 34
        draw.rounded_rectangle((x + 8, y + 12, x + w + 8, y + h + 12), radius=radius, fill=(0, 0, 0, 105))
        mask = Image.new("L", presenter.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
        clipped = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        clipped.paste(presenter.convert("RGBA"), pos, mask)
        border = ImageDraw.Draw(clipped)
        border.rounded_rectangle((x, y, x + w, y + h), radius=radius, outline=(255, 247, 218, 155), width=3)

    canvas.alpha_composite(overlay)
    canvas.alpha_composite(clipped)


def draw_subtitle(draw: ImageDraw.ImageDraw, t: float) -> None:
    subtitle = active(SUBTITLES, t)
    lines = fit_text(draw, subtitle.text, FONTS["subtitle"], 638)
    top = 1126 if len(lines) == 1 else 1094
    height = 58 + (len(lines) - 1) * 44
    draw.rounded_rectangle((34, top - 12, 686, top + height), radius=12, fill=(0, 0, 0, 158))
    for idx, line in enumerate(lines[:2]):
        draw_text(
            draw,
            (360, top + idx * 44),
            line,
            FONTS["subtitle"],
            (255, 255, 255, 255),
            stroke=2,
            anchor="ma",
        )


def draw_progress(draw: ImageDraw.ImageDraw, t: float, duration: float) -> None:
    draw.rectangle((0, 1272, CANVAS_W, CANVAS_H), fill=(0, 0, 0, 145))
    width = int(CANVAS_W * min(max(t / duration, 0), 1))
    draw.rectangle((0, 1272, width, CANVAS_H), fill=(244, 180, 74, 255))


def draw_timed_overlays(canvas: Image.Image, t: float, duration: float) -> None:
    draw = ImageDraw.Draw(canvas)
    draw_subtitle(draw, t)
    draw_progress(draw, t, duration)


def run_ffmpeg_decode(input_path: Path, fps: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-r",
            str(fps),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        stdout=subprocess.PIPE,
    )


def run_ffmpeg_encode(output_path: Path, input_path: Path, fps: int, crf: int) -> subprocess.Popen[bytes]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{CANVAS_W}x{CANVAS_H}",
            "-r",
            str(fps),
            "-i",
            "-",
            "-i",
            str(input_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ],
        stdin=subprocess.PIPE,
    )


def write_shot_plan(path: Path, output_path: Path, input_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "template": "digital_presenter_shot_plan_poc",
        "presenter_source": str(input_path),
        "rendered_output": str(output_path),
        "canvas": {"width": CANVAS_W, "height": CANVAS_H, "fps": FPS},
        "tracks": [
            "presenter_video",
            "content_background",
            "brand_skin",
            "keyword_text",
            "subtitle_text",
            "progress_bar",
        ],
        "scenes": [asdict(scene) for scene in SCENES],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render(args: argparse.Namespace) -> None:
    if not args.input.exists():
        raise FileNotFoundError(args.input)

    decoder = run_ffmpeg_decode(args.input, args.fps)
    encoder = run_ffmpeg_encode(args.output, args.input, args.fps, args.crf)
    assert decoder.stdout is not None
    assert encoder.stdin is not None

    source_w, source_h = 1280, 720
    frame_size = source_w * source_h * 3
    frame_index = 0
    try:
        while True:
            data = decoder.stdout.read(frame_size)
            if not data or len(data) < frame_size:
                break

            t = frame_index / args.fps
            scene = active(SCENES, t)
            src = Image.frombytes("RGB", (source_w, source_h), data).convert("RGBA")
            canvas = draw_base_scene(scene, t)
            paste_presenter(canvas, src, scene.presenter_mode, scene.presenter_anchor)
            draw_timed_overlays(canvas, t, args.duration)
            encoder.stdin.write(canvas.convert("RGB").tobytes())
            frame_index += 1
            if frame_index % 50 == 0:
                print(f"rendered {frame_index} frames", file=sys.stderr)
    finally:
        try:
            encoder.stdin.close()
        except BrokenPipeError:
            pass
        decoder.wait()
        encoder.wait()

    if decoder.returncode != 0:
        raise RuntimeError(f"decoder exit {decoder.returncode}")
    if encoder.returncode != 0:
        raise RuntimeError(f"encoder exit {encoder.returncode}")

    shot_plan_path = args.shot_plan_output or args.output.with_suffix(".shot_plan.json")
    write_shot_plan(shot_plan_path, args.output, args.input)
    print(f"wrote {args.output} frames={frame_index}")
    print(f"wrote {shot_plan_path}")


def main() -> int:
    try:
        render(parse_args())
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
