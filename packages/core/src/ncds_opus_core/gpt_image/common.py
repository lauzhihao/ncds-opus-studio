"""Shared helpers for gpt-image gateway scripts."""
from __future__ import annotations

import base64
import datetime as dt
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence
import urllib.parse
import urllib.request

from ncds_opus_core.gpt_image.paths import GPT_IMAGE_OUTPUT_ROOT

DEFAULT_MODEL = "gpt-image-2"
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_OUTPUT_ROOT = GPT_IMAGE_OUTPUT_ROOT
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)

RETRY_MODELS = ["gpt-image-2"]
RETRY_HTTP_STATUS = 502
CLEANUP_MAX_AGE_DAYS = 14


class ApiHttpError(Exception):
    """HTTP API error that lets callers decide retry policy."""

    def __init__(self, code: int, body: str, curl: str = "") -> None:
        msg = f"HTTP {code}: {body}"
        if curl:
            msg = f"{curl}\n{msg}"
        super().__init__(msg)
        self.code = code
        self.body = body
        self.curl = curl


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def redact(text: str) -> str:
    text = re.sub(r"(x-api-key:\s*)[^\s'\"\\]+", r"\1REDACTED", text, flags=re.I)
    text = re.sub(r"(Authorization:\s*Bearer\s+)[^\s'\"\\]+", r"\1REDACTED", text, flags=re.I)
    text = re.sub(r"(-u\s+)[^\s]+", r"\1REDACTED", text)
    text = re.sub(r"(token\s*[:=]\s*)[^\s]+", r"\1REDACTED", text, flags=re.I)
    return text


def run_subprocess(
    args: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=dict(env),
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


def load_manifest(output_dir: Path) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        fail(f"Generation finished but manifest was not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def build_model_attempt_order(requested: str) -> list[str]:
    order = [requested]
    for model in RETRY_MODELS:
        if model not in order:
            order.append(model)
    return order


def ensure_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        fail(f"Environment variable {name} is not set.", code=2)
    return value


def download_url(url: str, timeout: int = 60) -> tuple[bytes, str]:
    parsed = urllib.parse.urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if origin:
        headers["Referer"] = f"{origin}/"
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            content_type = resp.headers.get_content_type() or ""
            ext = mimetypes.guess_extension(content_type) or Path(urllib.parse.urlparse(url).path).suffix or ".png"
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


def get_response_image_items(response: dict[str, Any]) -> list[Any]:
    for key in ("data", "images", "output"):
        value = response.get(key)
        if isinstance(value, list) and value:
            return value
    return [response]


def save_images_from_response(
    response: dict[str, Any],
    output_dir: Path,
    overwrite: bool,
) -> list[dict[str, str]]:
    data = get_response_image_items(response)
    if not data:
        fail(f"No images in API response: {json.dumps(response, ensure_ascii=False)[:800]}")

    saved: list[dict[str, str]] = []
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
        saved.append(
            {
                "path": str(output_path),
                "source": f"data[{index - 1}]",
                "kind": kind,
                "revised_prompt": item.get("revised_prompt", "") if isinstance(item, dict) else "",
            }
        )
    if not saved:
        fail(f"API returned data but no decodable images: {json.dumps(response, ensure_ascii=False)[:800]}")
    return saved
