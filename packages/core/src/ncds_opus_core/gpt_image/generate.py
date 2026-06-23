#!/usr/bin/env python3
"""Generate images with gpt-image-2 via /images/generations and return local file paths."""
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
        description="Generate image(s) with gpt-image-2 and return local file paths."
    )
    parser.add_argument("--prompt", help="Prompt text.")
    parser.add_argument("--prompt-file", help="Read prompt text from a file.")
    parser.add_argument("--out-dir", help="Directory for generated files.")
    parser.add_argument("--timeout", type=int, default=180, help="Image generation timeout in seconds.")
    parser.add_argument("--size", help="Aspect ratio (1:1/3:2/2:3/16:9/21:9/9:16/4:3/3:4).")
    parser.add_argument("--n", type=int, default=1, help="Number of images (1-4).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    cleanup_old_outputs(DEFAULT_OUTPUT_ROOT)

    output_dir = resolve_output_dir(args.out_dir)
    generator = Path(__file__).resolve().parent / "gpt_image_gen.py"

    generate_cmd = [sys.executable, str(generator), "--out-dir", str(output_dir), "--overwrite", "--timeout", str(args.timeout)]
    if args.prompt:
        generate_cmd.extend(["--prompt", args.prompt])
    if args.prompt_file:
        generate_cmd.extend(["--prompt-file", args.prompt_file])
    if args.size:
        generate_cmd.extend(["--size", args.size])
    if args.n:
        generate_cmd.extend(["--n", str(args.n)])

    env = os.environ.copy()
    generation = run_subprocess(generate_cmd, cwd=Path.cwd(), env=env, timeout=args.timeout + 30)
    (output_dir / "generation_stderr.log").write_text(redact(generation.stderr), encoding="utf-8")
    if generation.returncode != 0:
        fail("Image generation failed:\n" + redact(generation.stderr or generation.stdout), generation.returncode)

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
