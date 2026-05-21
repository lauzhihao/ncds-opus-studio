"""/wst —— 文生图（gpt-image-2）。

调用 gpt_image/generate.py 网关，返回生成图片的本地路径列表。
飞书发送由调用方走 lark-cli，本模块不接入飞书。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
IMAGE_GATEWAY = ROOT / "gpt_image" / "generate.py"

DEFAULT_TIMEOUT = int(os.getenv("NOF_WST_TIMEOUT", "600"))

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


def run(
    prompt: str,
    timeout_seconds: int = DEFAULT_TIMEOUT,
    on_progress: ProgressFn = _noop,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """生成图片，返回 {images: [...path], output_dir, raw}。"""
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt 不能为空")
    if not IMAGE_GATEWAY.exists():
        raise RuntimeError(f"gpt-image 网关脚本未就绪: {IMAGE_GATEWAY}")

    on_progress("正在执行文生图")
    command = [sys.executable, str(IMAGE_GATEWAY), "--prompt", prompt, *(extra_args or [])]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "文生图失败").strip())

    payload = json.loads(result.stdout or "{}")
    images = payload.get("images") if isinstance(payload.get("images"), list) else []
    return {
        "images": [str(p) for p in images],
        "output_dir": payload.get("output_dir"),
        "raw": payload,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof wst", description="文生图（gpt-image-2）")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args, unknown = parser.parse_known_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(prompt=args.prompt, timeout_seconds=args.timeout, on_progress=on_progress, extra_args=unknown)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
