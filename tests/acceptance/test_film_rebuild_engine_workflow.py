"""Assembled backend acceptance for the frame-first film highlight recipe.

The test crosses the production InstanceRunner, InstanceStore, recipe registry,
film performers, FFmpeg, ffprobe, and filesystem artifact boundaries.  It uses
no mocks and does not depend on repository-local video jobs or user media.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

EXPECTED_FPS = Fraction(24, 1)
MASTER_FRAMES = 8 * EXPECTED_FPS.numerator
VALID_SEGMENTS = [
    {
        "segment_id": "segment-001",
        "source_start_frame": 24,
        "source_end_frame": 72,
    },
    {
        "segment_id": "segment-002",
        "source_start_frame": 120,
        "source_end_frame": 168,
    },
]
EXPECTED_OUTPUT_FRAMES = sum(segment["source_end_frame"] - segment["source_start_frame"] for segment in VALID_SEGMENTS)


@dataclass(frozen=True)
class FilmFixture:
    root: Path
    ffmpeg: str
    ffprobe: str
    master: Path
    reference: Path
    voice: Path
    subtitle: Path
    edl: Path


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv is assembled from local fixtures.
        argv,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _require_ffmpeg() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("ffmpeg and ffprobe are required for film rebuild acceptance")
    return ffmpeg, ffprobe


def _render_video_fixture(ffmpeg: str, output: Path, pattern: str) -> None:
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"{pattern}=size=320x180:rate=24:duration=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=8",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-shortest",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        ]
    )


def _render_voice_fixture(ffmpeg: str, output: Path) -> None:
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=4",
            "-af",
            "volume=0.08",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s24le",
            "-y",
            str(output),
        ]
    )


def _write_edl(path: Path, segments: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "film_frame_edl.v1",
                "fps": {"numerator": 24, "denominator": 1},
                "segments": segments,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def film_fixture(tmp_path_factory: pytest.TempPathFactory) -> FilmFixture:
    ffmpeg, ffprobe = _require_ffmpeg()
    root = tmp_path_factory.mktemp("film-rebuild-acceptance")
    master = root / "clean-master.mp4"
    reference = root / "reference.mp4"
    voice = root / "aligned-voice.wav"
    subtitle = root / "narration.ass"
    edl = root / "highlight-edl.json"

    _render_video_fixture(ffmpeg, master, "testsrc2")
    _render_video_fixture(ffmpeg, reference, "smptebars")
    _render_voice_fixture(ffmpeg, voice)
    subtitle.write_text(
        """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,18,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,14,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,First narration block
Dialogue: 0,0:00:02.00,0:00:04.00,Default,,0,0,0,,Second narration block
""",
        encoding="utf-8",
    )
    _write_edl(edl, VALID_SEGMENTS)

    assert all(
        path.is_file() and path.stat().st_size > 0
        for path in (
            master,
            reference,
            voice,
            subtitle,
            edl,
        )
    )
    return FilmFixture(
        root=root,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        master=master,
        reference=reference,
        voice=voice,
        subtitle=subtitle,
        edl=edl,
    )


def _load_runtime() -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    """Load the production assembly lazily so this file remains collectable."""
    from ncds_opus_factory.commands import build_full_registry
    from ncds_opus_factory.server.engine.instance_runner import InstanceRunner
    from ncds_opus_factory.server.engine.instance_store import InstanceStore
    from ncds_opus_factory.server.engine.pipeline_performers_film import (
        PERFORMERS_FILM,
    )
    from ncds_opus_factory.server.engine.pipeline_performers_final import (
        PERFORMERS_FINAL,
    )
    from ncds_opus_factory.server.engine.recipes import RECIPE_REGISTRY

    recipes = dict(RECIPE_REGISTRY)
    assert "film_localized_rebuild_v1" in recipes
    assert "film_highlight_v1" in recipes
    registry = {
        **build_full_registry(),
        **PERFORMERS_FINAL,
        **PERFORMERS_FILM,
    }
    for recipe_id in ("film_localized_rebuild_v1", "film_highlight_v1"):
        recipe = recipes[recipe_id]
        recipe.validate()
        for step in recipe.steps:
            if step.performer is not None:
                assert step.performer in registry, (
                    f"missing performer {step.performer!r} for {recipe_id}/{step.step_id}"
                )
    return InstanceRunner, InstanceStore, registry, recipes


def _read_json(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value)
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _artifact_file(ref: dict[str, Any], job_dir: Path) -> Path:
    uri = str(ref["uri"])
    if uri.startswith("file://"):
        uri = uri.removeprefix("file://")
    path = Path(uri)
    return path if path.is_absolute() else job_dir / path


def _assert_artifact_ref(
    ref: dict[str, Any],
    job_dir: Path,
    *,
    producer_step: str,
    input_ids: set[str] | None = None,
) -> None:
    assert ref["schema_version"]
    assert ref["artifact_id"]
    assert ref["kind"]
    assert re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])
    assert ref["producer_step"] == producer_step
    assert ref["producer_version"]
    assert isinstance(ref["input_artifact_ids"], list)
    if input_ids is not None:
        assert input_ids <= set(ref["input_artifact_ids"])

    artifact_file = _artifact_file(ref, job_dir)
    assert artifact_file.is_file(), artifact_file
    digest = hashlib.sha256(artifact_file.read_bytes()).hexdigest()
    assert digest == ref["sha256"]


def _probe_output(ffprobe: str, output: Path) -> dict[str, Any]:
    completed = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(output),
        ]
    )
    value = json.loads(completed.stdout)
    assert isinstance(value, dict)
    return value


async def _run_gate(
    runner: Any,
    instance_id: str,
    step_id: str,
    step_inputs: dict[str, Any],
) -> dict[str, Any]:
    assert step_id in runner.get_runnable_steps(instance_id)
    state = await runner.run_step(instance_id, step_id, step_inputs=step_inputs)
    assert state.status == "awaiting_review"
    assert state.decision == "pending"
    approved = await runner.approve_step(
        instance_id,
        step_id,
        "approved",
        note="acceptance approved",
        reviewer="system",
    )
    assert approved.status == "done"
    assert approved.decision == "approved"
    assert approved.review is not None
    assert approved.review.decision == "approved"
    return dict(approved.outputs)


async def _run_technical(
    runner: Any,
    instance_id: str,
    step_id: str,
    step_inputs: dict[str, Any],
) -> dict[str, Any]:
    assert step_id in runner.get_runnable_steps(instance_id)
    state = await runner.run_step(instance_id, step_id, step_inputs=step_inputs)
    assert state.status == "done", f"{step_id}: {state.error}"
    return dict(state.outputs)


def test_film_highlight_recipe_renders_frame_exact_media(
    film_fixture: FilmFixture,
    tmp_path: Path,
) -> None:
    runner_cls, store_cls, registry, recipes = _load_runtime()
    recipe = recipes["film_highlight_v1"]
    assert recipe.step_ids() == [
        "input",
        "source",
        "highlight_plan",
        "storyboard",
        "edl_review",
        "tts",
        "voice_review",
        "render",
        "quality",
        "download",
    ]
    assert [step.step_id for step in recipe.steps if step.intervention is not None] == [
        "highlight_plan",
        "edl_review",
        "voice_review",
    ]

    job_dir = tmp_path / "video-jobs" / "film-highlight"
    store = store_cls(tmp_path / "instances")
    runner = runner_cls(store, registry=registry, recipes=recipes)
    instance = runner.create_instance(
        "film_highlight_v1",
        inputs={"domain": "film"},
        title="Film highlight acceptance",
    )
    instance_id = instance.meta.instance_id

    async def _drive() -> dict[str, dict[str, Any]]:
        outputs: dict[str, dict[str, Any]] = {}
        outputs["input"] = await _run_technical(
            runner,
            instance_id,
            "input",
            {"job_dir": str(job_dir)},
        )
        outputs["source"] = await _run_technical(
            runner,
            instance_id,
            "source",
            {
                "job_dir": str(job_dir),
                "reference_path": str(film_fixture.reference),
                "master_path": str(film_fixture.master),
            },
        )
        outputs["highlight_plan"] = await _run_gate(
            runner,
            instance_id,
            "highlight_plan",
            {
                "job_dir": str(job_dir),
                "target_duration_seconds": 4.0,
                "max_segment_seconds": 5.0,
                "title": "Fixture Highlight",
            },
        )
        outputs["storyboard"] = await _run_technical(
            runner,
            instance_id,
            "storyboard",
            {
                "job_dir": str(job_dir),
                "source_manifest_path": outputs["source"]["source_manifest_path"],
                "edl_path": str(film_fixture.edl),
                "profile": {
                    "fps": {"numerator": 24, "denominator": 1},
                    "max_segment_seconds": 5.0,
                },
            },
        )
        outputs["edl_review"] = await _run_gate(
            runner,
            instance_id,
            "edl_review",
            {
                "job_dir": str(job_dir),
                "edl_manifest_path": outputs["storyboard"]["edl_manifest_path"],
                "edl_artifact": outputs["storyboard"]["edl_artifact"],
                "frame_count": outputs["storyboard"]["frame_count"],
                "duration_seconds": outputs["storyboard"]["duration_seconds"],
                "qa": outputs["storyboard"]["qa"],
            },
        )
        outputs["tts"] = await _run_technical(
            runner,
            instance_id,
            "tts",
            {
                "job_dir": str(job_dir),
                "voice_path": str(film_fixture.voice),
                "edl_manifest_path": outputs["edl_review"]["edl_manifest_path"],
                "subtitle_path": str(film_fixture.subtitle),
            },
        )
        outputs["voice_review"] = await _run_gate(
            runner,
            instance_id,
            "voice_review",
            {
                "job_dir": str(job_dir),
                "voice_manifest_path": outputs["tts"]["voice_manifest_path"],
                "voice_artifact": outputs["tts"]["voice_artifact"],
                "subtitle_artifact": outputs["tts"].get("subtitle_artifact"),
            },
        )
        outputs["render"] = await _run_technical(
            runner,
            instance_id,
            "render",
            {
                "job_dir": str(job_dir),
                "source_manifest_path": outputs["source"]["source_manifest_path"],
                "edl_manifest_path": outputs["edl_review"]["edl_manifest_path"],
                "voice_manifest_path": outputs["voice_review"]["voice_manifest_path"],
                "render_profile": {
                    "width": 320,
                    "height": 180,
                    "fps": {"numerator": 24, "denominator": 1},
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                },
            },
        )
        outputs["quality"] = await _run_technical(
            runner,
            instance_id,
            "quality",
            {
                "job_dir": str(job_dir),
                "render_manifest_path": outputs["render"]["render_manifest_path"],
            },
        )
        outputs["download"] = await _run_technical(
            runner,
            instance_id,
            "download",
            {
                "output_path": outputs["render"]["output_path"],
                "qa_report_path": outputs["quality"]["qa_report_path"],
            },
        )
        meta = await runner.finalize_instance(instance_id)
        assert meta.status == "completed"
        return outputs

    outputs = asyncio.run(_drive())

    technical_steps = (
        "input",
        "source",
        "storyboard",
        "tts",
        "render",
        "quality",
        "download",
    )
    for step_id in technical_steps:
        assert store.get_step_state(instance_id, step_id).status == "done"
    for step_id in ("highlight_plan", "edl_review", "voice_review"):
        state = store.get_step_state(instance_id, step_id)
        assert state.status == "done"
        assert state.decision == "approved"

    assert outputs["storyboard"]["frame_count"] == EXPECTED_OUTPUT_FRAMES
    assert outputs["storyboard"]["duration_seconds"] == pytest.approx(4.0)
    assert outputs["storyboard"]["qa"]["status"] == "pass"
    assert outputs["render"]["expected_frames"] == EXPECTED_OUTPUT_FRAMES
    assert outputs["quality"]["status"] == "pass"
    assert isinstance(outputs["quality"]["checks"], list)
    assert isinstance(outputs["quality"]["warnings"], list)

    source = outputs["source"]
    assert all(isinstance(artifact, dict) for artifact in source["artifacts"])
    assert {artifact["artifact_id"] for artifact in source["artifacts"]} == {
        source["reference_asset"]["artifact_id"],
        source["master_asset"]["artifact_id"],
    }
    _assert_artifact_ref(source["reference_asset"], job_dir, producer_step="source")
    _assert_artifact_ref(source["master_asset"], job_dir, producer_step="source")

    source_ids = {
        source["reference_asset"]["artifact_id"],
        source["master_asset"]["artifact_id"],
    }
    edl_artifact = outputs["storyboard"]["edl_artifact"]
    _assert_artifact_ref(
        edl_artifact,
        job_dir,
        producer_step="storyboard",
        input_ids=source_ids,
    )
    voice_artifact = outputs["tts"]["voice_artifact"]
    _assert_artifact_ref(
        voice_artifact,
        job_dir,
        producer_step="tts",
        input_ids={edl_artifact["artifact_id"]},
    )
    subtitle_artifact = outputs["tts"]["subtitle_artifact"]
    _assert_artifact_ref(
        subtitle_artifact,
        job_dir,
        producer_step="tts",
        input_ids={edl_artifact["artifact_id"]},
    )
    render_artifact = outputs["render"]["render_artifact"]
    render_inputs = {
        source["master_asset"]["artifact_id"],
        edl_artifact["artifact_id"],
        voice_artifact["artifact_id"],
    }
    _assert_artifact_ref(
        render_artifact,
        job_dir,
        producer_step="render",
        input_ids=render_inputs,
    )
    qa_artifact = outputs["quality"]["qa_artifact"]
    _assert_artifact_ref(
        qa_artifact,
        job_dir,
        producer_step="quality",
        input_ids={render_artifact["artifact_id"]},
    )

    source_manifest = _read_json(source["source_manifest_path"])
    edl_manifest = _read_json(outputs["storyboard"]["edl_manifest_path"])
    voice_manifest = _read_json(outputs["tts"]["voice_manifest_path"])
    render_manifest = _read_json(outputs["render"]["render_manifest_path"])
    qa_report = _read_json(outputs["quality"]["qa_report_path"])
    assert source_manifest["schema_version"]
    assert edl_manifest["schema_version"]
    assert voice_manifest["schema_version"]
    assert render_inputs <= set(render_manifest["input_artifact_ids"])
    assert qa_report["status"] == "pass"
    assert qa_report["render_artifact_id"] == render_artifact["artifact_id"]

    output = Path(outputs["render"]["output_path"])
    assert output.is_file() and output.stat().st_size > 0
    probe = _probe_output(film_fixture.ffprobe, output)
    video_streams = [stream for stream in probe["streams"] if stream["codec_type"] == "video"]
    audio_streams = [stream for stream in probe["streams"] if stream["codec_type"] == "audio"]
    data_streams = [stream for stream in probe["streams"] if stream["codec_type"] == "data"]
    assert len(video_streams) == 1
    assert audio_streams
    assert data_streams == []
    video = video_streams[0]
    assert int(video["nb_read_frames"]) == EXPECTED_OUTPUT_FRAMES
    assert Fraction(video["r_frame_rate"]) == EXPECTED_FPS
    assert Fraction(video["avg_frame_rate"]) == EXPECTED_FPS


@pytest.mark.parametrize(
    ("case_name", "segments", "error_terms"),
    [
        (
            "zero-length",
            [
                {
                    "segment_id": "zero-segment",
                    "source_start_frame": 48,
                    "source_end_frame": 48,
                }
            ],
            ("edl", "zero-segment", "frame"),
        ),
        (
            "out-of-bounds",
            [
                {
                    "segment_id": "bounds-segment",
                    "source_start_frame": 180,
                    "source_end_frame": MASTER_FRAMES + 24,
                }
            ],
            ("edl", "bounds-segment", "master"),
        ),
    ],
)
def test_film_storyboard_rejects_invalid_edl_through_instance_runner(
    film_fixture: FilmFixture,
    tmp_path: Path,
    case_name: str,
    segments: list[dict[str, Any]],
    error_terms: tuple[str, ...],
) -> None:
    runner_cls, store_cls, registry, recipes = _load_runtime()
    invalid_edl = tmp_path / f"{case_name}.json"
    _write_edl(invalid_edl, segments)
    job_dir = tmp_path / "video-jobs" / case_name
    runner = runner_cls(
        store_cls(tmp_path / "instances"),
        registry=registry,
        recipes=recipes,
    )
    instance_id = runner.create_instance(
        "film_highlight_v1",
        inputs={"domain": "film"},
    ).meta.instance_id

    async def _drive_to_invalid_edl() -> Any:
        await _run_technical(runner, instance_id, "input", {"job_dir": str(job_dir)})
        source = await _run_technical(
            runner,
            instance_id,
            "source",
            {
                "job_dir": str(job_dir),
                "reference_path": str(film_fixture.reference),
                "master_path": str(film_fixture.master),
            },
        )
        await _run_gate(
            runner,
            instance_id,
            "highlight_plan",
            {
                "job_dir": str(job_dir),
                "target_duration_seconds": 4.0,
            },
        )
        assert "storyboard" in runner.get_runnable_steps(instance_id)
        return await runner.run_step(
            instance_id,
            "storyboard",
            step_inputs={
                "job_dir": str(job_dir),
                "source_manifest_path": source["source_manifest_path"],
                "edl_path": str(invalid_edl),
                "profile": {
                    "fps": {"numerator": 24, "denominator": 1},
                    "max_segment_seconds": 5.0,
                },
            },
        )

    state = asyncio.run(_drive_to_invalid_edl())
    assert state.status == "failed"
    error = (state.error or "").lower()
    assert all(term in error for term in error_terms), error
    assert runner.get_runnable_steps(instance_id) == []
