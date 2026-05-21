#!/usr/bin/env python3
"""Image editing via /images/edits endpoint (multipart/form-data)."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union


DEFAULT_MODEL = "gpt-image-2"
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_OUTPUT_ROOT = Path("/tmp/gpt-image-edit")
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


def is_image_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def download_url(url: str, timeout: int = 60) -> Tuple[bytes, str]:
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


def decode_source_value(value: Any) -> Tuple[str, str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if is_image_url(text):
        return "remote_url", text
    if text.startswith("data:image/"):
        _header, _, encoded = text.partition(",")
        if encoded:
            return "b64_json", encoded
    return None


def extract_image_source(item: Any) -> Tuple[str, str] | None:
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


def get_response_image_items(response: Dict[str, Any]) -> List[Any]:
    for key in ("data", "images", "output"):
        value = response.get(key)
        if isinstance(value, list) and value:
            return value
    return [response]


def load_image(raw: str) -> Tuple[bytes, str, str]:
    if is_image_url(raw):
        data, ext = download_url(raw)
        filename = hashlib.sha256(raw.encode()).hexdigest()[:12] + ext
        mime = mimetypes.guess_type(filename)[0] or "image/png"
        return data, filename, mime
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        fail(f"Input image not found: {path}")
    data = path.read_bytes()
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return data, path.name, mime


def build_multipart(
    fields: List[Tuple[str, str]],
    files: List[Tuple[str, str, bytes, str]],
) -> Tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n"
            f"{value}\r\n".encode("utf-8")
        )
    for field_name, filename, content, content_type in files:
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n"
            f"\r\n"
        )
        parts.append(header.encode("utf-8") + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def request_image_edit(
    base_url: str,
    api_key: str,
    prompt: str,
    image_file: Tuple[bytes, str, str],
    model: str,
    timeout_seconds: int,
    mask_file: Optional[Tuple[bytes, str, str]] = None,
    size: str = "auto",
    quality: str = "auto",
    n: int = 1,
) -> Dict[str, Any]:
    request_url = f"{base_url}/images/edits"

    fields: List[Tuple[str, str]] = [
        ("model", model),
        ("prompt", prompt),
        ("n", str(n)),
        ("size", size),
        ("quality", quality),
    ]
    img_data, img_name, img_mime = image_file
    files: List[Tuple[str, str, bytes, str]] = [
        ("image", img_name, img_data, img_mime),
    ]
    if mask_file:
        mask_data, mask_name, mask_mime = mask_file
        files.append(("mask", mask_name, mask_data, mask_mime))

    body, content_type = build_multipart(fields, files)

    parsed_url = urllib.parse.urlparse(request_url)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    print_debug_curl(request_url, fields, files, origin)

    req = urllib.request.Request(
        request_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": content_type,
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
        fail(f"Image edit API failed with HTTP {exc.code}: {err_body or exc.reason}")
    except urllib.error.URLError as exc:
        fail(f"Image edit API request failed: {exc.reason}")
    return {}


def print_debug_curl(
    request_url: str,
    fields: List[Tuple[str, str]],
    files: List[Tuple[str, str, bytes, str]],
    origin: str,
) -> None:
    lines = [
        "curl -i \\",
        f"  {shlex.quote(request_url)} \\",
        "  -X POST \\",
        "  -H 'x-api-key: REDACTED' \\",
        f"  -H {shlex.quote(f'User-Agent: {DEFAULT_USER_AGENT}')} \\",
    ]
    for name, value in fields:
        lines.append(f"  -F {shlex.quote(f'{name}={value}')} \\")
    for field_name, filename, _data, _mime in files:
        lines.append(f"  -F {shlex.quote(f'{field_name}=@{filename}')} \\")
    if lines[-1].endswith(" \\"):
        lines[-1] = lines[-1][:-2]
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
    images: List[Union[Path, str]],
    output_dir: Path,
    response: Dict[str, Any],
    saved_images: List[Dict[str, str]],
) -> Dict[str, Any]:
    return {
        "prompt": prompt,
        "mode": "图生图",
        "input_images": [str(img) for img in images],
        "output_dir": str(output_dir),
        "images": saved_images,
        "usage": response.get("usage"),
        "response_preview": json.dumps(response, ensure_ascii=False)[:1200],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Edit images via /images/edits (multipart upload)."
    )
    parser.add_argument("--prompt", help="Prompt text.")
    parser.add_argument("--prompt-file", help="Read prompt text from a file.")
    parser.add_argument(
        "--image", required=True,
        help="Base image to edit: local file path or https URL.",
    )
    parser.add_argument(
        "--mask",
        help="Optional mask image (PNG with transparent areas marking edit regions).",
    )
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

    raw_image: Union[Path, str]
    if is_image_url(args.image):
        raw_image = args.image
    else:
        raw_image = Path(args.image).expanduser().resolve()
    image_file = load_image(args.image)

    raw_images: List[Union[Path, str]] = [raw_image]
    mask_file: Optional[Tuple[str, bytes, str]] = None
    if args.mask:
        mask_file = load_image(args.mask)
        if is_image_url(args.mask):
            raw_images.append(args.mask)
        else:
            raw_images.append(Path(args.mask).expanduser().resolve())

    response = request_image_edit(
        base_url=base_url,
        api_key=api_key,
        prompt=prompt,
        image_file=image_file,
        model=args.model,
        timeout_seconds=args.timeout,
        mask_file=mask_file,
        size=args.size,
        quality=args.quality,
        n=args.n,
    )

    saved_images = save_images_from_response(response, output_dir, args.overwrite)

    manifest = build_manifest(
        prompt=prompt,
        images=raw_images,
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
