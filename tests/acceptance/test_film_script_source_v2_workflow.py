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
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

TARGET_TEXT = "恐怖分子占领白宫"
WATERMARK_TEXT = "@DY影视强声"
WATERMARK_BODY_TEXT = "@DY影视强声另一边总统公布了最新消息"
CLEAN_WATERMARK_BODY_TEXT = "另一边总统公布了最新消息"
CURRENT_OCR_BACKEND = "rapidocr-onnxruntime-ppocrv6-tiny"
CURRENT_FRAME_SAMPLING_FPS = 1
LEGACY_OCR_BACKEND = "rapidocr-onnxruntime-ppocrv6-small"
LEGACY_FRAME_SAMPLING_FPS = 2
OCR_VARIANTS = (
    "恐怖分孑占领白官",
    "恐怖分孑占令百宫",
    "恐怖分子站领白宫",
    "恐怖分孑站令百官",
)

FAKE_CLEANER_CLI = r'''#!/usr/bin/env python3
import json
import os
import sys

backend = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1] if backend == "agy" else argv[-1]
rows = json.loads(prompt.splitlines()[-1])
record = {
    "backend": backend,
    "cue_ids": [str(row["cue_id"]) for row in rows],
    "size": len(rows),
}
with open(os.environ["FAKE_FILM_CLEANER_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\n")

if len(rows) > 30:
    print("simulated oversized cleaner batch", file=sys.stderr)
    raise SystemExit(17)

payload = json.dumps([
    {
        "cue_id": str(row["cue_id"]),
        "text": f"校正字幕{row['cue_id']}",
        "confidence": 0.99,
    }
    for row in rows
], ensure_ascii=False)
if backend == "scodex":
    print(json.dumps({"type": "item.completed", "item": {"text": payload}}))
else:
    print(payload)
'''


def _continuous_variant_frames() -> tuple[str, ...]:
    """Seven 1 fps samples preserve four raw variants across a 7s window."""
    return (
        OCR_VARIANTS[0],
        OCR_VARIANTS[0],
        OCR_VARIANTS[1],
        OCR_VARIANTS[1],
        OCR_VARIANTS[2],
        OCR_VARIANTS[3],
        OCR_VARIANTS[3],
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


def _make_video(
    ffmpeg: str,
    output: Path,
    *,
    duration_seconds: float = 7.0,
) -> None:
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


def _run_collector_subprocess(
    repo_root: Path,
    run_root: Path,
    video_path: Path,
    *,
    cleaner_mode: str,
    frame_texts: tuple[str, ...] = OCR_VARIANTS,
    collect_twice: bool = False,
    fake_cli_dir: Path | None = None,
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
collect_twice = sys.argv[6] == "1"
film_subtitles.OCR_WORKER_COUNT = 1


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
                    else (
                        "另一边总统公布了最新消息"
                        if cleaner_mode == "watermark"
                        else "恐怖分子占领白宫"
                    )
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


if cleaner_mode != "real_cli":
    film_subtitles.CLEAN_SCRIPT_AGENT = (
        unavailable_cleaner if cleaner_mode == "unavailable" else deterministic_cleaner
    )

progress = []
if collect_twice:
    film_subtitles.OCR_BACKEND = "rapidocr-onnxruntime-ppocrv6-small"
    film_subtitles.FRAME_SAMPLING_FPS = 2
collected = film_subtitles.collect_film_subtitles(
    job_dir,
    [str(video_path)],
    on_progress=progress.append,
)
second_collected = None
raw_cache = None
if collect_twice:
    first_source = collected["collected"][0]["film_source"]
    raw_path = Path(first_source["raw_ocr"]["json"])
    if not raw_path.is_absolute():
        raw_path = Path.cwd() / raw_path
    before = {
        "sha256": __import__("hashlib").sha256(raw_path.read_bytes()).hexdigest(),
        "mtime_ns": raw_path.stat().st_mtime_ns,
    }

    def exploding_ocr_factory():
        raise RuntimeError("OCR factory must not run on raw cache hit")

    film_subtitles.OCR_BACKEND = "rapidocr-onnxruntime-ppocrv6-tiny"
    film_subtitles.FRAME_SAMPLING_FPS = 1
    film_subtitles.OCR_WORKER_COUNT = 1
    film_subtitles.OCR_ENGINE_FACTORY = exploding_ocr_factory
    second_collected = film_subtitles.collect_film_subtitles(
        job_dir.parent / "fixture-job-second",
        [str(video_path)],
        on_progress=progress.append,
    )
    after = {
        "sha256": __import__("hashlib").sha256(raw_path.read_bytes()).hexdigest(),
        "mtime_ns": raw_path.stat().st_mtime_ns,
    }
    raw_cache = {"before": before, "after": after}
result_path.write_text(
    json.dumps(
        {
            "collected": collected,
            "second_collected": second_collected,
            "cleaner_inputs": cleaner_inputs,
            "progress": progress,
            "raw_cache": raw_cache,
        },
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
    if fake_cli_dir is not None:
        env["NOF_AGY"] = "1"
        env["FAKE_FILM_CLEANER_LOG"] = str(run_root / "cleaner-calls.jsonl")
        env["PATH"] = os.pathsep.join([str(fake_cli_dir), env.get("PATH", "")])
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
            "1" if collect_twice else "0",
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


def _install_fake_cleaner_clis(directory: Path) -> list[Path]:
    directory.mkdir(parents=True)
    paths = []
    for name in ("scodex", "agy"):
        path = directory / name
        path.write_text(FAKE_CLEANER_CLI, encoding="utf-8")
        path.chmod(
            path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        paths.append(path)
    return paths


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
    expected_ocr_backend: str = CURRENT_OCR_BACKEND,
    expected_frame_sampling_fps: int = CURRENT_FRAME_SAMPLING_FPS,
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
    assert raw_ocr["backend"] == expected_ocr_backend
    assert raw_ocr["frame_sampling_fps"] == expected_frame_sampling_fps
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
        frame_texts=_continuous_variant_frames(),
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
        frame_texts=_continuous_variant_frames(),
    )
    assert result["cleaner_inputs"]
    _assert_v2_artifacts(
        result,
        repo_root,
        expected_text=None,
        needs_review=True,
    )
    _entry, _source, _raw_path, clean_path = _clean_artifacts(result, repo_root)
    clean_cues = _read_json(clean_path)["cues"]
    assert len(clean_cues) == 1
    assert clean_cues[0]["text"] in OCR_VARIANTS
    assert clean_cues[0]["source_cue_ids"] == [
        "cue_0001",
        "cue_0002",
        "cue_0003",
        "cue_0004",
    ]
    assert 5_500 <= int(clean_cues[0]["end_ms"]) - int(clean_cues[0]["start_ms"]) <= 7_500


def test_film_script_source_v2_does_not_fuzzy_merge_across_long_gap(
    tmp_path: Path,
) -> None:
    """A subtitle absence longer than one second is a semantic boundary."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "film.mp4"
    _make_video(ffmpeg, video)
    frame_texts = (
        OCR_VARIANTS[0],
        OCR_VARIANTS[1],
        "",
        "",
        OCR_VARIANTS[0],
        OCR_VARIANTS[3],
        OCR_VARIANTS[3],
    )

    result = _run_collector_subprocess(
        repo_root,
        tmp_path / "gap",
        video,
        cleaner_mode="unavailable",
        frame_texts=frame_texts,
    )
    assert result["cleaner_inputs"][0]["cue_count"] == 4
    _entry, _source, _raw_path, clean_path = _clean_artifacts(result, repo_root)
    clean_cues = _read_json(clean_path)["cues"]
    assert len(clean_cues) == 2
    assert [len(cue["source_cue_ids"]) for cue in clean_cues] == [2, 2]
    assert int(clean_cues[1]["start_ms"]) - int(clean_cues[0]["end_ms"]) > 1_000


def test_film_script_source_v2_rejects_non_cjk_cleaner_output(
    tmp_path: Path,
) -> None:
    """An English correction cannot replace the OCR-grounded Chinese baseline."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "film.mp4"
    _make_video(ffmpeg, video)
    frame_texts = _continuous_variant_frames()

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


def test_film_script_source_v2_reuses_raw_ocr_on_second_collection(
    tmp_path: Path,
) -> None:
    """A same-source rerun re-cleans persisted raw cues without invoking OCR."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "film.mp4"
    _make_video(ffmpeg, video)
    frame_texts = tuple(
        text for variant in OCR_VARIANTS for text in (variant,) * 4
    )

    result = _run_collector_subprocess(
        repo_root,
        tmp_path / "raw-cache",
        video,
        cleaner_mode="deterministic",
        frame_texts=frame_texts,
        collect_twice=True,
    )
    assert result["second_collected"] is not None
    assert len(result["cleaner_inputs"]) == 2
    assert result["raw_cache"]["before"] == result["raw_cache"]["after"]

    second_result = {"collected": result["second_collected"]}
    _assert_v2_artifacts(
        second_result,
        repo_root,
        expected_text=TARGET_TEXT,
        needs_review=False,
        expected_ocr_backend=LEGACY_OCR_BACKEND,
        expected_frame_sampling_fps=LEGACY_FRAME_SAMPLING_FPS,
    )


def test_real_cleaner_retries_and_splits_failed_batch_in_subprocess(
    tmp_path: Path,
) -> None:
    """The real cleaner keeps one backend, splits a failed batch, and covers all cues."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "long-film.mp4"
    frame_texts = tuple(
        (
            f"天地玄黄宇第{index:03d}幕"
            if index % 2 == 0
            else f"甲乙丙丁戊第{index:03d}幕"
        )
        for index in range(70)
    )
    _make_video(ffmpeg, video, duration_seconds=len(frame_texts))
    cli_dir = tmp_path / "fake-bin"
    _install_fake_cleaner_clis(cli_dir)
    run_root = tmp_path / "real-cleaner"

    result = _run_collector_subprocess(
        repo_root,
        run_root,
        video,
        cleaner_mode="real_cli",
        frame_texts=frame_texts,
        fake_cli_dir=cli_dir,
    )
    _entry, source, _raw_path, clean_path = _clean_artifacts(result, repo_root)
    clean_cues = _read_json(clean_path)["cues"]
    report_path = _artifact_path(repo_root, source["clean_script"]["report"])
    report = _read_json(report_path)
    calls = [
        json.loads(line)
        for line in (run_root / "cleaner-calls.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    sizes = [call["size"] for call in calls]
    split_at = sizes.index(30)
    assert split_at >= 2
    assert sizes[:split_at] == [60] * split_at
    assert sizes[split_at:] == [30, 30, 10]
    assert all(call["cue_ids"] == calls[0]["cue_ids"] for call in calls[:split_at])
    assert len({call["backend"] for call in calls}) == 1
    assert report["correction_backend"] == calls[0]["backend"]
    assert report["correction_failures"] == []
    assert report["raw_cue_count"] == report["clean_cue_count"] == 70
    assert len(clean_cues) == 70
    assert len({cue["source_cue_ids"][0] for cue in clean_cues}) == 70
    assert any("splitting size=60" in message for message in result["progress"])


def test_film_script_source_v2_drops_repeated_pure_watermark_before_cleaner(
    tmp_path: Path,
) -> None:
    """Repeated pure handles remain raw evidence but never reach clean output."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "watermark-film.mp4"
    frame_texts = (
        WATERMARK_TEXT,
        "",
        "",
        "",
        WATERMARK_TEXT,
        "",
        "",
        "",
        WATERMARK_TEXT,
        "",
        "",
        "",
        WATERMARK_BODY_TEXT,
        WATERMARK_BODY_TEXT,
    )
    _make_video(ffmpeg, video, duration_seconds=len(frame_texts))

    result = _run_collector_subprocess(
        repo_root,
        tmp_path / "watermark",
        video,
        cleaner_mode="watermark",
        frame_texts=frame_texts,
    )
    assert result["cleaner_inputs"] == [
        {
            "cue_count": 1,
            "texts": [WATERMARK_BODY_TEXT],
            "asr_timeline": [],
        }
    ]

    entry, source, raw_path, clean_path = _clean_artifacts(result, repo_root)
    raw_cues = _read_json(raw_path)["cues"]
    assert [cue["text"] for cue in raw_cues] == [
        WATERMARK_TEXT,
        WATERMARK_TEXT,
        WATERMARK_TEXT,
        WATERMARK_BODY_TEXT,
    ]
    dropped_ids = [str(cue["cue_id"]) for cue in raw_cues[:3]]

    clean_cues = _read_json(clean_path)["cues"]
    assert len(clean_cues) == 1
    assert clean_cues[0]["text"] == CLEAN_WATERMARK_BODY_TEXT
    assert clean_cues[0]["source_cue_ids"] == [str(raw_cues[3]["cue_id"])]
    clean_text_path = _artifact_path(repo_root, source["clean_script"]["txt"])
    clean_text = clean_text_path.read_text(encoding="utf-8")
    assert clean_text == f"{CLEAN_WATERMARK_BODY_TEXT}\n"
    assert WATERMARK_TEXT not in clean_text
    assert entry["text"] == clean_text

    report_path = _artifact_path(repo_root, source["clean_script"]["report"])
    report = _read_json(report_path)
    assert report["dropped_source_cue_ids"] == dropped_ids
    assert report["dropped_source_cue_count"] == len(dropped_ids) == 3
