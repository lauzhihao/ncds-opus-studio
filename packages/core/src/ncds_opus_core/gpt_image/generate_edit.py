#!/usr/bin/env python3
"""Entry point for image editing (图生图) via /images/edits endpoint."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from ncds_opus_core.gpt_image.common import (
    DEFAULT_OUTPUT_ROOT,
    cleanup_old_outputs,
    fail,
    load_manifest,
    redact,
    resolve_output_dir,
    run_subprocess,
)


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
    parser.add_argument("--size", help="Aspect ratio (1:1/3:2/2:3/16:9/21:9/9:16/4:3/3:4).")
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
    if args.size:
        edit_cmd.extend(["--size", args.size])

    env = os.environ.copy()
    result = run_subprocess(edit_cmd, cwd=Path.cwd(), env=env, timeout=args.timeout + 30)
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
