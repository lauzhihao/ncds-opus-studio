"""Build a lightweight Jianying draft package from a final_preview job.

The package is intentionally self-contained: it includes Jianying-like draft
JSON, copied media assets, SRT subtitles, and a timeline manifest. Jianying's
native draft schema changes between desktop versions, so the manifest/SRT give
operators an editable fallback even when a specific Jianying build rejects some
JSON fields.
"""

from __future__ import annotations

import json
import shutil
import time
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1080
FPS = 30.0
US_PER_MS = 1000


def build_jianying_draft(job_dir: Path, *, job_id: str, title: str = "") -> dict[str, Any]:
    """Generate ``06_jianying/<draft>.zip`` and return public job-relative paths."""

    ep_path = job_dir / "02_rw" / "episode.json"
    if not ep_path.is_file():
        raise FileNotFoundError("episode.json not found; please finish the preview prerequisites first")

    episode = json.loads(ep_path.read_text(encoding="utf-8"))
    beats = [b for b in episode.get("beats") or [] if isinstance(b, dict) and str(b.get("scene") or "")]
    if not beats:
        raise ValueError("episode.json has no beats")

    safe_name = _safe_name(title or episode.get("meta", {}).get("title") or f"opus-{job_id}")
    draft_name = f"{safe_name}-{job_id}-jianying"
    out_root = job_dir / "06_jianying"
    draft_dir = out_root / draft_name
    shutil.rmtree(draft_dir, ignore_errors=True)
    (draft_dir / "materials" / "images").mkdir(parents=True, exist_ok=True)
    (draft_dir / "materials" / "audio").mkdir(parents=True, exist_ok=True)
    (draft_dir / "materials" / "subtitles").mkdir(parents=True, exist_ok=True)

    scene_ranges = _scene_ranges(beats)
    total_us = sum(item["duration_us"] for item in scene_ranges.values())
    if total_us <= 0:
        raise ValueError("episode duration is empty")

    image_tracks: list[dict[str, Any]] = []
    video_materials: list[dict[str, Any]] = []
    audio_materials: list[dict[str, Any]] = []
    text_materials: list[dict[str, Any]] = []
    speeds: list[dict[str, Any]] = []
    manifest_assets: list[dict[str, Any]] = []

    background_rel = _stage_background_rel(episode)
    background_src = _resolve_image(job_dir, background_rel) or _resolve_image(job_dir, "background.webp")
    if background_src is not None:
        copied = _copy_asset(background_src, draft_dir / "materials" / "images" / background_src.name)
        mat_id, speed_id = _add_video_segment(
            image_tracks,
            video_materials,
            speeds,
            track_name="background",
            path=copied,
            start_us=0,
            duration_us=total_us,
            render_index=0,
            transform_x=0,
            transform_y=0,
            scale=1.35,
        )
        manifest_assets.append({"kind": "background", "material_id": mat_id, "speed_id": speed_id, "path": str(copied)})

    # Primary scene pictures, one clip per scene. These are usually clean stage
    # backgrounds generated for the shot family and make the draft readable even
    # before detailed foreground editing.
    for scene, info in scene_ranges.items():
        src = _resolve_image(job_dir, f"{scene}.webp")
        if src is None:
            source_scene = _first_source_scene(episode, scene)
            src = _resolve_image(job_dir, f"{source_scene}.webp") if source_scene else None
        if src is None:
            continue
        copied = _copy_asset(src, draft_dir / "materials" / "images" / src.name)
        mat_id, speed_id = _add_video_segment(
            image_tracks,
            video_materials,
            speeds,
            track_name="scene_images",
            path=copied,
            start_us=info["start_us"],
            duration_us=info["duration_us"],
            render_index=10,
            transform_x=0,
            transform_y=0,
            scale=1.35,
        )
        manifest_assets.append({"kind": "scene", "scene": scene, "material_id": mat_id, "speed_id": speed_id, "path": str(copied)})

    # Foreground assets from visual.shots. Put them on separate overlay tracks by
    # asset slot so clips in the same beat can overlap.
    shot_time = _shot_time_ranges(beats, scene_ranges)
    for shot in _visual_shots(episode):
        shot_id = str(shot.get("shotId") or "")
        t_range = shot_time.get(int(shot.get("beatIndex") or 0))
        if not shot_id or t_range is None:
            continue
        for asset_idx, asset in enumerate(shot.get("assets") or []):
            if not isinstance(asset, dict):
                continue
            src = _resolve_image(job_dir, str(asset.get("imageFile") or ""))
            if src is None:
                continue
            copied = _copy_asset(src, draft_dir / "materials" / "images" / src.name)
            pos = asset.get("pos") if isinstance(asset.get("pos"), dict) else {}
            size = _float(asset.get("size"), 24.0)
            mat_id, speed_id = _add_video_segment(
                image_tracks,
                video_materials,
                speeds,
                track_name=f"foreground_{asset_idx + 1}",
                path=copied,
                start_us=t_range["start_us"],
                duration_us=max(1, t_range["duration_us"]),
                render_index=100 + asset_idx,
                transform_x=(_float(pos.get("x"), 50.0) - 50.0) / 50.0,
                transform_y=(50.0 - _float(pos.get("y"), 50.0)) / 50.0,
                scale=max(0.05, size / 100.0),
            )
            manifest_assets.append({
                "kind": "foreground",
                "shot_id": shot_id,
                "asset_id": asset.get("id"),
                "material_id": mat_id,
                "speed_id": speed_id,
                "path": str(copied),
                "target": t_range,
                "pos": pos,
                "size": size,
                "motion": asset.get("motion"),
            })

    audio_segments: list[dict[str, Any]] = []
    audio_track = _new_track("audio", "scene_audio", 0)
    for scene, info in scene_ranges.items():
        audio_src = _resolve_audio(job_dir, scene)
        if audio_src is None:
            continue
        copied = _copy_asset(audio_src, draft_dir / "materials" / "audio" / audio_src.name)
        mat_id = _id()
        speed_id = _id()
        duration_us = info["duration_us"]
        audio_materials.append(_audio_material(mat_id, copied, duration_us))
        speeds.append(_speed(speed_id))
        audio_track["segments"].append(_audio_segment(mat_id, speed_id, info["start_us"], duration_us))
        audio_segments.append({"scene": scene, "material_id": mat_id, "speed_id": speed_id, "path": str(copied), **info})

    subtitle_segments = _subtitle_segments(beats, scene_ranges)
    srt_path = draft_dir / "materials" / "subtitles" / "subtitles.srt"
    srt_path.write_text(_to_srt(subtitle_segments), encoding="utf-8")
    text_track = _new_track("text", "subtitles", 15000)
    for seg in subtitle_segments:
        mat_id = _id()
        text_materials.append(_text_material(mat_id, seg["text"]))
        text_track["segments"].append(_text_segment(mat_id, seg["start_us"], seg["duration_us"]))

    tracks = [t for t in image_tracks if t["segments"]]
    if audio_track["segments"]:
        tracks.append(audio_track)
    if text_track["segments"]:
        tracks.append(text_track)

    content = _draft_content(
        draft_name=draft_name,
        duration_us=total_us,
        tracks=tracks,
        video_materials=video_materials,
        audio_materials=audio_materials,
        text_materials=text_materials,
        speeds=speeds,
    )
    meta = _draft_meta(
        draft_name=draft_name,
        draft_dir=draft_dir,
        duration_us=total_us,
        video_materials=video_materials,
        audio_materials=audio_materials,
    )

    (draft_dir / "draft_content.json").write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    (draft_dir / "draft_meta_info.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (draft_dir / "timeline_manifest.json").write_text(
        json.dumps({
            "job_id": job_id,
            "draft_name": draft_name,
            "duration_us": total_us,
            "scenes": scene_ranges,
            "audio": audio_segments,
            "subtitles": subtitle_segments,
            "assets": manifest_assets,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (draft_dir / "README.txt").write_text(
        "剪映草稿包：包含 draft_content.json、draft_meta_info.json、素材、subtitles.srt 和 timeline_manifest.json。\n"
        "不同剪映桌面版本的草稿 JSON 兼容性可能不同；如果不能直接识别，可导入 materials/audio、materials/images 和 subtitles.srt 后按 timeline_manifest.json 对齐。\n",
        encoding="utf-8",
    )

    zip_path = out_root / f"{draft_name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(draft_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(out_root))

    return {
        "ok": True,
        "job_id": job_id,
        "draft_name": draft_name,
        "draft_dir_relpath": draft_dir.relative_to(job_dir).as_posix(),
        "zip_relpath": zip_path.relative_to(job_dir).as_posix(),
        "download_url": f"/jobs/{job_id}/files/{zip_path.relative_to(job_dir).as_posix()}",
        "duration_us": total_us,
        "scene_count": len(scene_ranges),
        "subtitle_count": len(subtitle_segments),
        "asset_count": len(manifest_assets),
    }


def _scene_ranges(beats: list[dict[str, Any]]) -> "OrderedDict[str, dict[str, int]]":
    scene_audio_ms: "OrderedDict[str, int]" = OrderedDict()
    for beat in beats:
        scene = str(beat.get("scene") or "")
        if not scene:
            continue
        end_ms = int(_float(beat.get("audioEnd"), _float(beat.get("audioStart"), 0.0) + 1800.0))
        scene_audio_ms[scene] = max(scene_audio_ms.get(scene, 0), end_ms)

    out: "OrderedDict[str, dict[str, int]]" = OrderedDict()
    cursor_us = 0
    for scene, duration_ms in scene_audio_ms.items():
        duration_us = max(1, duration_ms * US_PER_MS)
        out[scene] = {"start_us": cursor_us, "duration_us": duration_us}
        cursor_us += duration_us
    return out


def _shot_time_ranges(beats: list[dict[str, Any]], scene_ranges: dict[str, dict[str, int]]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for index, beat in enumerate(beats, start=1):
        scene = str(beat.get("scene") or "")
        base = scene_ranges.get(scene)
        if base is None:
            continue
        start_ms = int(_float(beat.get("audioStart"), 0.0))
        end_ms = int(_float(beat.get("audioEnd"), start_ms + 1800.0))
        start_us = base["start_us"] + start_ms * US_PER_MS
        out[index] = {"start_us": start_us, "duration_us": max(1, (end_ms - start_ms) * US_PER_MS)}
    return out


def _subtitle_segments(beats: list[dict[str, Any]], scene_ranges: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for beat in beats:
        text = str(beat.get("zh") or "").strip()
        scene = str(beat.get("scene") or "")
        base = scene_ranges.get(scene)
        if not text or base is None:
            continue
        start_ms = int(_float(beat.get("audioStart"), 0.0))
        end_ms = int(_float(beat.get("audioEnd"), start_ms + 1800.0))
        out.append({
            "scene": scene,
            "start_us": base["start_us"] + start_ms * US_PER_MS,
            "duration_us": max(1, (end_ms - start_ms) * US_PER_MS),
            "text": text,
        })
    return out


def _visual_shots(episode: dict[str, Any]) -> list[dict[str, Any]]:
    visual = episode.get("visual") if isinstance(episode.get("visual"), dict) else {}
    shots = visual.get("shots") if isinstance(visual.get("shots"), list) else []
    return [s for s in shots if isinstance(s, dict)]


def _stage_background_rel(episode: dict[str, Any]) -> str:
    visual = episode.get("visual") if isinstance(episode.get("visual"), dict) else {}
    stage = visual.get("stage") if isinstance(visual.get("stage"), dict) else {}
    background = stage.get("background") if isinstance(stage.get("background"), dict) else {}
    return str(background.get("imageFile") or "")


def _first_source_scene(episode: dict[str, Any], scene: str) -> str | None:
    for shot in _visual_shots(episode):
        if str(shot.get("group") or "") == scene and shot.get("sourceScene"):
            return str(shot["sourceScene"])
    return None


def _resolve_image(job_dir: Path, rel: str) -> Path | None:
    rel = rel.replace("\\", "/").strip()
    if not rel:
        return None
    candidates = []
    if rel.startswith("pictures/"):
        candidates.append(job_dir / "03_image" / rel.removeprefix("pictures/"))
    candidates.append(job_dir / "03_image" / rel)
    if not Path(rel).suffix:
        candidates.append(job_dir / "03_image" / f"{rel}.webp")
    for path in candidates:
        if path.is_file():
            return path
    return None


def _resolve_audio(job_dir: Path, scene: str) -> Path | None:
    for name in (f"scene-{scene}.mp3", f"{scene}.mp3", f"scene-{scene}.wav", f"{scene}.wav"):
        path = job_dir / "04_tts" / name
        if path.is_file():
            return path
    return None


def _copy_asset(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst.resolve()


def _add_video_segment(
    tracks: list[dict[str, Any]],
    materials: list[dict[str, Any]],
    speeds: list[dict[str, Any]],
    *,
    track_name: str,
    path: Path,
    start_us: int,
    duration_us: int,
    render_index: int,
    transform_x: float,
    transform_y: float,
    scale: float,
) -> tuple[str, str]:
    material_id = _id()
    speed_id = _id()
    width, height = _image_size(path)
    materials.append(_video_material(material_id, path, duration_us, width, height))
    speeds.append(_speed(speed_id))
    track = _ensure_track(tracks, "video", track_name, render_index)
    track["segments"].append(_video_segment(material_id, speed_id, start_us, duration_us, render_index, transform_x, transform_y, scale))
    return material_id, speed_id


def _ensure_track(tracks: list[dict[str, Any]], track_type: str, name: str, render_index: int) -> dict[str, Any]:
    for track in tracks:
        if track["type"] == track_type and track["name"] == name:
            return track
    track = _new_track(track_type, name, render_index)
    tracks.append(track)
    return track


def _new_track(track_type: str, name: str, render_index: int) -> dict[str, Any]:
    return {
        "attribute": 0,
        "flag": 0,
        "id": _id(),
        "is_default_name": False,
        "name": name,
        "render_index": render_index,
        "segments": [],
        "type": track_type,
    }


def _video_segment(
    material_id: str,
    speed_id: str,
    start_us: int,
    duration_us: int,
    render_index: int,
    transform_x: float,
    transform_y: float,
    scale: float,
) -> dict[str, Any]:
    return {
        **_base_segment(material_id, start_us, duration_us),
        "source_timerange": _timerange(0, duration_us),
        "speed": 1.0,
        "volume": 1.0,
        "extra_material_refs": [speed_id],
        "is_tone_modify": False,
        "clip": _clip(transform_x, transform_y, scale),
        "uniform_scale": {"on": True, "value": 1.0},
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        "render_index": render_index,
    }


def _audio_segment(material_id: str, speed_id: str, start_us: int, duration_us: int) -> dict[str, Any]:
    return {
        **_base_segment(material_id, start_us, duration_us),
        "source_timerange": _timerange(0, duration_us),
        "speed": 1.0,
        "volume": 1.0,
        "extra_material_refs": [speed_id],
        "is_tone_modify": False,
        "clip": None,
        "hdr_settings": None,
        "render_index": 0,
    }


def _text_segment(material_id: str, start_us: int, duration_us: int) -> dict[str, Any]:
    return {
        **_base_segment(material_id, start_us, duration_us),
        "source_timerange": None,
        "speed": 1.0,
        "volume": 1.0,
        "extra_material_refs": [],
        "is_tone_modify": False,
        "clip": _clip(0.0, -0.78, 1.0),
        "uniform_scale": {"on": True, "value": 1.0},
        "render_index": 15000,
    }


def _base_segment(material_id: str, start_us: int, duration_us: int) -> dict[str, Any]:
    return {
        "id": _id(),
        "material_id": material_id,
        "target_timerange": _timerange(start_us, duration_us),
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "last_nonzero_volume": 1.0,
        "reverse": False,
        "track_attribute": 0,
        "track_render_index": 0,
        "visible": True,
        "common_keyframes": [],
        "keyframe_refs": [],
    }


def _timerange(start_us: int, duration_us: int) -> dict[str, int]:
    return {"start": int(start_us), "duration": int(duration_us)}


def _clip(transform_x: float, transform_y: float, scale: float) -> dict[str, Any]:
    return {
        "alpha": 1.0,
        "flip": {"horizontal": False, "vertical": False},
        "rotation": 0.0,
        "scale": {"x": scale, "y": scale},
        "transform": {"x": transform_x, "y": transform_y},
    }


def _video_material(material_id: str, path: Path, duration_us: int, width: int, height: int) -> dict[str, Any]:
    return {
        "audio_fade": None,
        "category_id": "",
        "category_name": "local",
        "check_flag": 63487,
        "crop": {
            "upper_left_x": 0.0,
            "upper_left_y": 0.0,
            "upper_right_x": 1.0,
            "upper_right_y": 0.0,
            "lower_left_x": 0.0,
            "lower_left_y": 1.0,
            "lower_right_x": 1.0,
            "lower_right_y": 1.0,
        },
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "duration": duration_us,
        "height": height,
        "id": material_id,
        "local_material_id": "",
        "material_id": material_id,
        "material_name": path.name,
        "media_path": "",
        "path": str(path),
        "type": "photo",
        "width": width,
    }


def _audio_material(material_id: str, path: Path, duration_us: int) -> dict[str, Any]:
    return {
        "app_id": 0,
        "category_id": "",
        "category_name": "local",
        "check_flag": 3,
        "copyright_limit_type": "none",
        "duration": duration_us,
        "effect_id": "",
        "formula_id": "",
        "id": material_id,
        "local_material_id": material_id,
        "music_id": material_id,
        "name": path.name,
        "path": str(path),
        "source_platform": 0,
        "type": "extract_music",
        "wave_points": [],
    }


def _text_material(material_id: str, text: str) -> dict[str, Any]:
    content = {
        "styles": [{
            "fill": {
                "alpha": 1.0,
                "content": {
                    "render_type": "solid",
                    "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]},
                },
            },
            "range": [0, len(text)],
            "size": 7.2,
            "bold": True,
            "italic": False,
            "underline": False,
            "strokes": [{
                "content": {"solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]}},
                "width": 0.08,
            }],
        }],
        "text": text,
    }
    return {
        "id": material_id,
        "content": json.dumps(content, ensure_ascii=False),
        "typesetting": 0,
        "alignment": 1,
        "letter_spacing": 0,
        "line_spacing": 0.02,
        "line_feed": 1,
        "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": 15,
        "type": "subtitle",
        "global_alpha": 1.0,
    }


def _speed(speed_id: str) -> dict[str, Any]:
    return {"curve_speed": None, "id": speed_id, "mode": 0, "speed": 1.0, "type": "speed"}


def _materials(video: list[dict[str, Any]], audio: list[dict[str, Any]], text: list[dict[str, Any]], speeds: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ai_translates": [],
        "audio_balances": [],
        "audio_effects": [],
        "audio_fades": [],
        "audio_track_indexes": [],
        "audios": audio,
        "beats": [],
        "canvases": [],
        "chromas": [],
        "color_curves": [],
        "digital_humans": [],
        "drafts": [],
        "effects": [],
        "flowers": [],
        "green_screens": [],
        "handwrites": [],
        "hsl": [],
        "images": [],
        "log_color_wheels": [],
        "loudnesses": [],
        "manual_deformations": [],
        "masks": [],
        "material_animations": [],
        "material_colors": [],
        "multi_language_refs": [],
        "placeholders": [],
        "plugin_effects": [],
        "primary_color_wheels": [],
        "realtime_denoises": [],
        "shapes": [],
        "smart_crops": [],
        "smart_relights": [],
        "sound_channel_mappings": [],
        "speeds": speeds,
        "stickers": [],
        "tail_leaders": [],
        "text_templates": [],
        "texts": text,
        "time_marks": [],
        "transitions": [],
        "video_effects": [],
        "video_trackings": [],
        "videos": video,
        "vocal_beautifys": [],
        "vocal_separations": [],
    }


def _draft_content(
    *,
    draft_name: str,
    duration_us: int,
    tracks: list[dict[str, Any]],
    video_materials: list[dict[str, Any]],
    audio_materials: list[dict[str, Any]],
    text_materials: list[dict[str, Any]],
    speeds: list[dict[str, Any]],
) -> dict[str, Any]:
    now = int(time.time())
    for track in tracks:
        render_index = int(track.pop("render_index", 0))
        for segment in track.get("segments") or []:
            segment.setdefault("render_index", render_index)
    return {
        "canvas_config": {"height": CANVAS_HEIGHT, "ratio": "original", "width": CANVAS_WIDTH},
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": 1,
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "maintrack_adsorb": True,
            "material_save_mode": 0,
            "multi_language_current": "none",
            "multi_language_list": [],
            "multi_language_main": "none",
            "multi_language_mode": "none",
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": 1,
            "subtitle_keywords_config": None,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
            "zoom_info_params": None,
        },
        "cover": None,
        "create_time": now,
        "duration": duration_us,
        "extra_info": None,
        "fps": FPS,
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": str(uuid4()).upper(),
        "keyframe_graph_list": [],
        "keyframes": {"adjusts": [], "audios": [], "effects": [], "filters": [], "handwrites": [], "stickers": [], "texts": [], "videos": []},
        "last_modified_platform": _platform(),
        "materials": _materials(video_materials, audio_materials, text_materials, speeds),
        "mutable_config": None,
        "name": draft_name,
        "new_version": "103.0.0",
        "platform": _platform(),
        "relationships": [],
        "render_index_track_mode_on": False,
        "retouch_cover": None,
        "source": "default",
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": tracks,
        "update_time": now,
        "version": 360000,
    }


def _draft_meta(
    *,
    draft_name: str,
    draft_dir: Path,
    duration_us: int,
    video_materials: list[dict[str, Any]],
    audio_materials: list[dict[str, Any]],
) -> dict[str, Any]:
    now_us = int(time.time() * 1_000_000)
    return {
        "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_materials": [],
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {
            "draft_enterprise_extra": "",
            "draft_enterprise_id": "",
            "draft_enterprise_name": "",
            "enterprise_material": [],
        },
        "draft_fold_path": str(draft_dir),
        "draft_id": _id(),
        "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False,
        "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False,
        "draft_is_from_deeplink": "false",
        "draft_is_invisible": False,
        "draft_materials": [
            {"type": 0, "value": video_materials},
            {"type": 1, "value": []},
            {"type": 2, "value": []},
            {"type": 3, "value": []},
            {"type": 6, "value": []},
            {"type": 7, "value": []},
            {"type": 8, "value": audio_materials},
        ],
        "draft_materials_copied_info": [],
        "draft_name": draft_name,
        "draft_new_version": "103.0.0",
        "draft_removable_storage_device": "",
        "draft_root_path": str(draft_dir.parent),
        "draft_segment_extra_info": [],
        "draft_timeline_materials_size_": 0,
        "draft_type": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_modified": 0,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": duration_us,
    }


def _platform() -> dict[str, Any]:
    return {
        "app_id": 3704,
        "app_source": "lv",
        "app_version": "5.5.0",
        "device_id": "",
        "hard_disk_id": "",
        "mac_address": "",
        "os": "mac",
        "os_version": "",
    }


def _to_srt(subtitles: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, item in enumerate(subtitles, start=1):
        start_us = int(item["start_us"])
        end_us = start_us + int(item["duration_us"])
        blocks.append(f"{index}\n{_srt_ts(start_us)} --> {_srt_ts(end_us)}\n{item['text']}\n")
    return "\n".join(blocks)


def _srt_ts(us: int) -> str:
    ms = max(0, us // 1000)
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in value.strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return (cleaned or "opus")[:48]


def _id() -> str:
    return uuid4().hex
