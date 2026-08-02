"""Backend acceptance for film localization's ordered model fallback.

The production command runs in a separate Python process. Its model launchers
are also separate processes: this test installs executable stand-ins on PATH
and lets the real CLI adapters invoke them. No production callable is mocked.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

FAKE_CLI = r'''#!/usr/bin/env python3
import json
import os
import re
import sys


def _model(argv, backend):
    flag = "--model" if backend in {"agy", "opus"} else "-m"
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return ""


def _payload(argv):
    decoder = json.JSONDecoder()
    candidates = []
    for value in argv:
        for match in re.finditer(r"\[", value):
            try:
                parsed, _ = decoder.raw_decode(value[match.start():])
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, list):
                continue
            rows = [row for row in parsed if isinstance(row, dict)]
            if rows and any("cue_id" in row for row in rows):
                candidates.append(rows)
    if not candidates:
        raise RuntimeError("fake CLI could not find cue payload")
    return candidates[-1]


backend = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
model = _model(argv, backend)
rows = _payload(argv)
cue_ids = [str(row.get("cue_id")) for row in rows]
record = {
    "backend": backend,
    "model": model,
    "cue_ids": cue_ids,
    "status": "ok",
    "argv": argv,
}
log_path = os.environ["FAKE_FILM_CLI_LOG"]
if backend == "agy" and any(int(re.search(r"(\d+)$", cue).group(1)) > 40 for cue in cue_ids):
    record["status"] = "failed"
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    print("simulated AGY batch 2 failure", file=sys.stderr)
    raise SystemExit(17)

with open(log_path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\n")

translated = [
    {
        "cue_id": cue_id,
        "translated_text": f"{backend}:{model}:{cue_id}",
    }
    for cue_id in cue_ids
]
raw = json.dumps(translated, ensure_ascii=False)
if backend == "agy":
    print(raw)
elif backend == "scodex":
    print(json.dumps({"type": "item.completed", "item": {"text": raw}}))
elif backend == "opus":
    print(json.dumps({"type": "result", "result": raw}))
else:
    raise SystemExit(f"unexpected fake backend: {backend}")
'''


def _install_fake_clis(directory: Path) -> None:
    directory.mkdir()
    for name in ("agy", "scodex", "opus"):
        path = directory / name
        path.write_text(FAKE_CLI, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_localization_subprocess(
    repo_root: Path,
    job_dir: Path,
    cli_dir: Path,
    log_path: Path,
    result_path: Path,
) -> subprocess.CompletedProcess[str]:
    script = r'''
import json
import sys
from pathlib import Path

from ncds_opus_factory.commands.film_localization import localize_film_script

job_dir = Path(sys.argv[1])
result_path = Path(sys.argv[2])
result = localize_film_script(job_dir, target_language="en")
result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
'''
    env = os.environ.copy()
    env["FAKE_FILM_CLI_LOG"] = str(log_path)
    # Keep host-level feature flags from disabling the fake AGY executable.
    env["NOF_AGY"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root / "src"), str(repo_root / "packages" / "core" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    env["PATH"] = os.pathsep.join([str(cli_dir), env.get("PATH", "")])
    return subprocess.run(  # noqa: S603 - fixed child script and pytest temp paths.
        [sys.executable, "-c", script, str(job_dir), str(result_path)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _make_guiguzi_artifact(job_dir: Path) -> None:
    cues: list[dict[str, Any]] = []
    for number in range(1, 81):
        cues.append(
            {
                "cue_id": f"cue-{number:03d}",
                "source_work_id": "fixture-film",
                "start_ms": (number - 1) * 1000,
                "end_ms": number * 1000,
                "kind": "narration",
                "ocr_text": f"原文 {number}",
                "asr_text": "",
                "text": f"这是第 {number} 条旁白。",
            }
        )
    # A non-narration cue proves localization still consumes only clean narration.
    cues.append(
        {
            "cue_id": "cue-999",
            "start_ms": 80000,
            "end_ms": 81000,
            "kind": "dialogue",
            "text": "不应进入翻译",
        }
    )
    job_dir.mkdir(parents=True)
    (job_dir / "guiguzi.json").write_text(
        json.dumps(
            {
                "version": 1,
                "mode": "film_commentary",
                "status": "done",
                "entity_glossary": [{"canonical": "小明", "aliases": ["明"]}],
                "cues": cues,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_film_localization_falls_back_from_agy_to_codex_from_batch_one(tmp_path: Path) -> None:
    """A failed AGY batch cannot leak into the successful SCodex artifact."""
    repo_root = Path(
        os.environ.get(
            "NOF_FILM_FALLBACK_REPO_ROOT",
            Path(__file__).resolve().parents[2],
        )
    ).resolve()
    job_dir = tmp_path / "video-jobs" / "film-fallback"
    cli_dir = tmp_path / "fake-bin"
    log_path = tmp_path / "cli-calls.jsonl"
    result_path = tmp_path / "subprocess-result.json"
    _make_guiguzi_artifact(job_dir)
    _install_fake_clis(cli_dir)

    completed = _run_localization_subprocess(
        repo_root, job_dir, cli_dir, log_path, result_path
    )
    assert completed.returncode == 0, (
        "production localization subprocess failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )

    calls = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [call["backend"] for call in calls] == [
        "agy",
        "agy",
        "scodex",
        "scodex",
    ]
    assert [call["model"] for call in calls] == [
        "gemini-3.5-flash-high",
        "gemini-3.5-flash-high",
        "gpt-5.6-terra",
        "gpt-5.6-terra",
    ]
    assert calls[0]["status"] == "ok"
    assert calls[1]["status"] == "failed"
    assert calls[0]["cue_ids"] == calls[2]["cue_ids"]
    assert calls[2]["cue_ids"] == [f"cue-{number:03d}" for number in range(1, 41)]
    assert calls[3]["cue_ids"] == [f"cue-{number:03d}" for number in range(41, 81)]
    assert not any(call["backend"] == "opus" for call in calls)

    result = json.loads(result_path.read_text(encoding="utf-8"))
    artifact_path = job_dir / "02_rw" / "film_localization.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert result["translation_backend"] == "codex"
    assert result["translation_model"] == "gpt-5.6-terra"
    assert artifact["translation_backend"] == "codex"
    assert artifact["translation_model"] == "gpt-5.6-terra"

    segments = artifact["segments"]
    assert len(segments) == 80
    cue_ids = [str(segment["cue_id"]) for segment in segments]
    assert cue_ids == [f"cue-{number:03d}" for number in range(1, 81)]
    assert len(set(cue_ids)) == len(cue_ids)
    assert all(
        segment["translated_text"].startswith("scodex:gpt-5.6-terra:")
        for segment in segments
    )
    assert not any(
        segment["translated_text"].startswith("agy:") for segment in segments
    )
