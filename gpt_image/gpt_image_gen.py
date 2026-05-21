#!/usr/bin/env python3
"""Image generation via /images/generations endpoint (JSON body)."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import mimetypes
import os
from pathlib import Path
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


DEFAULT_MODEL = "gpt-image-2"
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_OUTPUT_ROOT = Path("/tmp/gpt-image")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def ensure_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        fail(f"Environment variable {name} is not set.", code=2)
    return value


def resolve_output_dir(raw_output_dir: Optional[str]) -> Path:
    if raw_output_dir:
        output_dir = Path(raw_output_dir).expanduser().resolve()
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = (DEFAULT_OUTPUT_ROOT / stamp).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def download_url(url: str, timeout: int = 60) -> tuple[bytes, str]:
    parsed = urllib.parse.urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if origin:
        # 有些提供方返回的临时图片地址会拒绝 Python 默认 User-Agent/空 Referer。
        headers["Referer"] = f"{origin}/"
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            ct = resp.headers.get_content_type() or ""
            ext = mimetypes.guess_extension(ct) or Path(urllib.parse.urlparse(url).path).suffix or ".png"
            if ext == ".jpe":
                ext = ".jpg"
            return data, ext
    except Exception as exc:
        fail(f"Failed to download image from {url}: {exc}")
    return b"", ".png"


def is_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def decode_source_value(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if is_http_url(text):
        return "remote_url", text
    if text.startswith("data:image/"):
        _header, _, encoded = text.partition(",")
        if encoded:
            return "b64_json", encoded
    return None


def extract_image_source(item: Any) -> tuple[str, str] | None:
    if isinstance(item, str):
        return decode_source_value(item)
    if not isinstance(item, dict):
        return None

    for key in ("b64_json", "b64", "base64", "result"):
        source = decode_source_value(item.get(key))
        if source:
            return source
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return "b64_json", value.strip()

    for key in ("url", "image_url", "output_url"):
        value = item.get(key)
        source = decode_source_value(value)
        if source:
            return source
        if isinstance(value, dict):
            nested = extract_image_source(value)
            if nested:
                return nested

    for key in ("image", "content"):
        value = item.get(key)
        if isinstance(value, dict):
            nested = extract_image_source(value)
            if nested:
                return nested
        if isinstance(value, list):
            for child in value:
                nested = extract_image_source(child)
                if nested:
                    return nested
    return None


def get_response_image_items(response: Dict[str, Any]) -> list[Any]:
    for key in ("data", "images", "output"):
        value = response.get(key)
        if isinstance(value, list) and value:
            return value
    return [response]


def request_image_generation(
    base_url: str,
    api_key: str,
    prompt: str,
    model: str,
    timeout_seconds: int,
    size: str = "auto",
    quality: str = "auto",
    n: int = 1,
) -> Dict[str, Any]:
    request_url = f"{base_url}/images/generations"
    payload = {
        "model": model,
        "prompt": prompt,
        "n": n,
        "size": size,
        "quality": quality,
    }
    request_body = json.dumps(payload).encode("utf-8")

    parsed_url = urllib.parse.urlparse(request_url)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    print_debug_curl(request_url, payload, origin)

    req = urllib.request.Request(
        request_url,
        data=request_body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "x-api-key": api_key,
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
        fail(f"Image generation API failed with HTTP {exc.code}: {err_body or exc.reason}")
    except urllib.error.URLError as exc:
        fail(f"Image generation API request failed: {exc.reason}")
    return {}


def print_debug_curl(
    request_url: str,
    payload: Dict[str, Any],
    origin: str,
) -> None:
    lines = [
        "curl -i \\",
        f"  {shlex.quote(request_url)} \\",
        "  -X POST \\",
        "  -H 'Content-Type: application/json; charset=utf-8' \\",
        "  -H 'x-api-key: REDACTED' \\",
        f"  -H {shlex.quote(f'User-Agent: {DEFAULT_USER_AGENT}')} \\",
        f"  --data-raw {shlex.quote(json.dumps(payload, ensure_ascii=False))}",
    ]
    print("Equivalent curl request:", file=sys.stderr)
    print("\n".join(lines), file=sys.stderr)


def save_images_from_response(
    response: Dict[str, Any],
    output_dir: Path,
    overwrite: bool,
) -> List[Dict[str, str]]:
    data = get_response_image_items(response)
    if not data:
        fail(f"No images in API response: {json.dumps(response, ensure_ascii=False)[:800]}")

    saved: List[Dict[str, str]] = []
    for index, item in enumerate(data, start=1):
        source = extract_image_source(item)
        if not source:
            continue
        kind, value = source
        if kind == "b64_json":
            image_bytes = base64.b64decode(value, validate=True)
            ext = ".png"
        elif kind == "remote_url":
            image_bytes, ext = download_url(value)
        else:
            continue

        output_path = output_dir / f"image_{index:02d}{ext}"
        if output_path.exists() and not overwrite:
            fail(f"Output already exists: {output_path}. Use --overwrite.")
        output_path.write_bytes(image_bytes)
        saved.append({
            "path": str(output_path),
            "source": f"data[{index - 1}]",
            "kind": kind,
            "revised_prompt": item.get("revised_prompt", "") if isinstance(item, dict) else "",
        })
    if not saved:
        fail(f"API returned data but no decodable images: {json.dumps(response, ensure_ascii=False)[:800]}")
    return saved


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
    parser.add_argument("--size", default="auto", help="Output size. Default: auto")
    parser.add_argument("--quality", default="auto", help="Output quality. Default: auto")
    parser.add_argument("--n", type=int, default=1, help="Number of images. Default: 1")
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
    output_dir = resolve_output_dir(args.out_dir)

    response = request_image_generation(
        base_url=base_url,
        api_key=api_key,
        prompt=prompt,
        model=args.model,
        timeout_seconds=args.timeout,
        size=args.size,
        quality=args.quality,
        n=args.n,
    )

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
