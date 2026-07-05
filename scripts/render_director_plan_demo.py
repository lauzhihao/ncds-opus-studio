#!/usr/bin/env python3
"""Render a director_plan.v1 into a demo MP4.

This is a thin contract probe for Guanhanqing output. It intentionally reads the
plan fields mechanically instead of re-designing the video by hand.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ncds_opus_factory.commands.horizontal_video_layouts import frame_to_tuple, get_layout  # noqa: E402

DEFAULT_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FALLBACK_FONT = "/Library/Fonts/Arial Unicode.ttf"
DEFAULT_FPS = 24
SOURCE_FACE_BOX = (180, 220, 540, 580)
SOURCE_BUST_BOX = (70, 40, 650, 850)


@dataclass(frozen=True)
class VideoProbe:
    width: int
    height: int
    duration: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render Guanhanqing director_plan.v1 demo video.")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--presenter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--crf", type=int, default=20)
    parser.add_argument("--sample-frames", action="store_true")
    return parser.parse_args()


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in (DEFAULT_FONT, FALLBACK_FONT):
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default(size=size)


FONTS: dict[int, ImageFont.FreeTypeFont] = {}


def font(size: int) -> ImageFont.FreeTypeFont:
    if size not in FONTS:
        FONTS[size] = load_font(size)
    return FONTS[size]


def ffprobe_video(path: Path) -> VideoProbe:
    raw = subprocess.check_output([
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration",
        "-of",
        "json",
        str(path),
    ])
    data = json.loads(raw)
    stream = data["streams"][0]
    return VideoProbe(
        width=int(stream["width"]),
        height=int(stream["height"]),
        duration=float(stream.get("duration") or 0),
    )


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int, min_size: int) -> ImageFont.ImageFont:
    size = start
    while size > min_size:
        fnt = font(size)
        if text_size(draw, text, fnt)[0] <= max_width:
            return fnt
        size -= 2
    return font(min_size)


def draw_fit_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    max_width: int,
    size: int,
    min_size: int = 24,
    fill: tuple[int, int, int, int] = (255, 255, 255, 255),
    anchor: str = "la",
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 255),
) -> None:
    fnt = fit_font(draw, text, max_width, size, min_size)
    draw.text(xy, text, font=fnt, fill=fill, anchor=anchor, stroke_width=stroke_width, stroke_fill=stroke_fill)


def rounded(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], radius: int, fill: tuple[int, int, int, int], outline=None, width: int = 1) -> None:
    draw.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=width)


def overlay_rect(dst: Image.Image, rect: tuple[int, int, int, int], fill: tuple[int, int, int, int]) -> None:
    layer = Image.new("RGBA", dst.size, (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    ldraw.rectangle(rect, fill=fill)
    dst.alpha_composite(layer)


def paste_cover(dst: Image.Image, src: Image.Image, box: tuple[int, int, int, int], *, crop: tuple[int, int, int, int] | None = None, alpha: int = 255) -> None:
    x1, y1, x2, y2 = box
    image = src.crop(crop) if crop else src
    fitted = ImageOps.fit(image, (x2 - x1, y2 - y1), method=Image.Resampling.LANCZOS, centering=(0.5, 0.42)).convert("RGBA")
    if alpha < 255:
        fitted.putalpha(alpha)
    dst.alpha_composite(fitted, (x1, y1))


def paste_contain(dst: Image.Image, src: Image.Image, box: tuple[int, int, int, int], *, crop: tuple[int, int, int, int] | None = None) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    image = src.crop(crop) if crop else src
    contained = ImageOps.contain(image, (x2 - x1, y2 - y1), method=Image.Resampling.LANCZOS).convert("RGBA")
    px = x1 + ((x2 - x1) - contained.width) // 2
    py = y1 + ((y2 - y1) - contained.height) // 2
    dst.alpha_composite(contained, (px, py))
    return px, py, px + contained.width, py + contained.height


def paste_circle(dst: Image.Image, src: Image.Image, box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    size = min(x2 - x1, y2 - y1)
    crop = src.crop(SOURCE_FACE_BOX)
    face = ImageOps.fit(crop, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.52)).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.ellipse((0, 0, size - 1, size - 1), fill=255)
    ring = Image.new("RGBA", (size + 18, size + 18), (0, 0, 0, 0))
    rdraw = ImageDraw.Draw(ring)
    rdraw.ellipse((0, 0, size + 17, size + 17), fill=(244, 194, 54, 255))
    rdraw.ellipse((7, 7, size + 10, size + 10), fill=(255, 255, 255, 255))
    dst.alpha_composite(ring, (x1 - 9, y1 - 9))
    dst.paste(face, (x1, y1), mask)


def active_timeline_clip(plan: dict[str, Any], track_name: str, t: float) -> dict[str, Any] | None:
    timeline = plan.get("timeline") if isinstance(plan.get("timeline"), dict) else {}
    tracks = timeline.get("tracks") if isinstance(timeline.get("tracks"), dict) else {}
    clips = tracks.get(track_name) if isinstance(tracks.get(track_name), list) else []
    for clip in clips:
        if float(clip.get("start", 0.0)) <= t < float(clip.get("end", 0.0)):
            return clip
    return clips[-1] if clips else None


def scene_by_id(plan: dict[str, Any], scene_id: str | None) -> dict[str, Any] | None:
    if not scene_id:
        return None
    for scene in plan.get("scenes") or []:
        if scene.get("id") == scene_id:
            return scene
    return None


def shot_by_id(scene: dict[str, Any], shot_id: str | None) -> dict[str, Any] | None:
    if not shot_id:
        return None
    for shot in scene.get("digital_human_shots") or []:
        if shot.get("id") == shot_id:
            return shot
    return None


def active_scene(plan: dict[str, Any], t: float) -> dict[str, Any]:
    clip = active_timeline_clip(plan, "scenes", t)
    scene = scene_by_id(plan, str(clip.get("scene_id") or clip.get("id")) if clip else None)
    if scene:
        return scene
    scenes = plan["scenes"]
    for scene in scenes:
        if float(scene["start"]) <= t < float(scene["end"]):
            return scene
    return scenes[-1]


def active_shot(scene: dict[str, Any], t: float) -> dict[str, Any]:
    plan = scene.get("_plan") if isinstance(scene.get("_plan"), dict) else None
    if plan:
        clip = active_timeline_clip(plan, "presenter", t)
        if clip and clip.get("scene_id") == scene.get("id"):
            shot = shot_by_id(scene, str(clip.get("shot_id") or clip.get("id")))
            merged = dict(shot or {})
            merged.update(clip)
            if "digital_human_mode" not in merged and shot:
                merged["digital_human_mode"] = shot.get("digital_human_mode")
            return merged
    shots = scene.get("digital_human_shots") or []
    for shot in shots:
        if float(shot["start"]) <= t < float(shot["end"]):
            return shot
    return shots[-1] if shots else {"digital_human_mode": "head_corner", "layout_slot": "avatar_right_bottom"}


def active_subtitle(plan: dict[str, Any], scene: dict[str, Any], t: float) -> str:
    clip = active_timeline_clip(plan, "subtitles", t)
    if clip and clip.get("scene_id") == scene.get("id"):
        return str(clip.get("text") or "")
    for cue in scene.get("subtitle_cues") or []:
        if float(cue["start"]) <= t < float(cue["end"]):
            return str(cue.get("text") or "")
    return ""


def gradient_background(width: int, height: int, scene_index: int) -> Image.Image:
    palettes = [
        ((19, 37, 44), (246, 184, 70), (18, 116, 94)),
        ((22, 24, 36), (241, 108, 83), (72, 141, 214)),
        ((24, 42, 31), (94, 171, 116), (249, 214, 91)),
        ((31, 27, 45), (112, 120, 219), (248, 141, 82)),
        ((20, 45, 54), (89, 188, 190), (238, 201, 88)),
        ((35, 37, 32), (218, 86, 63), (69, 158, 121)),
    ]
    c1, c2, c3 = palettes[scene_index % len(palettes)]
    img = Image.new("RGBA", (width, height), c1 + (255,))
    draw = ImageDraw.Draw(img, "RGBA")
    for y in range(height):
        mix = y / max(1, height - 1)
        col = tuple(int(c1[i] * (1 - mix) + c3[i] * mix) for i in range(3))
        draw.line((0, y, width, y), fill=col + (255,))
    draw.rounded_rectangle((-width * 0.25, height * 0.05, width * 0.55, height * 0.38), radius=40, fill=c2 + (42,))
    draw.rounded_rectangle((width * 0.55, height * 0.18, width * 1.1, height * 0.56), radius=44, fill=c3 + (55,))
    draw.line((0, int(height * 0.72), width, int(height * 0.58)), fill=(255, 255, 255, 40), width=3)
    return img


def scene_index(plan: dict[str, Any], scene: dict[str, Any]) -> int:
    for idx, candidate in enumerate(plan["scenes"]):
        if candidate.get("id") == scene.get("id"):
            return idx
    return 0


def content_rect(plan: dict[str, Any], shot: dict[str, Any]) -> tuple[int, int, int, int]:
    canvas = plan["canvas"]
    width, height = int(canvas["width"]), int(canvas["height"])
    safe = canvas.get("safe_areas", {})
    orientation = plan.get("source", {}).get("orientation", "portrait")
    if orientation == "landscape":
        slots = shot.get("content_slots") if isinstance(shot.get("content_slots"), dict) else None
        if not slots and shot.get("layout_id"):
            try:
                slots = get_layout(str(shot["layout_id"])).get("content_slots")
            except KeyError:
                slots = None
        if slots and isinstance(slots.get("main"), dict):
            rect = slots["main"]
            return int(rect["x"]), int(rect["y"]), int(rect["x"] + rect["w"]), int(rect["y"] + rect["h"])
        rect = safe.get("content_main", {"x": 110, "y": 96, "w": 1500, "h": 730})
        return int(rect["x"]), int(rect["y"]), int(rect["x"] + rect["w"]), int(rect["y"] + rect["h"])
    if shot.get("digital_human_mode") == "bust_top_half":
        rect = {"x": 44, "y": 720, "w": width - 88, "h": 286}
    elif shot.get("digital_human_mode") == "full_screen":
        rect = {"x": 44, "y": 760, "w": width - 88, "h": 240}
    else:
        rect = safe.get("content_main", {"x": 44, "y": 128, "w": width - 88, "h": 820})
    return int(rect["x"]), int(rect["y"]), int(rect["x"] + rect["w"]), int(rect["y"] + rect["h"])


def draw_host(canvas: Image.Image, presenter: Image.Image, plan: dict[str, Any], shot: dict[str, Any]) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    width, height = canvas.size
    mode = shot.get("digital_human_mode")
    orientation = plan.get("source", {}).get("orientation", "portrait")
    safe = plan.get("canvas", {}).get("safe_areas", {})
    layout_frame = frame_to_tuple(shot.get("presenter_frame") if isinstance(shot.get("presenter_frame"), dict) else None)
    if orientation == "landscape" and not layout_frame and shot.get("layout_id"):
        try:
            layout_frame = frame_to_tuple(get_layout(str(shot["layout_id"])).get("presenter_frame"))
        except KeyError:
            layout_frame = None

    if mode == "full_screen":
        if orientation == "portrait":
            paste_cover(canvas, presenter, (0, 0, width, height))
            overlay_rect(canvas, (0, 0, width, height), (0, 0, 0, 70))
        else:
            box = layout_frame or (0, 0, width // 2, height)
            draw.rounded_rectangle((box[0] + 26, box[1] + 26, box[2] - 26, box[3] - 26), radius=34, fill=(4, 8, 12, 205), outline=(255, 255, 255, 90), width=2)
            actual = paste_contain(canvas, presenter, (box[0] + 44, box[1] + 40, box[2] - 44, box[3] - 40))
            draw.rounded_rectangle(actual, radius=26, outline=(255, 255, 255, 120), width=3)
        return

    if mode == "bust_top_half":
        if orientation == "landscape":
            box = layout_frame
            if not box:
                slot = safe.get("host_bust_left", {"x": 0, "y": 0, "w": 680, "h": 900})
                box = (int(slot["x"]), int(slot["y"]), int(slot["x"] + slot["w"]), int(slot["y"] + slot["h"]))
            draw.rounded_rectangle((box[0] + 18, box[1] + 24, box[2] - 18, box[3] - 24), radius=34, fill=(4, 8, 12, 185), outline=(255, 255, 255, 90), width=2)
            paste_cover(canvas, presenter, (box[0] + 36, box[1] + 42, box[2] - 36, box[3] - 42), crop=SOURCE_BUST_BOX)
            divider_x = box[2] if box[0] < width // 2 else box[0]
            draw.line((divider_x, 68, divider_x, height - 170), fill=(255, 255, 255, 70), width=2)
        else:
            slot = safe.get("host_top_half", {"x": 0, "y": 0, "w": width, "h": 690})
            box = (int(slot["x"]), int(slot["y"]), int(slot["x"] + slot["w"]), int(slot["y"] + slot["h"]))
            paste_cover(canvas, presenter, box, crop=SOURCE_BUST_BOX)
            overlay_rect(canvas, (0, box[3] - 140, width, box[3]), (0, 0, 0, 85))
        return

    avatar = safe.get("avatar_right_bottom")
    if layout_frame:
        box = layout_frame
    elif avatar:
        size = min(int(avatar["w"]), int(avatar["h"]))
        box = (int(avatar["x"]), int(avatar["y"]), int(avatar["x"] + size), int(avatar["y"] + size))
    elif orientation == "landscape":
        box = (width - 270, height - 330, width - 70, height - 130)
    else:
        box = (width - 220, height - 460, width - 40, height - 280)
    paste_circle(canvas, presenter, box)


def draw_chip(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, *, accent: tuple[int, int, int, int], max_width: int) -> int:
    x, y = xy
    fnt = fit_font(draw, text, max_width - 24, 24, 16)
    tw, th = text_size(draw, text, fnt)
    w = min(max_width, tw + 28)
    rounded(draw, (x, y, x + w, y + 38), 19, accent)
    draw.text((x + 14, y + 21), text, font=fnt, fill=(16, 20, 24, 255), anchor="lm")
    return w


def draw_title_card(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], block: dict[str, Any], scene: dict[str, Any], landscape: bool) -> int:
    x1, y1, x2, _ = rect
    content = block.get("content") or {}
    title = str(content.get("title") or scene.get("title") or "")
    keywords = [str(k) for k in content.get("keywords") or []][:4]
    h = 170 if landscape else 190
    rounded(draw, (x1, y1, x2, y1 + h), 16, (4, 10, 14, 206), outline=(255, 255, 255, 80), width=2)
    draw.text((x1 + 24, y1 + 30), str(scene.get("kicker") or "DIRECTOR"), font=font(22), fill=(248, 209, 85, 255), anchor="la")
    draw_fit_text(draw, (x1 + 24, y1 + 90), title, max_width=x2 - x1 - 48, size=landscape and 58 or 48, min_size=30, fill=(255, 255, 255, 255), anchor="lm")
    cx = x1 + 24
    cy = y1 + h - 56
    for kw in keywords:
        used = draw_chip(draw, (cx, cy), kw, accent=(247, 203, 75, 235), max_width=max(130, x2 - cx - 20))
        cx += used + 10
        if cx > x2 - 130:
            break
    return h + 18


def draw_metric_card(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], block: dict[str, Any], landscape: bool) -> int:
    x1, y1, x2, _ = rect
    items = [str(i) for i in (block.get("content") or {}).get("items") or []][:4]
    if not items:
        return 0
    if landscape:
        h = 142
        gap = 16
        card_w = (x2 - x1 - gap * (len(items) - 1)) // len(items)
        for idx, item in enumerate(items):
            bx = x1 + idx * (card_w + gap)
            rounded(draw, (bx, y1, bx + card_w, y1 + h), 16, (255, 255, 255, 226))
            parts = item.replace("：", ":").split(":", 1)
            label = parts[0] if len(parts) == 2 else "指标"
            value = parts[1].strip() if len(parts) == 2 else item
            draw_fit_text(draw, (bx + 18, y1 + 36), label, max_width=card_w - 36, size=24, min_size=16, fill=(55, 65, 70, 255), anchor="lm")
            draw_fit_text(draw, (bx + 18, y1 + 92), value, max_width=card_w - 36, size=42, min_size=24, fill=(203, 63, 47, 255), anchor="lm")
        return h + 18
    h = 70 * len(items) + 24
    rounded(draw, (x1, y1, x2, y1 + h), 16, (255, 255, 255, 226))
    for idx, item in enumerate(items):
        y = y1 + 24 + idx * 70
        draw.ellipse((x1 + 24, y, x1 + 48, y + 24), fill=(222, 73, 48, 255))
        draw_fit_text(draw, (x1 + 64, y + 13), item, max_width=x2 - x1 - 88, size=34, min_size=22, fill=(28, 37, 39, 255), anchor="lm")
    return h + 18


def draw_comparison_bar(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], block: dict[str, Any], landscape: bool) -> int:
    x1, y1, x2, _ = rect
    content = block.get("content") or {}
    left = str(content.get("left_label") or "普通做法")
    right = str(content.get("right_label") or "主角做法")
    h = 160 if landscape else 190
    rounded(draw, (x1, y1, x2, y1 + h), 16, (8, 14, 17, 205), outline=(255, 255, 255, 72), width=2)
    mid = x1 + (x2 - x1) // 2
    draw.rounded_rectangle((x1 + 18, y1 + 48, mid - 8, y1 + h - 28), radius=14, fill=(74, 85, 91, 230))
    draw.rounded_rectangle((mid + 8, y1 + 48, x2 - 18, y1 + h - 28), radius=14, fill=(247, 204, 78, 242))
    draw.text((x1 + 24, y1 + 26), "对比", font=font(24), fill=(255, 255, 255, 210), anchor="lm")
    draw_fit_text(draw, (x1 + 36, y1 + h // 2 + 18), left, max_width=mid - x1 - 58, size=30, min_size=18, fill=(255, 255, 255, 255), anchor="lm")
    draw_fit_text(draw, (mid + 28, y1 + h // 2 + 18), right, max_width=x2 - mid - 58, size=30, min_size=18, fill=(25, 31, 35, 255), anchor="lm")
    return h + 18


def draw_process_map(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], block: dict[str, Any], landscape: bool) -> int:
    x1, y1, x2, _ = rect
    nodes = [str(n) for n in (block.get("content") or {}).get("nodes") or []][:5]
    if len(nodes) < 2:
        return 0
    h = 160 if landscape else 250
    rounded(draw, (x1, y1, x2, y1 + h), 16, (255, 255, 255, 218))
    draw.text((x1 + 22, y1 + 30), "流程", font=font(24), fill=(35, 45, 48, 255), anchor="lm")
    if landscape:
        available = x2 - x1 - 64
        step = available / max(1, len(nodes) - 1)
        cy = y1 + 100
        points = []
        for idx, node in enumerate(nodes):
            cx = int(x1 + 36 + idx * step)
            points.append((cx, cy))
            draw.ellipse((cx - 13, cy - 13, cx + 13, cy + 13), fill=(31, 137, 104, 255))
            draw_fit_text(draw, (cx, cy + 42), node, max_width=int(step) + 30, size=22, min_size=14, fill=(30, 38, 42, 255), anchor="ma")
        for a, b in zip(points, points[1:]):
            draw.line((a[0] + 16, a[1], b[0] - 16, b[1]), fill=(31, 137, 104, 180), width=5)
    else:
        top = y1 + 66
        step = (h - 92) / max(1, len(nodes) - 1)
        for idx, node in enumerate(nodes):
            cy = int(top + idx * step)
            draw.ellipse((x1 + 28, cy - 12, x1 + 52, cy + 12), fill=(31, 137, 104, 255))
            if idx < len(nodes) - 1:
                draw.line((x1 + 40, cy + 15, x1 + 40, cy + int(step) - 15), fill=(31, 137, 104, 150), width=4)
            draw_fit_text(draw, (x1 + 70, cy), node, max_width=x2 - x1 - 96, size=28, min_size=18, fill=(30, 38, 42, 255), anchor="lm")
    return h + 18


def draw_keyword_card(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], block: dict[str, Any], landscape: bool) -> int:
    x1, y1, x2, _ = rect
    items = [str(i) for i in (block.get("content") or {}).get("items") or []][:6]
    h = 140 if landscape else 170
    rounded(draw, (x1, y1, x2, y1 + h), 16, (5, 10, 14, 198), outline=(255, 255, 255, 70), width=2)
    cx, cy = x1 + 22, y1 + 28
    for item in items:
        used = draw_chip(draw, (cx, cy), item, accent=(255, 255, 255, 224), max_width=x2 - cx - 22)
        cx += used + 10
        if cx > x2 - 160:
            cx = x1 + 22
            cy += 50
        if cy > y1 + h - 44:
            break
    return h + 18


def draw_visual_blocks(canvas: Image.Image, plan: dict[str, Any], scene: dict[str, Any], shot: dict[str, Any], t: float) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    rect = content_rect(plan, shot)
    x1, y1, x2, y2 = rect
    landscape = plan.get("source", {}).get("orientation") == "landscape"

    if shot.get("digital_human_mode") == "head_corner":
        rounded(draw, (x1 - 12, y1 - 12, x2 + 12, y2 + 12), 24, (255, 255, 255, 26))

    cursor = y1
    blocks = sorted(scene.get("visual_blocks") or [], key=lambda b: int(b.get("priority") or 99))
    for block in blocks:
        timing = block.get("timing_hint") or {}
        start = float(timing.get("start", scene.get("start", 0)))
        end = float(timing.get("end", scene.get("end", 0)))
        if not (start <= t <= end):
            continue
        btype = block.get("type")
        remaining = y2 - cursor
        if remaining < 90:
            break
        block_rect = (x1, cursor, x2, y2)
        if btype == "title_card":
            used = draw_title_card(draw, block_rect, block, scene, landscape)
        elif btype == "metric_card":
            used = draw_metric_card(draw, block_rect, block, landscape)
        elif btype == "comparison_bar":
            used = draw_comparison_bar(draw, block_rect, block, landscape)
        elif btype == "process_map":
            used = draw_process_map(draw, block_rect, block, landscape)
        else:
            used = draw_keyword_card(draw, block_rect, block, landscape)
        cursor += used


def draw_scene_badge(canvas: Image.Image, scene: dict[str, Any]) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    label = str(scene.get("kicker") or "")
    if not label:
        return
    rounded(draw, (28, 28, 260, 74), 23, (246, 198, 61, 235))
    draw_fit_text(draw, (46, 52), label, max_width=194, size=24, min_size=16, fill=(20, 26, 28, 255), anchor="lm")


def draw_subtitle(canvas: Image.Image, plan: dict[str, Any], scene: dict[str, Any], t: float) -> None:
    text = active_subtitle(plan, scene, t)
    if not text:
        return
    draw = ImageDraw.Draw(canvas, "RGBA")
    shot = active_shot(scene, t)
    safe = shot.get("subtitle_frame") if isinstance(shot.get("subtitle_frame"), dict) else None
    if not safe and shot.get("layout_id"):
        try:
            safe = get_layout(str(shot["layout_id"])).get("subtitle_frame")
        except KeyError:
            safe = None
    if not safe:
        safe = plan.get("canvas", {}).get("safe_areas", {}).get("subtitle_bottom")
    width, height = canvas.size
    if safe:
        x1, y1, x2, y2 = int(safe["x"]), int(safe["y"]), int(safe["x"] + safe["w"]), int(safe["y"] + safe["h"])
    else:
        x1, y1, x2, y2 = 32, height - 160, width - 32, height - 40
    rounded(draw, (x1, y1, x2, y2), 24, (1, 7, 9, 224), outline=(255, 255, 255, 220), width=2)
    draw_fit_text(
        draw,
        ((x1 + x2) // 2, (y1 + y2) // 2 + 2),
        text,
        max_width=x2 - x1 - 46,
        size=46 if width < 1000 else 54,
        min_size=24,
        fill=(255, 255, 255, 255),
        anchor="mm",
        stroke_width=1,
        stroke_fill=(0, 0, 0, 255),
    )


def draw_progress(canvas: Image.Image, t: float, duration: float) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    width, height = canvas.size
    y = height - 9
    draw.rectangle((0, y, width, height), fill=(0, 0, 0, 120))
    draw.rectangle((0, y, int(width * min(1.0, max(0.0, t / duration))), height), fill=(247, 200, 62, 255))


def render_frame(plan: dict[str, Any], presenter: Image.Image, t: float, duration: float) -> Image.Image:
    width, height = int(plan["canvas"]["width"]), int(plan["canvas"]["height"])
    scene = active_scene(plan, t)
    scene["_plan"] = plan
    shot = active_shot(scene, t)
    canvas = gradient_background(width, height, scene_index(plan, scene))
    presenter_rgba = presenter.convert("RGBA")
    if shot.get("digital_human_mode") != "head_corner":
        draw_host(canvas, presenter_rgba, plan, shot)
    if shot.get("digital_human_mode") != "full_screen" or t - float(shot.get("start", 0)) > 0.6:
        draw_visual_blocks(canvas, plan, scene, shot, t)
    if shot.get("digital_human_mode") == "head_corner":
        draw_host(canvas, presenter_rgba, plan, shot)
    draw_scene_badge(canvas, scene)
    draw_subtitle(canvas, plan, scene, t)
    draw_progress(canvas, t, duration)
    return canvas.convert("RGB")


def run_ffmpeg_decode(input_path: Path, fps: int, duration: float) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-t",
            f"{duration:.3f}",
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


def run_ffmpeg_encode(
    output_path: Path,
    presenter_path: Path,
    fps: int,
    width: int,
    height: int,
    crf: int,
) -> subprocess.Popen[bytes]:
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
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "-",
            "-i",
            str(presenter_path),
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
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        stdin=subprocess.PIPE,
    )


def write_manifest(plan: dict[str, Any], args: argparse.Namespace, duration: float, frames: int) -> None:
    manifest = {
        "renderer": "director_plan_demo",
        "plan": str(args.plan),
        "presenter": str(args.presenter),
        "output": str(args.output),
        "duration_seconds": duration,
        "frames": frames,
        "canvas": plan.get("canvas"),
        "timeline": {
            "schema_version": (plan.get("timeline") or {}).get("schema_version"),
            "fps": (plan.get("timeline") or {}).get("fps"),
            "total_frames": (plan.get("timeline") or {}).get("total_frames"),
            "track_counts": {
                name: len(items)
                for name, items in ((plan.get("timeline") or {}).get("tracks") or {}).items()
                if isinstance(items, list)
            },
        },
        "layout_library": (plan.get("layout_library") or {}).get("library_id"),
        "generation": plan.get("generation"),
        "self_check": plan.get("self_check"),
        "note": "Contract probe: rendered mechanically from director_plan.v1.",
    }
    args.output.with_suffix(".render_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sample_frames(output_path: Path) -> None:
    stem = output_path.with_suffix("")
    for sec in (2, 6, 14, 24, 40, 58, 76, 88):
        out = stem.parent / f"{stem.name}-frame-{sec:02d}s.png"
        subprocess.run([
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(sec),
            "-i",
            str(output_path),
            "-frames:v",
            "1",
            str(out),
        ], check=False)


def render(args: argparse.Namespace) -> None:
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    probe = ffprobe_video(args.presenter)
    width, height = int(plan["canvas"]["width"]), int(plan["canvas"]["height"])
    duration = float(args.duration or plan.get("source", {}).get("duration_seconds") or probe.duration)
    duration = min(duration, probe.duration) if probe.duration else duration

    decoder = run_ffmpeg_decode(args.presenter, args.fps, duration)
    encoder = run_ffmpeg_encode(args.output, args.presenter, args.fps, width, height, args.crf)
    assert decoder.stdout is not None
    assert encoder.stdin is not None

    frame_size = probe.width * probe.height * 3
    frame_index = 0
    total_frames = max(1, int(math.ceil(duration * args.fps)))
    try:
        while frame_index < total_frames:
            raw = decoder.stdout.read(frame_size)
            if not raw or len(raw) < frame_size:
                break
            t = frame_index / args.fps
            src = Image.frombytes("RGB", (probe.width, probe.height), raw)
            frame = render_frame(plan, src, t, duration)
            encoder.stdin.write(frame.tobytes())
            frame_index += 1
            if frame_index % (args.fps * 4) == 0:
                print(f"[progress] rendered {frame_index}/{total_frames} frames", file=sys.stderr, flush=True)
    finally:
        try:
            encoder.stdin.close()
        except BrokenPipeError:
            pass
        decoder.wait()
        encoder.wait()

    if decoder.returncode != 0:
        raise RuntimeError(f"ffmpeg decode failed: {decoder.returncode}")
    if encoder.returncode != 0:
        raise RuntimeError(f"ffmpeg encode failed: {encoder.returncode}")

    write_manifest(plan, args, duration, frame_index)
    if args.sample_frames:
        sample_frames(args.output)
    print(json.dumps({
        "output": str(args.output),
        "manifest": str(args.output.with_suffix(".render_manifest.json")),
        "frames": frame_index,
        "duration_seconds": duration,
    }, ensure_ascii=False))


def main() -> int:
    try:
        render(parse_args())
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
