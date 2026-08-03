"""Assembled acceptance for the ASR-first ``film_script_source.v3`` flow.

The collector runs in a child interpreter against a real temporary video,
filesystem state, FFmpeg and ffprobe.  The fake OCR engine is the documented
production seam; ASR is supplied as a realistic sidecar timeline so this test
never reaches user state or an external recognizer.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _repo_root() -> Path:
    return Path(
        os.environ.get(
            "NOF_FILM_SCRIPT_SOURCE_V3_ACCEPTANCE_REPO_ROOT",
            Path(__file__).resolve().parents[2],
        )
    ).resolve()


def _require_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for film script acceptance")
    return ffmpeg


def _make_video(ffmpeg: str, output: Path, *, duration_seconds: int = 7) -> None:
    subprocess.run(  # noqa: S603 - fixed FFmpeg argv and pytest-owned output.
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=#30343b:s=640x360:r=24",
            "-t",
            str(duration_seconds),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        ],
        check=True,
    )
    assert output.is_file() and output.stat().st_size > 0


def _write_asr_timeline(video: Path) -> Path:
    timeline = {
        "segments": [
            {"id": "asr_filler_laugh", "start_ms": 0, "end_ms": 500, "text": "哈哈"},
            {"id": "asr_descent", "start_ms": 1000, "end_ms": 1900, "text": "锁降突击开始"},
            {"id": "asr_white_one", "start_ms": 2100, "end_ms": 2700, "text": "白色解说保留"},
            {"id": "asr_white_two", "start_ms": 2800, "end_ms": 3400, "text": "白色解说保留"},
            {"id": "asr_review", "start_ms": 3500, "end_ms": 3900, "text": "缺少画面证据仍要保留"},
            {"id": "asr_next", "start_ms": 5000, "end_ms": 5600, "text": "另一个白色解说"},
            {"id": "asr_dialogue", "start_ms": 6000, "end_ms": 6400, "text": "电影台词"},
            {"id": "asr_english", "start_ms": 6450, "end_ms": 6600, "text": "我们要go now"},
            {"id": "asr_filler_hesitate", "start_ms": 6650, "end_ms": 6900, "text": "呃啊"},
        ]
    }
    path = video.with_suffix(".asr.timeline.json")
    path.write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    return path


def _run_collector_subprocess(repo_root: Path, run_root: Path, video: Path) -> dict[str, Any]:
    result_path = run_root / "result.json"
    script = r'''
import json
import sys
from pathlib import Path

from ncds_opus_factory.commands import film_script_v3, film_subtitles

video = Path(sys.argv[1])
job_dir = Path(sys.argv[2])
result_path = Path(sys.argv[3])
state_root = Path(sys.argv[4])


class FakeOcrResult:
    def __init__(self, rows):
        self.txts = [row[0] for row in rows]
        self.scores = [row[1] for row in rows]
        self.boxes = [row[2] for row in rows]


def box(top):
    return [[100, top], [800, top], [800, top + 20], [100, top + 20]]


class FakeOcr:
    calls = []

    def __call__(self, path, *, use_cls=False):
        name = Path(path).name
        FakeOcr.calls.append(name)
        if name.startswith("layout_"):
            return FakeOcrResult([("布局发现字幕", 0.99, box(380))])
        frame = int(name.removeprefix("frame_").removesuffix(".jpg"))
        rows = {
            1: [("OCR独有文字绝不进稿", 0.99, box(55))],
            2: [("索降突击开始", 0.99, box(45))],
            3: [("白色解说保留", 0.98, box(55))],
            4: [],
            5: [],
            # The first Chinese line is yellow; the second has English below it.
            6: [("另一个白色解说", 0.98, box(55))],
            7: [
                ("电影台词", 0.99, box(10)),
                ("双语电影台词", 0.99, box(45)),
                ("We are coming", 0.99, box(72)),
            ],
        }[frame]
        return FakeOcrResult(rows)


def fake_color(_frame, bbox):
    return {"label": "yellow" if bbox[1] < 30 else "white", "yellow_ratio": 0.9, "white_ratio": 0.9}


film_script_v3.OCR_ENGINE_FACTORY = FakeOcr
film_script_v3.OCR_WORKERS = 1
film_script_v3._color_signature = fake_color

# v2 names can coexist in this exact source work directory, but they are not a
# valid v3 observations cache.
work_dir = state_root / "works" / "local" / film_subtitles._stable_local_id(video)
work_dir.mkdir(parents=True, exist_ok=True)
(work_dir / "film_subtitles").mkdir(exist_ok=True)
for name in ("raw.ocr.json", "clean.json", "clean.txt"):
    (work_dir / "film_subtitles" / name).write_text('{"version": 2, "cues": ["旧OCR缓存"]}', encoding="utf-8")

progress = []
collected = film_subtitles.collect_film_subtitles(job_dir, [str(video)], on_progress=progress.append)
entry = collected["collected"][0]
source = entry["film_source"]
raw_path = Path(source["raw_observations"]["json"])
if not raw_path.is_absolute():
    raw_path = Path.cwd() / raw_path
result_path.write_text(json.dumps({
    "collected": collected,
    "entry": entry,
    "source": source,
    "progress": progress,
    "ocr_calls": FakeOcr.calls,
    "raw": json.loads(raw_path.read_text(encoding="utf-8")),
}, ensure_ascii=False, indent=2), encoding="utf-8")
'''
    env = os.environ.copy()
    state_dir = run_root / "state" / "tasks"
    env["NOF_STATE_DIR"] = str(state_dir)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root / "src"), str(repo_root / "packages" / "core" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    completed = subprocess.run(  # noqa: S603 - fixed child code and pytest paths.
        [
            sys.executable,
            "-c",
            script,
            str(video),
            str(run_root / "video-jobs" / "fixture-job"),
            str(result_path),
            str(state_dir.parent),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        "film v3 collector subprocess failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return json.loads(result_path.read_text(encoding="utf-8"))


def _artifact_path(repo_root: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else repo_root / path


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_film_script_source_v3_asr_first_multimodal_reviewable_workflow(tmp_path: Path) -> None:
    """ASR candidates are selectively validated; OCR remains evidence only."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "film.mp4"
    _make_video(ffmpeg, video)
    _write_asr_timeline(video)

    result = _run_collector_subprocess(repo_root, tmp_path / "run", video)
    entry = result["entry"]
    source = result["source"]
    assert source["mode"] == "film_script_source"
    assert source["version"] == 3
    assert source["language"] == "zh-CN"
    assert entry["text"] == source["draft_text"]
    assert result["ocr_calls"]  # v2 raw/clean names were not reused as v3 observations.

    raw_path = _artifact_path(repo_root, source["raw_observations"]["json"])
    tracks_path = _artifact_path(repo_root, source["tracks"]["json"])
    script_path = _artifact_path(repo_root, source["commentary_script"]["json"])
    txt_path = _artifact_path(repo_root, source["commentary_script"]["txt"])
    report_path = _artifact_path(repo_root, source["commentary_script"]["report"])
    assert all(path.is_file() for path in (raw_path, tracks_path, script_path, txt_path, report_path))

    raw = _read_json(raw_path)
    assert raw["version"] == 3
    assert raw["observations"]
    assert "旧OCR缓存" not in json.dumps(raw, ensure_ascii=False)
    roi = source["raw_observations"]["roi"]
    assert float(roi["y"]) >= 0.62 and float(roi["height"]) >= 0.20

    # Observation boxes are remapped from the crop into original-frame coordinates.
    relevant = next(row for row in raw["observations"] if row["text"] == "索降突击开始")
    bbox = relevant["bbox_norm"]
    assert float(bbox["y"]) >= float(roi["y"])
    assert float(bbox["y"]) + float(bbox["height"]) <= float(roi["y"]) + float(roi["height"]) + 0.00001

    tracks = _read_json(tracks_path)
    assignments = {row["observation_id"]: row["source_class"] for row in tracks["assignments"]}
    by_text = {row["text"]: row["observation_id"] for row in raw["observations"]}
    assert assignments[by_text["电影台词"]] == "film_dialogue"  # yellow dialogue
    assert assignments[by_text["双语电影台词"]] == "film_dialogue"  # Chinese + English companion
    assert assignments[by_text["白色解说保留"]] == "commentary"

    script = _read_json(script_path)
    cues = script["cues"]
    cue_text = "\n".join(cue["text"] for cue in cues)
    assert "哈哈" not in cue_text and "呃啊" not in cue_text and "我们要go now" not in cue_text
    assert "电影台词" not in cue_text and "双语电影台词" not in cue_text
    assert "OCR独有文字绝不进稿" not in cue_text
    assert "索降突击开始" in cue_text  # high-confidence OCR corrects ASR's 锁降.
    assert "白色解说保留" in cue_text
    assert "缺少画面证据仍要保留" in cue_text
    assert txt_path.read_text(encoding="utf-8") == source["draft_text"]

    merged = next(cue for cue in cues if cue["text"] == "白色解说保留")
    assert merged["decision"] == "merge"
    assert merged["source_asr_segment_ids"] == ["asr_white_one", "asr_white_two"]
    assert all(cue["source_asr_segment_ids"] for cue in cues)
    assert all(cue["review_reasons"] is not None for cue in cues)
    corrected = next(cue for cue in cues if cue["text"] == "索降突击开始")
    assert corrected["decision"] == "edit" and corrected["source_observation_ids"]
    review_cue = next(cue for cue in cues if cue["text"] == "缺少画面证据仍要保留")
    assert review_cue["source_observation_ids"] == []
    assert review_cue["review_reasons"] == ["ocr_support_missing_context_kept"]

    report = _read_json(report_path)
    assert report["near_duplicate_count"] == 0
    assert report["quality_status"] == "review"
    assert report["publishable"] is False
    assert script["publishable"] is False and source["publishable"] is False
    assert source["draft_text"]  # review state is a visible draft, not a dropped artifact.
    assert report["drop_reason_counts"]["short_speech_or_filler"] == 2
    assert report["drop_reason_counts"]["non_chinese_source_audio"] == 1
    assert report["drop_reason_counts"]["film_dialogue_visual_track"] == 1
