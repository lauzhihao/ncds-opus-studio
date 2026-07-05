"""必剪/哔哩哔哩 Bcut ASR adapter。

该接口接收本地媒体文件，先确保上传内容为 mp3，再走 Bcut 的资源上传 + 异步识别任务。
它是沈括采集的首选云端 ASR；调用方仍应保留听悟等 fallback，因为该接口并非公开稳定契约。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from ._base import ProgressFn, noop, read_repo_env_value

API_BASE_URL = "https://member.bilibili.com/x/bcut/rubick-interface"
API_REQ_UPLOAD = API_BASE_URL + "/resource/create"
API_COMMIT_UPLOAD = API_BASE_URL + "/resource/create/complete"
API_CREATE_TASK = API_BASE_URL + "/task"
API_QUERY_RESULT = API_BASE_URL + "/task/result"

DEFAULT_MODEL_ID = "8"
QUERY_MODEL_ID = "7"
DEFAULT_MAX_POLLS = 500
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
REQUEST_TIMEOUT = (15, 120)

HEADERS = {
    "User-Agent": "Bilibili/1.0.0 (https://www.bilibili.com)",
    "Content-Type": "application/json",
}


class BcutUnavailableError(RuntimeError):
    """Raised when the Bcut adapter cannot produce a transcript."""


@dataclass
class BcutTranscript:
    text: str
    raw_response: dict[str, Any]
    task_id: str
    backend: str = "bcut"
    model: str = DEFAULT_MODEL_ID


@dataclass
class UploadTicket:
    in_boss_key: str
    resource_id: str
    upload_id: str
    upload_urls: list[str]
    per_size: int


def resolve_ffmpeg() -> str:
    configured = (
        os.getenv("OPENCLAW_FFMPEG")
        or os.getenv("NOF_FFMPEG")
        or read_repo_env_value("OPENCLAW_FFMPEG")
        or read_repo_env_value("NOF_FFMPEG")
    )
    candidates = [
        configured,
        shutil.which("ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise BcutUnavailableError("ffmpeg unavailable; required to prepare mp3 for Bcut ASR")


def _ensure_mp3(file_path: Path, tmp_dir: Path) -> Path:
    if file_path.suffix.lower() == ".mp3":
        return file_path

    out_path = tmp_dir / "audio.mp3"
    cmd = [
        resolve_ffmpeg(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(file_path),
        "-vn",
        "-ac",
        "1",
        "-f",
        "mp3",
        "-af",
        "aresample=async=1",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # noqa: S603
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "").strip()[-400:]
        raise BcutUnavailableError(f"ffmpeg mp3 conversion failed: {detail or result.returncode}")
    return out_path


def _load_mp3_bytes(file_value: str | Path | bytes) -> bytes:
    if isinstance(file_value, bytes):
        return file_value

    file_path = Path(file_value).expanduser()
    if not file_path.exists():
        raise BcutUnavailableError(f"media file not found: {file_path}")
    with tempfile.TemporaryDirectory(prefix="ncds-bcut-") as tmp:
        mp3_path = _ensure_mp3(file_path, Path(tmp))
        return mp3_path.read_bytes()


def request_upload(session: requests.Session, audio_bytes: bytes) -> UploadTicket:
    payload = {
        "type": 2,
        "name": "audio.mp3",
        "size": len(audio_bytes),
        "ResourceFileType": "mp3",
        "model_id": DEFAULT_MODEL_ID,
    }
    response = session.post(API_REQ_UPLOAD, data=json.dumps(payload), headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, dict):
        raise BcutUnavailableError(f"Bcut resource/create returned no data: {response.text[:400]}")

    upload_urls = data.get("upload_urls") or []
    if not upload_urls:
        raise BcutUnavailableError("Bcut resource/create returned no upload_urls")
    try:
        return UploadTicket(
            in_boss_key=str(data["in_boss_key"]),
            resource_id=str(data["resource_id"]),
            upload_id=str(data["upload_id"]),
            upload_urls=[str(url) for url in upload_urls],
            per_size=int(data["per_size"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BcutUnavailableError(f"Bcut upload ticket is incomplete: {json.dumps(data, ensure_ascii=True)}") from exc


def upload_parts(session: requests.Session, ticket: UploadTicket, audio_bytes: bytes) -> list[str]:
    etags: list[str] = []
    for index, upload_url in enumerate(ticket.upload_urls):
        start = index * ticket.per_size
        end = (index + 1) * ticket.per_size
        response = session.put(
            upload_url,
            data=audio_bytes[start:end],
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        etag = response.headers.get("Etag") or response.headers.get("ETag")
        if not etag:
            raise BcutUnavailableError(f"Bcut upload part {index} returned no ETag")
        etags.append(etag)
    return etags


def commit_upload(session: requests.Session, ticket: UploadTicket, etags: list[str]) -> str:
    payload = {
        "InBossKey": ticket.in_boss_key,
        "ResourceId": ticket.resource_id,
        "Etags": ",".join(etags),
        "UploadId": ticket.upload_id,
        "model_id": DEFAULT_MODEL_ID,
    }
    response = session.post(API_COMMIT_UPLOAD, data=json.dumps(payload), headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, dict) or not data.get("download_url"):
        raise BcutUnavailableError(f"Bcut upload complete returned no download_url: {response.text[:400]}")
    return str(data["download_url"])


def create_task(session: requests.Session, resource_url: str) -> str:
    response = session.post(
        API_CREATE_TASK,
        json={"resource": resource_url, "model_id": DEFAULT_MODEL_ID},
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, dict) or not data.get("task_id"):
        raise BcutUnavailableError(f"Bcut task create returned no task_id: {response.text[:400]}")
    return str(data["task_id"])


def query_result(session: requests.Session, task_id: str) -> dict[str, Any]:
    response = session.get(
        API_QUERY_RESULT,
        params={"model_id": QUERY_MODEL_ID, "task_id": task_id},
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, dict):
        raise BcutUnavailableError(f"Bcut task result returned no data: {response.text[:400]}")
    return data


def wait_result(
    session: requests.Session,
    task_id: str,
    *,
    max_polls: int = DEFAULT_MAX_POLLS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    last_response: dict[str, Any] | None = None
    for _ in range(max_polls):
        last_response = query_result(session, task_id)
        state = last_response.get("state")
        if state == 4 or str(state) == "4":
            result = last_response.get("result")
            if isinstance(result, str):
                return json.loads(result)
            if isinstance(result, dict):
                return result
            raise BcutUnavailableError("Bcut task completed without result payload")
        if str(state).lower() in {"5", "6", "failed", "error", "cancelled", "canceled"}:
            raise BcutUnavailableError(f"Bcut task failed: {json.dumps(last_response, ensure_ascii=True)}")
        time.sleep(poll_interval_seconds)
    raise TimeoutError(
        f"Bcut task timeout after {max_polls} polls: {json.dumps(last_response or {}, ensure_ascii=True)}"
    )


def extract_text(result: dict[str, Any]) -> str:
    utterances = result.get("utterances")
    if not isinstance(utterances, list):
        return ""
    parts = []
    for item in utterances:
        if isinstance(item, dict):
            transcript = item.get("transcript")
            if isinstance(transcript, str) and transcript.strip():
                parts.append(transcript.strip())
    return "\n".join(parts).strip()


def transcribe_file(
    file_value: str | Path | bytes,
    *,
    session: requests.Session | None = None,
    max_polls: int | None = None,
    poll_interval_seconds: float | None = None,
    on_progress: ProgressFn = noop,
) -> BcutTranscript:
    resolved_max_polls = max_polls if max_polls is not None else int(
        os.getenv("OPENCLAW_BCUT_MAX_POLLS", DEFAULT_MAX_POLLS)
    )
    resolved_poll_interval = poll_interval_seconds if poll_interval_seconds is not None else float(
        os.getenv("OPENCLAW_BCUT_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
    )

    audio_bytes = _load_mp3_bytes(file_value)
    http = session or requests.Session()
    on_progress("Bcut ASR: 申请上传...")
    ticket = request_upload(http, audio_bytes)
    on_progress(f"Bcut ASR: 上传音频({len(ticket.upload_urls)} 分片)...")
    etags = upload_parts(http, ticket, audio_bytes)
    resource_url = commit_upload(http, ticket, etags)
    on_progress("Bcut ASR: 创建识别任务...")
    task_id = create_task(http, resource_url)
    on_progress("Bcut ASR: 等待识别结果...")
    result = wait_result(
        http,
        task_id,
        max_polls=resolved_max_polls,
        poll_interval_seconds=resolved_poll_interval,
    )
    text = extract_text(result)
    if not text:
        raise BcutUnavailableError(f"Bcut task completed without transcript text: {json.dumps(result, ensure_ascii=True)}")
    return BcutTranscript(text=text, raw_response=result, task_id=task_id)
