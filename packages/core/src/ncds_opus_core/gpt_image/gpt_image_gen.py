#!/usr/bin/env python3
"""Image generation via /images/generations endpoint (JSON body)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from ncds_opus_core.gpt_image.common import (
    ApiHttpError,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    RETRY_HTTP_STATUS,
    build_model_attempt_order,
    ensure_env,
    fail,
    resolve_output_dir,
    save_images_from_response,
)

# model 与分辨率档位强绑定（文档要求随 model 一起发 image_resolutic）。
MODEL_RESOLUTION = {
    "gpt-image-2": "1k",
    "gpt-image-2k": "2k",
    "gpt-image-2-4k": "4k",
}
DEFAULT_RESOLUTION = "1k"
DEFAULT_SIZE = "1:1"


def resolution_for_model(model: str) -> str:
    # 未知 model 回退到 1k，避免漏发字段导致请求被拒。
    return MODEL_RESOLUTION.get(model, DEFAULT_RESOLUTION)


def request_image_generation(
    base_url: str,
    api_key: str,
    prompt: str,
    model: str,
    timeout_seconds: int,
    size: str = DEFAULT_SIZE,
    n: int = 1,
) -> Dict[str, Any]:
    request_url = f"{base_url}/images/generations"
    payload = {
        "model": model,
        "prompt": prompt,
        "n": n,
        "size": size,
        # 分辨率档位随 model 绑定下发，换模型重试时一起切换。
        "image_resolutic": resolution_for_model(model),
    }
    request_body = json.dumps(payload).encode("utf-8")

    parsed_url = urllib.parse.urlparse(request_url)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    curl_text = build_curl(request_url, payload, origin)
    print(curl_text, file=sys.stderr)

    req = urllib.request.Request(
        request_url,
        data=request_body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
            "Origin": origin,
            "Referer": f"{origin}/",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        # 抛出带状态码的异常，由 main 决定是否换模型重试。
        raise ApiHttpError(exc.code, err_body or exc.reason, curl=curl_text)
    except urllib.error.URLError as exc:
        fail(f"Image generation API request failed: {exc.reason}\n{curl_text}")
    return {}


def build_curl(
    request_url: str,
    payload: Dict[str, Any],
    origin: str,
) -> str:
    lines = [
        "curl -i \\",
        f"  {shlex.quote(request_url)} \\",
        "  -X POST \\",
        "  -H 'Content-Type: application/json; charset=utf-8' \\",
        "  -H 'Authorization: Bearer REDACTED' \\",
        f"  -H {shlex.quote(f'User-Agent: {DEFAULT_USER_AGENT}')} \\",
        f"  --data-raw {shlex.quote(json.dumps(payload, ensure_ascii=False))}",
    ]
    return "Equivalent curl request:\n" + "\n".join(lines)

def print_debug_curl(
    request_url: str,
    payload: Dict[str, Any],
    origin: str,
) -> None:
    print(build_curl(request_url, payload, origin), file=sys.stderr)


def build_manifest(
    prompt: str,
    output_dir: Path,
    response: Dict[str, Any],
    saved_images: List[Dict[str, str]],
) -> Dict[str, Any]:
    return {
        "prompt": prompt,
        "mode": "文生图",
        "input_images": [],
        "output_dir": str(output_dir),
        "images": saved_images,
        "usage": response.get("usage"),
        "response_preview": json.dumps(response, ensure_ascii=False)[:1200],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate images via /images/generations (JSON body)."
    )
    parser.add_argument("--prompt", help="Prompt text.")
    parser.add_argument("--prompt-file", help="Read prompt text from a file.")
    parser.add_argument("--out-dir", help="Output directory.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model name. Default: {DEFAULT_MODEL}")
    parser.add_argument(
        "--size",
        default=DEFAULT_SIZE,
        help=f"Aspect ratio (1:1/3:2/2:3/16:9/21:9/9:16/4:3/3:4). Default: {DEFAULT_SIZE}",
    )
    parser.add_argument("--n", type=int, default=1, help="Number of images (1-4). Default: 1")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.prompt and args.prompt_file:
        fail("Use --prompt or --prompt-file, not both.")
    if args.prompt_file:
        prompt = Path(args.prompt_file).expanduser().resolve().read_text("utf-8").strip()
    else:
        prompt = (args.prompt or "").strip()
    if not prompt:
        fail("Missing prompt. Use --prompt or --prompt-file.")

    base_url = ensure_env("GPT_IMAGE2_BASE_URL").rstrip("/")
    api_key = ensure_env("GPT_IMAGE2_API_KEY")
    # 文档限制 n ∈ [1, 4]，越界直接钳制避免被服务端拒绝。
    n = max(1, min(4, args.n))
    output_dir = resolve_output_dir(args.out_dir)

    response: Dict[str, Any] = {}
    attempts = build_model_attempt_order(args.model)
    for index, model in enumerate(attempts):
        is_last = index == len(attempts) - 1
        try:
            response = request_image_generation(
                base_url=base_url,
                api_key=api_key,
                prompt=prompt,
                model=model,
                timeout_seconds=args.timeout,
                size=args.size,
                n=n,
            )
            break
        except ApiHttpError as exc:
            # 仅 502 触发换模型；其它状态码或已是最后候选则直接失败。
            if exc.code == RETRY_HTTP_STATUS and not is_last:
                print(
                    f"Model {model} returned HTTP {exc.code}; retrying with {attempts[index + 1]}.",
                    file=sys.stderr,
                )
                continue
            fail(f"Image generation API failed with HTTP {exc.code}: {exc.body}")

    saved_images = save_images_from_response(response, output_dir, args.overwrite)

    manifest = build_manifest(
        prompt=prompt,
        output_dir=output_dir,
        response=response,
        saved_images=saved_images,
    )

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (output_dir / "response.json").write_text(
        json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (output_dir / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
