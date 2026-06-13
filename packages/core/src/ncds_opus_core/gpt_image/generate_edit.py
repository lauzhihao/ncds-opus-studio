#!/usr/bin/env python3
"""Entry point for image editing (图生图) via /images/edits endpoint."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List


DEFAULT_OUTPUT_ROOT = Path("/tmp/gpt-image-edit")
CLEANUP_MAX_AGE_DAYS = 14


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def redact(text: str) -> str:
    text = re.sub(r"(x-api-key:\s*)[^\s'\"\\]+", r"\1REDACTED", text, flags=re.I)
    text = re.sub(r"(-u\s+)[^\s]+", r"\1REDACTED", text)
    text = re.sub(r"(token\s*[:=]\s*)[^\s]+", r"\1REDACTED", text, flags=re.I)
    return text


def run(args: List[str], cwd: Path, env: Dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def cleanup_old_outputs(root: Path, max_age_days: int = CLEANUP_MAX_AGE_DAYS) -> None:
    if not root.is_dir():
        return
    cutoff = dt.datetime.now().timestamp() - max_age_days * 86400
    for child in root.iterdir():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)


def resolve_output_dir(raw_output_dir: str | None) -> Path:
    if raw_output_dir:
        output_dir = Path(raw_output_dir).expanduser().resolve()
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = (DEFAULT_OUTPUT_ROOT / stamp).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def load_manifest(output_dir: Path) -> Dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        fail(f"Generation finished but manifest was not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Edit image(s) via /images/edits and return local file paths."
    )
    parser.add_argument("--prompt", help="Prompt text.")
    parser.add_argument("--prompt-file", help="Read prompt text from a file.")
    parser.add_argument("--image", required=True, help="Base image to edit: local file path or https URL.")
    parser.add_argument("--mask", help="Optional mask image.")
    parser.add_argument("--out-dir", help="Directory for generated files.")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    cleanup_old_outputs(DEFAULT_OUTPUT_ROOT)

    output_dir = resolve_output_dir(args.out_dir)
    editor = Path(__file__).resolve().parent / "gpt_image_edit.py"

    edit_cmd = [
        sys.executable, str(editor),
        "--out-dir", str(output_dir),
        "--overwrite",
        "--timeout", str(args.timeout),
    ]
    if args.prompt:
        edit_cmd.extend(["--prompt", args.prompt])
    if args.prompt_file:
        edit_cmd.extend(["--prompt-file", args.prompt_file])
    edit_cmd.extend(["--image", args.image])
    if args.mask:
        edit_cmd.extend(["--mask", args.mask])

    env = os.environ.copy()
    result = run(edit_cmd, cwd=Path.cwd(), env=env, timeout=args.timeout + 30)
    (output_dir / "generation_stderr.log").write_text(redact(result.stderr), encoding="utf-8")
    if result.returncode != 0:
        fail("Image edit failed:\n" + redact(result.stderr or result.stdout), result.returncode)

    manifest = load_manifest(output_dir)
    images = [Path(item["path"]).resolve() for item in manifest.get("images", []) if item.get("path")]
    if not images:
        fail(f"No generated images found in manifest: {output_dir / 'manifest.json'}")

    print(
        json.dumps(
            {
                "ok": True,
                "mode": manifest.get("mode"),
                "output_dir": str(output_dir),
                "images": [str(path) for path in images],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
