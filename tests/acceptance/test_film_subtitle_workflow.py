"""Real OCR acceptance for the film subtitle domain.

This test deliberately crosses process, ffmpeg, OCR, and filesystem boundaries.
It is opt-in because RapidOCR model initialization is comparatively expensive.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont

RUN_ACCEPTANCE = os.environ.get("NOF_RUN_FILM_OCR_ACCEPTANCE") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_ACCEPTANCE,
    reason="set NOF_RUN_FILM_OCR_ACCEPTANCE=1 to run real film OCR acceptance",
)

NARRATION_ONE = "大家好我是小明"
DIALOGUE = "快跑快跑"
NARRATION_TWO = "真相终于被发现"
EXPECTED_TEXTS = (NARRATION_ONE, DIALOGUE, NARRATION_TWO)


def _require_runtime() -> tuple[str, Path]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required for film OCR acceptance")
    if importlib.util.find_spec("rapidocr") is None:
        pytest.skip("rapidocr>=3.9.0 is required for film OCR acceptance")
    if importlib.util.find_spec("onnxruntime") is None:
        pytest.skip("onnxruntime>=1.17,<2 is required for film OCR acceptance")

    configured = os.environ.get("NOF_FILM_ACCEPTANCE_FONT")
    candidates = [
        Path(configured) if configured else None,
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ]
    font = next((path for path in candidates if path and path.is_file()), None)
    if font is None:
        pytest.skip(
            "a Chinese font is required; set NOF_FILM_ACCEPTANCE_FONT"
        )

    return ffmpeg, font


def _render_fixture(ffmpeg: str, font: Path, output: Path) -> None:
    frame_texts = (
        NARRATION_ONE,
        NARRATION_ONE,
        "",
        DIALOGUE,
        DIALOGUE,
        "",
        NARRATION_TWO,
        NARRATION_TWO,
    )
    frames_dir = output.parent / "fixture-frames"
    frames_dir.mkdir()
    subtitle_font = ImageFont.truetype(str(font), 64)
    for index, text in enumerate(frame_texts):
        frame = Image.new("RGB", (1280, 720), "#30343b")
        if text:
            draw = ImageDraw.Draw(frame)
            draw.text(
                (640, 570),
                text,
                font=subtitle_font,
                anchor="mm",
                fill="white",
                stroke_width=5,
                stroke_fill="black",
            )
        frame.save(frames_dir / f"frame-{index:02d}.png")

    subprocess.run(  # noqa: S603 - fixed argv with generated fixture values.
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            "1",
            "-i",
            str(frames_dir / "frame-%02d.png"),
            "-r",
            "25",
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
    assert output.stat().st_size > 0


def _run_production_workflow(
    repo_root: Path,
    run_root: Path,
    fixture_video: Path,
) -> dict[str, Any]:
    result_path = run_root / "subprocess-result.json"
    script = r"""
import json
import sys
from pathlib import Path

from ncds_opus_factory.commands.film_subtitles import collect_film_subtitles
from ncds_opus_factory.commands import film_commentary, film_localization

video_path = Path(sys.argv[1])
job_dir = Path(sys.argv[2])
result_path = Path(sys.argv[3])
job_dir.mkdir(parents=True, exist_ok=True)

progress = []

def record_progress(text):
    progress.append(text)
    print(text, flush=True)

collected = collect_film_subtitles(
    job_dir,
    [str(video_path)],
    on_progress=record_progress,
)
film_commentary.FILM_COMMENTARY_AGENT = lambda cues, on_progress: None
commentary = film_commentary.build_film_commentary(
    job_dir,
    collected["collected"],
    on_progress=record_progress,
)
(job_dir / "guiguzi.json").write_text(
    json.dumps(commentary, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

translation_input = []

def deterministic_translation(segments, target_language, entity_glossary):
    translation_input.extend(dict(segment) for segment in segments)
    return [
        {
            "cue_id": str(
                segment.get("cue_id")
                or segment.get("segment_key")
                or segment.get("id")
            ),
            "translated_text": f"translated:{segment['text']}",
        }
        for segment in segments
    ]

film_localization.TRANSLATION_AGENT = deterministic_translation
localized = film_localization.localize_film_script(
    job_dir,
    target_language="en",
    on_progress=record_progress,
)

legacy_dir = job_dir.parent / "legacy-job"
legacy_dir.mkdir(parents=True, exist_ok=True)
(legacy_dir / "guiguzi.json").write_text(
    json.dumps(
        {
            "mode": "film_script_split",
            "status": "done",
            "segments": [],
        },
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
legacy_error = None
try:
    film_localization.localize_film_script(
        legacy_dir,
        target_language="en",
    )
except Exception as exc:
    legacy_error = {
        "type": type(exc).__name__,
        "message": str(exc),
    }

result_path.write_text(
    json.dumps(
        {
            "collected": collected,
            "commentary": commentary,
            "localized": localized,
            "translation_input": translation_input,
            "legacy_error": legacy_error,
            "progress": progress,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
"""
    env = os.environ.copy()
    env["NOF_STATE_DIR"] = str(run_root / "state" / "tasks")
    pythonpath = [
        str(repo_root / "src"),
        str(repo_root / "packages" / "core" / "src"),
    ]
    current_pythonpath = env.get("PYTHONPATH")
    if current_pythonpath:
        pythonpath.append(current_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    completed = subprocess.run(  # noqa: S603 - current Python, fixed script.
        [
            sys.executable,
            "-c",
            script,
            str(fixture_video),
            str(run_root / "video-jobs" / "fixture-job"),
            str(result_path),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, (
        "film workflow subprocess failed\n"
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


def test_real_ocr_film_subtitle_to_commentary_contract(tmp_path: Path) -> None:
    """A burned-in subtitle video becomes deduplicated narration artifacts."""
    ffmpeg, font = _require_runtime()
    repo_root = Path(
        os.environ.get(
            "NOF_FILM_ACCEPTANCE_REPO_ROOT",
            Path(__file__).resolve().parents[2],
        )
    ).resolve()
    fixture_video = tmp_path / "film-subtitle-fixture.mp4"
    _render_fixture(ffmpeg, font, fixture_video)

    result = _run_production_workflow(repo_root, tmp_path, fixture_video)
    assert result["collected"]["items"] == result["collected"]["collected"]
    assert len(result["collected"]["collected"]) == 1

    entry = result["collected"]["collected"][0]
    source = entry["film_source"]
    assert source["mode"] == "film_subtitle_source"
    assert source["version"] == 1
    assert source["video"]
    assert source["asr_timeline"] is None

    ocr = source["ocr"]
    assert ocr["backend"]
    assert ocr["frame_sampling_fps"] == 2
    assert ocr["cue_count"] == 3

    raw_paths = {
        name: _artifact_path(repo_root, ocr[name])
        for name in ("raw_cues", "srt", "txt", "report")
    }
    assert all(path.is_file() for path in raw_paths.values())
    assert raw_paths["raw_cues"].name == "raw.json"
    assert raw_paths["srt"].name == "raw.srt"
    assert raw_paths["txt"].name == "raw.txt"
    assert raw_paths["report"].name == "report.json"
    assert raw_paths["raw_cues"].parent.name == "film_subtitles"
    assert "state/works/local/" in raw_paths["raw_cues"].as_posix()

    raw_doc = _read_json(raw_paths["raw_cues"])
    raw_cues = raw_doc["cues"]
    assert len(raw_cues) == 3
    assert [cue["text"] for cue in raw_cues] == list(EXPECTED_TEXTS)
    assert [cue["cue_id"] for cue in raw_cues] == [
        "cue_0001",
        "cue_0002",
        "cue_0003",
    ]
    assert all(
        {
            "cue_id",
            "start_ms",
            "end_ms",
            "text",
            "confidence",
            "sample_count",
        }.issubset(cue)
        for cue in raw_cues
    )
    assert all(cue["sample_count"] >= 2 for cue in raw_cues)
    assert all(
        current["end_ms"] <= following["start_ms"]
        for current, following in zip(raw_cues, raw_cues[1:])
    )
    assert raw_paths["txt"].read_text(encoding="utf-8").splitlines() == list(
        EXPECTED_TEXTS
    )
    raw_srt = raw_paths["srt"].read_text(encoding="utf-8")
    assert raw_srt.count("-->") == 3
    assert all(text in raw_srt for text in EXPECTED_TEXTS)
    report = _read_json(raw_paths["report"])
    assert report["cue_count"] == 3
    assert report["sampled_frames"] > 3

    commentary = result["commentary"]
    assert commentary["mode"] == "film_commentary"
    assert commentary["status"] == "done"
    assert [cue["kind"] for cue in commentary["cues"]] == [
        "narration",
        "dialogue",
        "narration",
    ]
    assert all(
        {
            "cue_id",
            "source_work_id",
            "start_ms",
            "end_ms",
            "ocr_text",
            "asr_text",
            "text",
            "kind",
            "confidence",
        }.issubset(cue)
        for cue in commentary["cues"]
    )

    script_paths = {
        name: _artifact_path(
            repo_root,
            tmp_path / "video-jobs" / "fixture-job" / commentary["script"][name],
        )
        for name in ("txt", "srt", "json")
    }
    qa_path = tmp_path / "video-jobs" / "fixture-job" / "film_script" / "qa.json"
    assert all(path.is_file() for path in (*script_paths.values(), qa_path))

    narration_text = script_paths["txt"].read_text(encoding="utf-8")
    assert NARRATION_ONE in narration_text
    assert NARRATION_TWO in narration_text
    assert DIALOGUE not in narration_text
    narration_doc = _read_json(script_paths["json"])
    assert all(cue["kind"] == "narration" for cue in narration_doc["cues"])
    narration_srt = script_paths["srt"].read_text(encoding="utf-8")
    assert narration_srt.count("-->") == 2
    assert DIALOGUE not in narration_srt
    qa = _read_json(qa_path)
    assert qa["raw_cues"] == 3
    assert qa["narration_cues"] == 2
    assert qa["dialogue_filtered"] == 1

    assert [cue["kind"] for cue in result["translation_input"]] == [
        "narration",
        "narration",
    ]
    assert all(cue["text"] != DIALOGUE for cue in result["translation_input"])
    assert result["localized"]["segment_count"] == 2
    assert result["legacy_error"] is not None
    assert result["legacy_error"]["type"] == "ValueError"
    assert "film commentary" in result["legacy_error"]["message"].replace(
        "_",
        " ",
    )
