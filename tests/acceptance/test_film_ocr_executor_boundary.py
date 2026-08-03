"""Assembled acceptance for the versioned film OCR executor boundary.

This test deliberately crosses a child-interpreter, FFmpeg/ffprobe, filesystem,
film collection, and executor boundary.  The fake transport is only the remote
upload seam: it receives the local path out of band and returns a versioned
wire result without reading any user state or making a network request.
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
        pytest.skip("ffmpeg and ffprobe are required for film OCR acceptance")
    return ffmpeg


def _make_video(ffmpeg: str, output: Path) -> None:
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
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            "4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
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
            {
                "id": "asr_commentary",
                "start_ms": 900,
                "end_ms": 1800,
                "text": "锁降突击开始",
            },
            {
                "id": "asr_dialogue",
                "start_ms": 2500,
                "end_ms": 3300,
                "text": "电影台词",
            },
        ]
    }
    path = video.with_suffix(".asr.timeline.json")
    path.write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    return path


def _run_remote_executor_subprocess(
    repo_root: Path,
    run_root: Path,
    video: Path,
    timeline: Path,
) -> dict[str, Any]:
    result_path = run_root / "result.json"
    script = r"""
import hashlib
import json
import sys
from pathlib import Path

from ncds_opus_factory.commands import film_script_v3, film_subtitles
from ncds_opus_factory.commands.film_ocr_executor import (
    FILM_OCR_REQUEST_SCHEMA_VERSION,
    FILM_OCR_RESULT_SCHEMA_VERSION,
    FILM_OCR_OPERATION,
    FilmOcrJob,
    FilmOcrMiddlewareTransport,
    FilmOcrResult,
    LocalFilmOcrExecutor,
    RemoteFilmOcrExecutor,
    algorithm_signature,
    canonical_observations_sha256,
)
from ncds_opus_factory.common import works_repo


video = Path(sys.argv[1])
timeline = Path(sys.argv[2])
result_path = Path(sys.argv[3])


def observation(observation_id, time_ms, text, label):
    return {
        "observation_id": observation_id,
        "frame_index": max(1, time_ms // 500 + 1),
        "time_ms": time_ms,
        "line_order": 0,
        "text": text,
        "confidence": 0.98,
        "bbox_norm": {"x": 0.10, "y": 0.76, "width": 0.80, "height": 0.08},
        "polygon_crop_px": [[0, 0], [20, 0], [20, 10], [0, 10]],
        "color_signature": {
            "label": label,
            "yellow_ratio": 0.0 if label == "white" else 0.9,
            "white_ratio": 0.9 if label == "white" else 0.0,
        },
        "has_latin_companion": False,
    }


class FakeTransport:
    requests = []
    uploaded_paths = []

    def submit(self, *, request, source_path, on_progress=None):
        assert isinstance(source_path, Path) and source_path.is_file()
        FakeTransport.uploaded_paths.append(str(source_path))
        serialized = json.dumps(request, ensure_ascii=False, sort_keys=True)
        assert str(source_path) not in serialized
        assert source_path.as_uri() not in serialized
        FakeTransport.requests.append(request)
        rows = [
            observation("remote_001", 1000, "锁降突击开始", "white"),
            observation("remote_002", 2800, "电影台词", "yellow"),
            observation("remote_003", 1600, "OCR独有文字绝不进稿", "white"),
        ]
        roi = {"x": 0.0, "y": 0.70, "width": 1.0, "height": 0.29}
        return {
            "schema_version": FILM_OCR_RESULT_SCHEMA_VERSION,
            "operation": request["operation"],
            "request_id": request["request_id"],
            "idempotency_key": request["idempotency_key"],
            "source": dict(request["source"]),
            "roi": roi,
            "observations": rows,
            "backend": request["algorithm_signature"]["backend"],
            "algorithm_signature": algorithm_signature(
                roi,
                backend=request["algorithm_signature"]["backend"],
                frame_sampling_fps=request["algorithm_signature"]["frame_sampling_fps"],
                layout_discovery_frames=request["algorithm_signature"]["layout_discovery_frames"],
                scale_width=request["algorithm_signature"]["scale_width"],
            ),
            "execution": {"executor": "remote", "transport": "fake", "attempt": 1},
            "observations_sha256": canonical_observations_sha256(rows),
        }


transport = FakeTransport()
remote = RemoteFilmOcrExecutor(transport)
assert isinstance(remote, RemoteFilmOcrExecutor)
assert isinstance(LocalFilmOcrExecutor(), LocalFilmOcrExecutor)
assert FilmOcrMiddlewareTransport.__name__ == "FilmOcrMiddlewareTransport"
assert callable(film_script_v3.OCR_EXECUTOR_FACTORY)
assert isinstance(FILM_OCR_REQUEST_SCHEMA_VERSION, str)
assert isinstance(FILM_OCR_RESULT_SCHEMA_VERSION, str)
film_script_v3.OCR_EXECUTOR_FACTORY = lambda: remote

progress = []
collected = film_subtitles.collect_film_subtitles(
    Path("job"), [str(video)], on_progress=progress.append
)
entry = collected["collected"][0]
source = entry["film_source"]
raw_path = Path(source["raw_observations"]["json"])
if not raw_path.is_absolute():
    raw_path = Path.cwd() / raw_path

# A second work id must form the same source request and idempotency identity.
film_script_v3.extract_video_subtitles_v3(
    video,
    platform="local",
    work_id="same-source-second-work",
    asr_timeline=timeline,
    on_progress=progress.append,
)

request = transport.requests[0]
assert request["schema_version"] == FILM_OCR_REQUEST_SCHEMA_VERSION
assert request["operation"] == FILM_OCR_OPERATION
assert request["request_id"] == request["idempotency_key"]
assert request["request_id"].startswith("film-ocr-")
assert len(request["request_id"].removeprefix("film-ocr-")) == 64
assert request["source"]["sha256"] == hashlib.sha256(
    Path(transport.uploaded_paths[0]).read_bytes()
).hexdigest()
assert request["source"]["byte_size"] == Path(transport.uploaded_paths[0]).stat().st_size
assert request["source"]["media_type"] == "video/mp4"
assert request["algorithm_signature"]["frame_sampling_fps"] > 0
assert request["algorithm_signature"]["backend"]
assert transport.requests[1]["request_id"] == request["request_id"]
assert transport.requests[1]["idempotency_key"] == request["idempotency_key"]

raw = json.loads(raw_path.read_text(encoding="utf-8"))
tracks_path = Path(source["tracks"]["json"])
script_path = Path(source["commentary_script"]["json"])
if not tracks_path.is_absolute():
    tracks_path = Path.cwd() / tracks_path
if not script_path.is_absolute():
    script_path = Path.cwd() / script_path
tracks = json.loads(tracks_path.read_text(encoding="utf-8"))
commentary = json.loads(script_path.read_text(encoding="utf-8"))

# Constructing these public values here ensures the boundary stays importable,
# while the assembled extraction above proves the executor is actually used.
assert FilmOcrJob and FilmOcrResult
result_path.write_text(json.dumps({
    "request": request,
    "request_json": json.dumps(request, ensure_ascii=False, sort_keys=True),
    "uploaded_paths": transport.uploaded_paths,
    "source": source,
    "raw": raw,
    "tracks": tracks,
    "commentary": commentary,
    "progress": progress,
}, ensure_ascii=False, indent=2), encoding="utf-8")
"""
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
            str(timeline),
            str(result_path),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        f"remote film OCR collector subprocess failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return json.loads(result_path.read_text(encoding="utf-8"))


def _run_integrity_rejection_subprocess(
    repo_root: Path,
    run_root: Path,
    video: Path,
    timeline: Path,
) -> dict[str, Any]:
    result_path = run_root / "rejection.json"
    script = r"""
import json
import sys
from pathlib import Path

from ncds_opus_factory.commands import film_script_v3
from ncds_opus_factory.commands.film_ocr_executor import (
    FILM_OCR_RESULT_SCHEMA_VERSION,
    RemoteFilmOcrExecutor,
    algorithm_signature,
    canonical_observations_sha256,
)
from ncds_opus_factory.common import works_repo


video = Path(sys.argv[1])
timeline = Path(sys.argv[2])
result_path = Path(sys.argv[3])


def row():
    return {
        "observation_id": "bad_001",
        "frame_index": 1,
        "time_ms": 1000,
        "line_order": 0,
        "text": "锁降突击开始",
        "confidence": 0.98,
        "bbox_norm": {"x": 0.1, "y": 0.76, "width": 0.8, "height": 0.08},
        "polygon_crop_px": [[0, 0], [20, 0], [20, 10], [0, 10]],
        "color_signature": {"label": "white", "yellow_ratio": 0.0, "white_ratio": 0.9},
        "has_latin_companion": False,
    }


def run_case(case):
    work_id = "integrity-" + case
    output_dir = works_repo.work_dir("local", work_id) / "film_subtitles"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "v3.raw_observations.json"
    raw_path.write_text("sentinel-must-not-be-overwritten", encoding="utf-8")

    class BadTransport:
        def submit(self, *, request, source_path, on_progress=None):
            rows = [row()]
            source = dict(request["source"])
            digest = canonical_observations_sha256(rows)
            if case == "bad-digest":
                digest = "0" * 64
            else:
                source["sha256"] = "f" * 64
            roi = {"x": 0.0, "y": 0.70, "width": 1.0, "height": 0.29}
            return {
                "schema_version": FILM_OCR_RESULT_SCHEMA_VERSION,
                "operation": request["operation"],
                "request_id": request["request_id"],
                "idempotency_key": request["idempotency_key"],
                "source": source,
                "roi": roi,
                "observations": rows,
                "backend": request["algorithm_signature"]["backend"],
                "algorithm_signature": algorithm_signature(
                    roi,
                    backend=request["algorithm_signature"]["backend"],
                    frame_sampling_fps=request["algorithm_signature"]["frame_sampling_fps"],
                    layout_discovery_frames=request["algorithm_signature"]["layout_discovery_frames"],
                    scale_width=request["algorithm_signature"]["scale_width"],
                ),
                "execution": {"executor": "remote", "attempt": 1},
                "observations_sha256": digest,
            }

    film_script_v3.OCR_EXECUTOR_FACTORY = lambda: RemoteFilmOcrExecutor(BadTransport())
    try:
        film_script_v3.extract_video_subtitles_v3(
            video,
            platform="local",
            work_id=work_id,
            asr_timeline=timeline,
        )
    except (RuntimeError, ValueError) as exc:
        message = str(exc)
    else:
        raise AssertionError(case + " remote result was accepted")
    assert raw_path.read_text(encoding="utf-8") == "sentinel-must-not-be-overwritten"
    assert not (output_dir / "v3.tracks.json").exists()
    assert not (output_dir / "v3.commentary.json").exists()
    return message


result_path.write_text(json.dumps({
    "bad_digest": run_case("bad-digest"),
    "source_mismatch": run_case("source-mismatch"),
}, ensure_ascii=False, indent=2), encoding="utf-8")
"""
    env = os.environ.copy()
    env["NOF_STATE_DIR"] = str(run_root / "state" / "tasks")
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
            str(timeline),
            str(result_path),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        f"film OCR integrity rejection subprocess failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return json.loads(result_path.read_text(encoding="utf-8"))


def _run_remote_boundary_hardening_subprocess(
    repo_root: Path,
    run_root: Path,
    video: Path,
    timeline: Path,
) -> dict[str, Any]:
    result_path = run_root / "hardening.json"
    script = r"""
import hashlib
import json
import sys
from pathlib import Path

from ncds_opus_factory.commands import film_script_v3
from ncds_opus_factory.commands.film_ocr_executor import (
    FILM_OCR_RESULT_SCHEMA_VERSION,
    RemoteFilmOcrExecutor,
    algorithm_signature,
    canonical_observations_sha256,
)
from ncds_opus_factory.common import works_repo


video = Path(sys.argv[1])
timeline = Path(sys.argv[2])
result_path = Path(sys.argv[3])


def row(observation_id="remote_001", text="锁降突击开始"):
    return {
        "observation_id": observation_id,
        "frame_index": 3,
        "time_ms": 1000,
        "line_order": 0,
        "text": text,
        "confidence": 0.98,
        "bbox_norm": {"x": 0.1, "y": 0.76, "width": 0.8, "height": 0.08},
        "polygon_crop_px": [[0, 0], [20, 0], [20, 10], [0, 10]],
        "color_signature": {"label": "white", "yellow_ratio": 0.0, "white_ratio": 0.9},
        "has_latin_companion": False,
    }


def response(request, rows=None, *, execution=None):
    observations = [row()] if rows is None else rows
    roi = {"x": 0.0, "y": 0.70, "width": 1.0, "height": 0.29}
    return {
        "schema_version": FILM_OCR_RESULT_SCHEMA_VERSION,
        "operation": request["operation"],
        "request_id": request["request_id"],
        "idempotency_key": request["idempotency_key"],
        "source": dict(request["source"]),
        "roi": roi,
        "observations": observations,
        "backend": request["algorithm_signature"]["backend"],
        "algorithm_signature": algorithm_signature(
            roi,
            backend=request["algorithm_signature"]["backend"],
            frame_sampling_fps=request["algorithm_signature"]["frame_sampling_fps"],
            layout_discovery_frames=request["algorithm_signature"]["layout_discovery_frames"],
            scale_width=request["algorithm_signature"]["scale_width"],
        ),
        "execution": {} if execution is None else execution,
        "observations_sha256": canonical_observations_sha256(observations),
    }


def raw_path_for(work_id):
    return works_repo.work_dir("local", work_id) / "film_subtitles" / "v3.raw_observations.json"


def must_reject(case):
    work_id = "hardening-" + case
    raw_path = raw_path_for(work_id)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text("sentinel-must-not-be-overwritten", encoding="utf-8")

    class BadTransport:
        def submit(self, *, request, source_path, on_progress=None):
            if case == "idempotency-mismatch":
                value = response(request)
                value["idempotency_key"] = "evil-idempotency-key"
                return value
            malformed = [{"text": "missing mandatory observation fields"}]
            return response(request, malformed)

    film_script_v3.OCR_EXECUTOR_FACTORY = lambda: RemoteFilmOcrExecutor(BadTransport())
    try:
        film_script_v3.extract_video_subtitles_v3(
            video,
            platform="local",
            work_id=work_id,
            asr_timeline=timeline,
        )
    except (RuntimeError, ValueError) as exc:
        message = str(exc)
    else:
        raise AssertionError(case + " remote result was accepted")
    assert raw_path.read_text(encoding="utf-8") == "sentinel-must-not-be-overwritten"
    assert not raw_path.with_name("v3.tracks.json").exists()
    assert not raw_path.with_name("v3.commentary.json").exists()
    return message


def authoritative_execution_case():
    work_id = "hardening-execution-authority"

    class HostileTransport:
        def submit(self, *, request, source_path, on_progress=None):
            return response(
                request,
                execution={"executor": "local", "request_id": "evil-request-id", "transport": "fake"},
            )

    film_script_v3.OCR_EXECUTOR_FACTORY = lambda: RemoteFilmOcrExecutor(HostileTransport())
    source = film_script_v3.extract_video_subtitles_v3(
        video,
        platform="local",
        work_id=work_id,
        asr_timeline=timeline,
    )
    raw = json.loads(raw_path_for(work_id).read_text(encoding="utf-8"))
    assert raw["executor"] == "remote"
    assert raw["execution"]["executor"] == "remote"
    assert raw["execution"]["request_id"] == raw["request"]["request_id"]
    assert raw["execution"]["request_id"] != "evil-request-id"
    assert source["raw_observations"]["executor"] == "remote"
    return raw


def stale_cache_case():
    work_id = "hardening-stale-observation-digest"
    raw_path = raw_path_for(work_id)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    roi = {"x": 0.0, "y": 0.70, "width": 1.0, "height": 0.29}
    stale_rows = [row("stale_001", "过期缓存绝不能复用")]
    raw_path.write_text(json.dumps({
        "version": film_script_v3.VERSION,
        "profile": film_script_v3.PROFILE,
        "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "backend": film_script_v3.OCR_BACKEND,
        "executor": "remote",
        "algorithm_signature": film_script_v3._algorithm_signature(roi),
        "execution": {"executor": "remote"},
        "observations": stale_rows,
        "observations_sha256": "0" * 64,
        "integrity": {"observations_sha256": "0" * 64},
    }, ensure_ascii=False), encoding="utf-8")

    class FreshTransport:
        calls = 0

        def submit(self, *, request, source_path, on_progress=None):
            FreshTransport.calls += 1
            return response(request, [row("fresh_001", "锁降突击开始")])

    film_script_v3.OCR_EXECUTOR_FACTORY = lambda: RemoteFilmOcrExecutor(FreshTransport())
    film_script_v3.extract_video_subtitles_v3(
        video,
        platform="local",
        work_id=work_id,
        asr_timeline=timeline,
    )
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert FreshTransport.calls == 1
    assert raw["observations"][0]["observation_id"] == "fresh_001"
    assert raw["observations_sha256"] == canonical_observations_sha256(raw["observations"])
    return raw


result_path.write_text(json.dumps({
    "idempotency_mismatch": must_reject("idempotency-mismatch"),
    "malformed_observation": must_reject("malformed-observation"),
    "authoritative_execution": authoritative_execution_case(),
    "stale_cache": stale_cache_case(),
}, ensure_ascii=False, indent=2), encoding="utf-8")
"""
    env = os.environ.copy()
    env["NOF_STATE_DIR"] = str(run_root / "state" / "tasks")
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
            str(timeline),
            str(result_path),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        f"film OCR hardening subprocess failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return json.loads(result_path.read_text(encoding="utf-8"))


def test_remote_film_ocr_executor_preserves_asr_first_artifacts(tmp_path: Path) -> None:
    """Remote results are auditable evidence, never commentary-script authority."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "film.mp4"
    _make_video(ffmpeg, video)
    timeline = _write_asr_timeline(video)

    result = _run_remote_executor_subprocess(repo_root, tmp_path / "remote", video, timeline)
    request = result["request"]
    raw = result["raw"]
    source = result["source"]
    serialized = result["request_json"]

    assert result["uploaded_paths"]
    assert all(path not in serialized for path in result["uploaded_paths"])
    assert all(Path(path).as_uri() not in serialized for path in result["uploaded_paths"])
    assert request["source"]["sha256"] in serialized
    assert str(request["source"]["byte_size"]) in serialized
    assert request["source"]["media_type"] in serialized
    assert request["operation"] in serialized
    assert "algorithm_signature" in serialized

    assert raw["executor"] == "remote"
    assert raw["backend"] == request["algorithm_signature"]["backend"]
    assert raw["execution"]["transport"] == "fake"
    assert raw["observations_sha256"]
    assert raw["request"]["request_id"] == request["request_id"]
    assert raw["request"]["source"] == request["source"]
    assert raw["request"]["algorithm_signature"] == request["algorithm_signature"]
    assert raw["algorithm_signature"]["roi"] == source["raw_observations"]["roi"]
    assert raw["algorithm_signature"]["backend"] == request["algorithm_signature"]["backend"]
    assert source["raw_observations"]["executor"] == "remote"

    assignments = {item["observation_id"]: item["source_class"] for item in result["tracks"]["assignments"]}
    assert assignments["remote_002"] == "film_dialogue"
    cue_text = "\n".join(cue["text"] for cue in result["commentary"]["cues"])
    assert "锁降突击开始" in cue_text
    assert "电影台词" not in cue_text
    assert "OCR独有文字绝不进稿" not in cue_text


def test_remote_film_ocr_rejects_bad_integrity_before_artifacts(tmp_path: Path) -> None:
    """A corrupt or source-mismatched remote result cannot create/overwrite outputs."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "film.mp4"
    _make_video(ffmpeg, video)
    timeline = _write_asr_timeline(video)

    rejected = _run_integrity_rejection_subprocess(
        repo_root,
        tmp_path / "integrity",
        video,
        timeline,
    )
    assert rejected["bad_digest"]
    assert rejected["source_mismatch"]


def test_remote_film_ocr_hardens_identity_execution_and_cached_evidence(
    tmp_path: Path,
) -> None:
    """Remote metadata cannot override authority and corrupt raw cache is never reused."""
    ffmpeg = _require_ffmpeg()
    repo_root = _repo_root()
    video = tmp_path / "film.mp4"
    _make_video(ffmpeg, video)
    timeline = _write_asr_timeline(video)

    result = _run_remote_boundary_hardening_subprocess(
        repo_root,
        tmp_path / "hardening",
        video,
        timeline,
    )
    assert result["idempotency_mismatch"]
    assert result["malformed_observation"]
    assert result["authoritative_execution"]["executor"] == "remote"
    assert result["stale_cache"]["observations"][0]["observation_id"] == "fresh_001"
