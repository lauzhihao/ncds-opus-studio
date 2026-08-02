"""Assembled backend acceptance for the Shenkuo film script source v2.

The production collector runs in a separate interpreter.  OCR and Chinese
correction use its documented dependency seams, while ffmpeg/ffprobe and the
artifact filesystem remain real.  This deliberately exercises an assembled
process/artifact boundary instead of an isolated implementation function.
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

TARGET_TEXT = "恐怖分子占领白宫"
OCR_VARIANTS = (
    "恐怖分孑占领白官",
    "恐怖份子攻占白宫",
    "孔怖分子控制白宫",
    "恐布份孑站领白官",
)


def _repo_root() -> Path:
    return Path(
        os.environ.get(
            "NOF_FILM_SCRIPT_SOURCE_V2_ACCEPTANCE_REPO_ROOT",
            Path(__file__).resolve().parents[2],
        )
    ).resolve()


def _require_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for film script acceptance")
    return ffmpeg


def _make_video(ffmpeg: str, output: Path) -> None:
    subprocess.run(  # noqa: S603 - fixed argv and pytest-owned output path.
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
            "7",
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


def _run_collector_subprocess(
    repo_root: Path,
    run_root: Path,
    video_path: Path,
    *,
    cleaner_mode: str,
    frame_texts: tuple[str, ...] = OCR_VARIANTS,
) -> dict[str, Any]:
    result_path = run_root / "result.json"
    script = r'''
import json
import sys
from pathlib import Path

from ncds_opus_factory.commands import film_subtitles

frame_texts = json.loads(sys.argv[1])
video_path = Path(sys.argv[2])
job_dir = Path(sys.argv[3])
result_path = Path(sys.argv[4])
cleaner_mode = sys.argv[5]


class FakeOcrResult:
    def __init__(self, text):
        self.txts = [text]
        self.scores = [0.93]
        self.boxes = [[[10, 10], [100, 10], [100, 30], [10, 30]]]


class FakeOcr:
    def __init__(self):
        self.index = 0

    def __call__(self, _path, *, use_cls=False):
        group = min(self.index, len(frame_texts) - 1)
        self.index += 1
        return FakeOcrResult(frame_texts[group])


film_subtitles.OCR_ENGINE_FACTORY = FakeOcr
cleaner_inputs = []


def deterministic_cleaner(cues, asr_timeline, on_progress):
    cleaner_inputs.append({
        "cue_count": len(cues),
        "texts": [str(cue.get("text") or cue.get("ocr_text") or "") for cue in cues],
        "asr_timeline": asr_timeline,
    })
    on_progress("film clean: deterministic fixture")
    return (
        {
            str(cue["cue_id"]): {
                "text": (
                    "The terrorists captured the White House"
                    if cleaner_mode == "english"
                    else "恐怖分子占领白宫"
                ),
                "confidence": 0.99,
            }
            for cue in cues
        },
        "fixture-cleaner",
        [],
    )


def unavailable_cleaner(cues, asr_timeline, on_progress):
    cleaner_inputs.append({
        "cue_count": len(cues),
        "texts": [str(cue.get("text") or cue.get("ocr_text") or "") for cue in cues],
        "asr_timeline": asr_timeline,
    })
    on_progress("film clean: unavailable fixture")
    return {}, None, ["fixture cleaner unavailable"]


film_subtitles.CLEAN_SCRIPT_AGENT = (
    unavailable_cleaner if cleaner_mode == "unavailable" else deterministic_cleaner
)
collected = film_subtitles.collect_film_subtitles(
    job_dir,
    [str(video_path)],
    on_progress=lambda text: None,
)
result_path.write_text(
    json.dumps(
        {"collected": collected, "cleaner_inputs": cleaner_inputs},
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
'''
    env = os.environ.copy()
    env["NOF_STATE_DIR"] = str(run_root / "state" / "tasks")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root / "src"), str(repo_root / "packages" / "core" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    completed = subprocess.run(  # noqa: S603 - fixed child code and temp paths.
        [
            sys.executable,
            "-c",
            script,
            json.dumps(frame_texts, ensure_ascii=False),
            str(video_path),
            str(run_root / "video-jobs" / "fixture-job"),
            str(result_path),
            cleaner_mode,
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        "film script collector subprocess failed\n"
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


def _normal_text(value: object) -> str:
    return "".join(
        char
        for char in str(value)
        if char not in " \t\r\n，。！？、；：,.!?;:'\"“”‘’（）()《》【】[]-"
    )


def _assert_v2_artifacts(
    result: dict[str, Any],
    repo_root: Path,
    *,
    expected_text: str | None,
    needs_review: bool,
) -> None:
    collected = result["collected"]
    assert collected["items"] == collected["collected"]
    assert len(collected["collected"]) == 1
    entry = collected["collected"][0]
    source = entry["film_source"]
    assert source["mode"] == "film_script_source"
    assert source["version"] == 2
    assert source["language"] == "zh-CN"
    assert "film_commentary" not in json.dumps(source, ensure_ascii=False)
    assert "film_localization" not in json.dumps(source, ensure_ascii=False)

    raw_ocr = source["raw_ocr"]
    clean_script = source["clean_script"]
    raw_json_path = _artifact_path(repo_root, raw_ocr["json"])
    clean_paths = {
        name: _artifact_path(repo_root, clean_script[name])
        for name in ("json", "srt", "txt", "report")
    }
    assert raw_json_path.is_file()
    assert all(path.is_file() for path in clean_paths.values())

    raw_doc = _read_json(raw_json_path)
    raw_text = json.dumps(raw_doc, ensure_ascii=False)
    for variant in OCR_VARIANTS:
        assert variant in raw_text

    clean_doc = _read_json(clean_paths["json"])
    clean_cues = clean_doc["cues"]
    assert clean_cues
    required = {
        "cue_id",
        "start_ms",
        "end_ms",
        "text",
        "source_cue_ids",
        "confidence",
    }
    assert all(required.issubset(cue) for cue in clean_cues)
    assert all(cue["source_cue_ids"] for cue in clean_cues)
    assert all(
        _normal_text(left["text"]) != _normal_text(right["text"])
        for left, right in zip(clean_cues, clean_cues[1:])
    )

    clean_text = clean_paths["txt"].read_text(encoding="utf-8")
    assert entry["text"] == clean_text
    report = _read_json(clean_paths["report"])
    review_count = int(report.get("needs_review") or 0)
    if needs_review:
        assert review_count > 0
    else:
        assert review_count == 0

    if expected_text is not None:
        assert len(clean_cues) == 1
        cue = clean_cues[0]
        assert cue["text"] == expected_text
        assert 5_500 <= int(cue["end_ms"]) - int(cue["start_ms"]) <= 7_500
        assert len(cue["source_cue_ids"]) == 4
        assert clean_text == f"{expected_text}\n"


def _clean_artifacts(result: dict[str, Any], repo_root: Path) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    entry = result["collected"]["collected"][0]
    source = entry["film_source"]
    raw_path = _artifact_path(repo_root, source["raw_ocr"]["json"])
    clean_path = _artifact_path(repo_root, source["clean_script"]["json"])
    return entry, source, raw_path, clean_path


def test_film_script_source_v2_corrects_and_merges_ocr_at_shenkuo_boundary(
    tmp_path: Path,
) -> None:
    """Four raw OCR variants become one clean, provenance-preserving v2 cue."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "film.mp4"
    _make_video(ffmpeg, video)

    result = _run_collector_subprocess(
        repo_root,
        tmp_path / "available",
        video,
        cleaner_mode="deterministic",
        frame_texts=tuple(
            text for variant in OCR_VARIANTS for text in (variant,) * 4
        ),
    )
    assert result["cleaner_inputs"]
    assert result["cleaner_inputs"][0]["cue_count"] == 4
    assert result["cleaner_inputs"][0]["texts"] == list(OCR_VARIANTS)
    _assert_v2_artifacts(
        result,
        repo_root,
        expected_text=TARGET_TEXT,
        needs_review=False,
    )


def test_film_script_source_v2_keeps_deterministic_baseline_when_cleaner_unavailable(
    tmp_path: Path,
) -> None:
    """Unavailable models leave a reviewable v2 baseline instead of old flows."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "film.mp4"
    _make_video(ffmpeg, video)

    result = _run_collector_subprocess(
        repo_root,
        tmp_path / "unavailable",
        video,
        cleaner_mode="unavailable",
        frame_texts=tuple(
            text for variant in OCR_VARIANTS for text in (variant,) * 4
        ),
    )
    assert result["cleaner_inputs"]
    _assert_v2_artifacts(
        result,
        repo_root,
        expected_text=None,
        needs_review=True,
    )


def test_film_script_source_v2_does_not_merge_same_clean_text_across_long_gap(
    tmp_path: Path,
) -> None:
    """A subtitle absence longer than one second is a semantic boundary."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "film.mp4"
    _make_video(ffmpeg, video)
    frame_texts = (
        (OCR_VARIANTS[0],) * 4
        + ("",) * 3
        + (OCR_VARIANTS[1],) * 7
    )

    result = _run_collector_subprocess(
        repo_root,
        tmp_path / "gap",
        video,
        cleaner_mode="deterministic",
        frame_texts=frame_texts,
    )
    assert result["cleaner_inputs"][0]["cue_count"] == 2
    _entry, _source, _raw_path, clean_path = _clean_artifacts(result, repo_root)
    clean_cues = _read_json(clean_path)["cues"]
    assert [cue["text"] for cue in clean_cues] == [TARGET_TEXT, TARGET_TEXT]
    assert all(len(cue["source_cue_ids"]) == 1 for cue in clean_cues)
    assert int(clean_cues[1]["start_ms"]) - int(clean_cues[0]["end_ms"]) > 1_000


def test_film_script_source_v2_rejects_non_cjk_cleaner_output(
    tmp_path: Path,
) -> None:
    """An English correction cannot replace the OCR-grounded Chinese baseline."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "film.mp4"
    _make_video(ffmpeg, video)
    frame_texts = tuple(
        text for variant in OCR_VARIANTS for text in (variant,) * 4
    )

    result = _run_collector_subprocess(
        repo_root,
        tmp_path / "english",
        video,
        cleaner_mode="english",
        frame_texts=frame_texts,
    )
    _assert_v2_artifacts(
        result,
        repo_root,
        expected_text=None,
        needs_review=True,
    )
    _entry, _source, raw_path, clean_path = _clean_artifacts(result, repo_root)
    raw_by_id = {
        str(cue["cue_id"]): str(cue["text"])
        for cue in _read_json(raw_path)["cues"]
    }
    clean_cues = _read_json(clean_path)["cues"]
    assert len(clean_cues) == len(raw_by_id)
    assert all(len(cue["source_cue_ids"]) == 1 for cue in clean_cues)
    assert all(
        cue["text"] == raw_by_id[cue["source_cue_ids"][0]]
        for cue in clean_cues
    )
    assert all(
        any("\u3400" <= char <= "\u9fff" for char in cue["text"])
        for cue in clean_cues
    )
    clean_text = _artifact_path(
        repo_root,
        result["collected"]["collected"][0]["film_source"]["clean_script"]["txt"],
    ).read_text(encoding="utf-8")
    assert "The terrorists captured the White House" not in clean_text
