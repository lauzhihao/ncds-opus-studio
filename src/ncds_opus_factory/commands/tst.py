"""/tst —— 图生图（gpt-image-2 edit）。

调用 gpt_image/generate_edit.py 网关。参考图可以是本地路径或公网 URL。
飞书发送由调用方走 lark-cli；如果参考图来自飞书消息，调用方需要先用 common.lark_cli.download_message_image 下载好再传进来。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
IMAGE_EDIT_GATEWAY = ROOT / "gpt_image" / "generate_edit.py"

DEFAULT_TIMEOUT = int(os.getenv("NOF_TST_TIMEOUT", "600"))
DEFAULT_PROMPT = "基于参考图生成一版高质量变体，保留主体和核心构图，优化细节、光影和整体质感。"
URL_PATTERN = re.compile(r"https?://[^\s\]）)>]+", re.IGNORECASE)

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for item in URL_PATTERN.findall(text or ""):
        cleaned = item.rstrip(".,;!?)]}")
        if cleaned not in urls:
            urls.append(cleaned)
    return urls


def parse_command_body(text: str) -> tuple[list[str], str]:
    """从命令体里抽出 URL 列表和提示词。"""
    urls = extract_urls(text)
    prompt = text
    for url in urls:
        prompt = prompt.replace(url, " ", 1)
    prompt = re.sub(r"^[|｜:,：\s]+", "", prompt)
    prompt = re.sub(r"\s*[|｜]\s*", " ", prompt)
    prompt = re.sub(r"\s+", " ", prompt).strip()
    return urls, prompt


def run(
    prompt: str,
    reference_images: list[str | Path],
    timeout_seconds: int = DEFAULT_TIMEOUT,
    on_progress: ProgressFn = _noop,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """图生图。reference_images 可以是本地文件路径或公网 URL（gateway 自行处理）。"""
    prompt = (prompt or "").strip() or DEFAULT_PROMPT
    if not reference_images:
        raise ValueError("至少需要 1 张参考图")
    if not IMAGE_EDIT_GATEWAY.exists():
        raise RuntimeError(f"gpt-image edit 网关脚本未就绪: {IMAGE_EDIT_GATEWAY}")

    on_progress("正在执行图生图")
    image_args: list[str] = ["--image", str(reference_images[0])]
    if len(reference_images) > 1:
        image_args.extend(["--mask", str(reference_images[1])])

    command = [sys.executable, str(IMAGE_EDIT_GATEWAY), "--prompt", prompt, *image_args, *(extra_args or [])]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "图生图失败").strip())

    payload = json.loads(result.stdout or "{}")
    images = payload.get("images") if isinstance(payload.get("images"), list) else []
    return {
        "images": [str(p) for p in images],
        "output_dir": payload.get("output_dir"),
        "raw": payload,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof tst", description="图生图（gpt-image-2 edit）")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--image", action="append", default=[], help="参考图（本地路径或公网 URL），可多次传")
    parser.add_argument("--body", default=None, help="一段包含 URL 和提示词的自由文本，自动解析（兼容 /tst <URL> | <提示词>）")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args, unknown = parser.parse_known_args(argv)

    images: list[str | Path] = list(args.image)
    prompt = args.prompt
    if args.body:
        body_urls, body_prompt = parse_command_body(args.body)
        images = body_urls + images
        if not prompt:
            prompt = body_prompt

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        prompt=prompt,
        reference_images=images,
        timeout_seconds=args.timeout,
        on_progress=on_progress,
        extra_args=unknown,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
