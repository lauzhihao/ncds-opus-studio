"""通义听悟 vendor adapter：只负责提交听写任务并取回原始文本。"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ._base import read_dashscope_key, read_repo_env_value

try:
    import dashscope as dashscope_module
    from dashscope import Files as DashScopeFiles
    from dashscope.audio.asr import Transcription as DashScopeTranscription
except ImportError:  # pragma: no cover - depends on optional runtime package
    dashscope_module = None
    DashScopeFiles = None
    DashScopeTranscription = None

try:
    from dashscope.multimodal.tingwu.tingwu import TingWu
except ImportError:  # pragma: no cover - depends on optional runtime package
    TingWu = None


DEFAULT_BASE_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
DEFAULT_MODEL = "tingwu-meeting"
LEGACY_MODEL = "paraformer-v1"
DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_MAX_POLLS = 180


class TingwuUnavailableError(RuntimeError):
    """Raised when the TingWu adapter cannot run or returns no transcript."""


@dataclass
class TingwuTranscript:
    text: str
    raw_response: dict[str, Any]
    data_id: str
    backend: str = "tingwu"
    model: str = DEFAULT_MODEL


def load_runtime_config() -> dict[str, Any]:
    config_path = Path.home() / ".openclaw" / "config.json"
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _config_value(config: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_api_key(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_runtime_config()
    key = (
        os.getenv("OPENCLAW_DASHSCOPE_API_KEY")
        or read_dashscope_key()
        or _config_value(cfg, "dashscope_api_key")
    )
    if not key:
        raise TingwuUnavailableError("missing DashScope API key")
    return key.strip()


def resolve_app_id(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_runtime_config()
    app_id = (
        os.getenv("OPENCLAW_TINGWU_APP_ID")
        or os.getenv("TINGWU_APP_ID")
        or read_repo_env_value("TINGWU_APP_ID")
        or read_repo_env_value("OPENCLAW_TINGWU_APP_ID")
        or _config_value(cfg, "tingwu_app_id")
    )
    if not app_id:
        raise TingwuUnavailableError("missing TingWu app id")
    return app_id.strip()


def resolve_optional_app_id(config: dict[str, Any] | None = None) -> str | None:
    try:
        return resolve_app_id(config)
    except TingwuUnavailableError:
        return None


def resolve_base_api_url(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_runtime_config()
    return (
        os.getenv("OPENCLAW_TINGWU_BASE_API_URL")
        or _config_value(cfg, "tingwu_base_api_url")
        or DEFAULT_BASE_API_URL
    ).strip()


def resolve_model(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_runtime_config()
    return (
        os.getenv("OPENCLAW_TINGWU_MODEL")
        or _config_value(cfg, "tingwu_model")
        or DEFAULT_MODEL
    ).strip()


def is_configured(config: dict[str, Any] | None = None) -> bool:
    try:
        resolve_api_key(config)
        if resolve_optional_app_id(config):
            return TingWu is not None and DashScopeFiles is not None
        return DashScopeTranscription is not None and DashScopeFiles is not None
    except TingwuUnavailableError:
        return False


def build_create_offline_task(app_id: str, file_url: str) -> dict[str, Any]:
    return {
        "task": "createTask",
        "type": "offline",
        "appId": app_id,
        "fileUrl": file_url,
        "phraseId": "",
    }


def build_get_task(data_id: str) -> dict[str, Any]:
    return {
        "task": "getTask",
        "dataId": data_id,
    }


def extract_output_field(payload: dict[str, Any], key: str, default: Any = None) -> Any:
    output = payload.get("output")
    if isinstance(output, dict):
        return output.get(key, default)
    data = payload.get("data")
    if isinstance(data, dict):
        return data.get(key, default)
    return default


def _response_status_ok(response: Any) -> bool:
    status_code = getattr(response, "status_code", None)
    if status_code is None and isinstance(response, dict):
        status_code = response.get("status_code")
    if status_code is None:
        payload = _response_payload(response)
        return not (payload.get("code") and not (payload.get("data") or payload.get("output")))
    return status_code in {None, HTTPStatus.OK, 200}


def _response_payload(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    payload = json.loads(json.dumps(response, default=lambda item: getattr(item, "__dict__", str(item))))
    return payload if isinstance(payload, dict) else {}


def _field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _raise_for_response(response: Any, operation: str) -> None:
    if _response_status_ok(response):
        return
    message = getattr(response, "message", None)
    payload = _response_payload(response)
    if not message:
        message = payload.get("message") or payload.get("code") or f"{operation} failed"
    raise RuntimeError(str(message))


def upload_local_file(file_path: Path, api_key: str) -> str:
    if DashScopeFiles is None:
        raise TingwuUnavailableError("dashscope files sdk unavailable")
    response = DashScopeFiles.upload(file_path=str(file_path.resolve()), purpose="file-extract", api_key=api_key)
    _raise_for_response(response, "dashscope file upload")
    payload = _response_payload(response)
    uploaded_files = extract_output_field(payload, "uploaded_files", []) or []
    if not uploaded_files:
        raise RuntimeError(f"dashscope file upload returned no uploaded_files: {json.dumps(payload, ensure_ascii=True)}")
    file_id = uploaded_files[0].get("file_id") if isinstance(uploaded_files[0], dict) else None
    if not file_id:
        raise RuntimeError(f"dashscope file upload returned no file_id: {json.dumps(payload, ensure_ascii=True)}")

    detail_response = DashScopeFiles.get(file_id, api_key=api_key)
    _raise_for_response(detail_response, "dashscope file detail")
    detail_payload = _response_payload(detail_response)
    file_url = extract_output_field(detail_payload, "url")
    if not isinstance(file_url, str) or not file_url.strip():
        raise RuntimeError(f"dashscope file detail returned no url: {json.dumps(detail_payload, ensure_ascii=True)}")
    return file_url.strip()


def collect_text(value: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(value, dict):
        for key in ("text", "paragraph", "content", "transcript", "sentence"):
            field = value.get(key)
            if isinstance(field, str) and field.strip():
                texts.append(field.strip())
        for nested_key in ("result", "results", "segments", "sentences", "paragraphs", "transcripts"):
            texts.extend(collect_text(value.get(nested_key)))
    elif isinstance(value, list):
        for item in value:
            texts.extend(collect_text(item))
    return texts


def extract_text_from_task(task_payload: dict[str, Any]) -> str:
    source = task_payload.get("output") if isinstance(task_payload.get("output"), dict) else task_payload
    texts = collect_text(source)
    seen: list[str] = []
    for text in texts:
        if text not in seen:
            seen.append(text)
    return "\n".join(seen).strip()


def extract_legacy_text(response: Any) -> str:
    payload = _response_payload(response)
    direct_text = extract_text_from_task(payload)
    if direct_text:
        return direct_text

    output = _field(response, "output", {})
    results = _field(output, "results", []) or []
    text_parts: list[str] = []
    for item in results:
        transcription = _field(item, "transcription", "")
        if transcription:
            text_parts.append(str(transcription))
            continue
        transcription_url = _field(item, "transcription_url", "")
        if transcription_url:
            parsed = urlparse(str(transcription_url))
            if parsed.scheme not in {"http", "https"}:
                raise RuntimeError(f"unsupported transcription_url scheme: {parsed.scheme or '<empty>'}")
            request = Request(  # noqa: S310 - scheme checked above.
                str(transcription_url),
                headers={"User-Agent": "ncds-opus-factory/1.0"},
            )
            with urlopen(request, timeout=120) as handle:  # noqa: S310 - scheme checked above.
                data = json.loads(handle.read().decode("utf-8"))
            for transcript in data.get("transcripts", []) or []:
                text = transcript.get("text", "")
                if text:
                    text_parts.append(str(text))
    return "\n".join(text_parts).strip()


def create_task(*, api_key: str, base_api_url: str, model: str, app_id: str, file_url: str) -> str:
    if TingWu is None:
        raise TingwuUnavailableError("tingwu sdk unavailable")
    response = TingWu.call(
        model=model,
        user_defined_input=build_create_offline_task(app_id, file_url),
        api_key=api_key,
        base_address=base_api_url,
        parameters={},
    )
    if not isinstance(response, dict):
        raise TingwuUnavailableError("tingwu createTask returned non-dict response")
    data_id = extract_output_field(response, "dataId")
    if not isinstance(data_id, str) or not data_id.strip():
        raise TingwuUnavailableError(f"tingwu createTask missing dataId: {json.dumps(response, ensure_ascii=True)}")
    return data_id


def poll_task(
    *,
    api_key: str,
    base_api_url: str,
    model: str,
    data_id: str,
    max_polls: int = DEFAULT_MAX_POLLS,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    if TingWu is None:
        raise TingwuUnavailableError("tingwu sdk unavailable")
    last_response: dict[str, Any] | None = None
    for _ in range(max_polls):
        response = TingWu.call(
            model=model,
            user_defined_input=build_get_task(data_id),
            api_key=api_key,
            base_address=base_api_url,
        )
        if not isinstance(response, dict):
            raise TingwuUnavailableError("tingwu getTask returned non-dict response")
        last_response = response
        task_status = extract_output_field(response, "taskStatus") or extract_output_field(response, "status")
        if isinstance(task_status, str):
            normalized = task_status.strip().upper()
            if normalized in {"SUCCEEDED", "SUCCESS", "COMPLETED", "FINISHED"}:
                return response
            if normalized in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED"}:
                raise RuntimeError(json.dumps(response, ensure_ascii=True))
        time.sleep(poll_interval_seconds)
    raise TimeoutError(
        f"tingwu getTask timeout after {max_polls} polls: {json.dumps(last_response or {}, ensure_ascii=True)}"
    )


def _wait_legacy_task(task_id: str, api_key: str) -> Any:
    if DashScopeTranscription is None:
        raise TingwuUnavailableError("dashscope transcription sdk unavailable")
    wait = getattr(DashScopeTranscription, "wait", None)
    if callable(wait):
        try:
            return wait(task=task_id, api_key=api_key)
        except TypeError:
            return wait(task=task_id)

    fetch = getattr(DashScopeTranscription, "fetch", None)
    if callable(fetch):
        for _ in range(DEFAULT_MAX_POLLS):
            response = fetch(task_id, api_key=api_key)
            output = _field(response, "output", {})
            status = _field(output, "task_status")
            if status in {"SUCCEEDED", "FAILED"}:
                return response
            time.sleep(DEFAULT_POLL_INTERVAL_SECONDS)
        raise TimeoutError(f"dashscope transcription timeout after {DEFAULT_MAX_POLLS} polls")

    raise TingwuUnavailableError("dashscope transcription polling unavailable")


def transcribe_file_legacy(*, api_key: str, file_url: str) -> TingwuTranscript:
    if dashscope_module is None or DashScopeTranscription is None:
        raise TingwuUnavailableError("dashscope transcription sdk unavailable")
    try:
        dashscope_module.api_key = api_key
    except Exception as exc:  # noqa: BLE001 - SDK 全局配置失败属于运行时不可用
        raise TingwuUnavailableError(f"dashscope setup failed: {exc}") from exc

    response = DashScopeTranscription.async_call(model=LEGACY_MODEL, file_urls=[file_url])
    _raise_for_response(response, "dashscope transcription create")
    output = _field(response, "output", {})
    task_id = _field(output, "task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise TingwuUnavailableError(f"dashscope transcription missing task_id: {json.dumps(_response_payload(response), ensure_ascii=True)}")

    result = _wait_legacy_task(task_id.strip(), api_key)
    _raise_for_response(result, "dashscope transcription result")
    result_output = _field(result, "output", {})
    status = _field(result_output, "task_status")
    if status == "FAILED":
        message = _field(result_output, "message") or _field(result, "message") or "dashscope transcription failed"
        raise RuntimeError(str(message))

    text = extract_legacy_text(result)
    if not text:
        raise TingwuUnavailableError(f"dashscope transcription completed without transcript text: {json.dumps(_response_payload(result), ensure_ascii=True)}")
    return TingwuTranscript(
        text=text,
        raw_response=_response_payload(result),
        data_id=task_id.strip(),
        backend="dashscope-paraformer",
        model=LEGACY_MODEL,
    )


def normalize_file_url(file_value: str | Path) -> str:
    return str(file_value)


def resolve_file_url(file_value: str | Path, api_key: str) -> str:
    file_input = Path(str(file_value)).expanduser()
    if file_input.exists():
        return upload_local_file(file_input, api_key)
    return normalize_file_url(file_value)


def transcribe_file(
    file_value: str | Path,
    *,
    api_key: str | None = None,
    app_id: str | None = None,
    base_api_url: str | None = None,
    model: str | None = None,
    max_polls: int | None = None,
    poll_interval_seconds: int | None = None,
    config: dict[str, Any] | None = None,
) -> TingwuTranscript:
    cfg = config or load_runtime_config()
    resolved_api_key = (api_key or resolve_api_key(cfg)).strip()
    resolved_max_polls = max_polls if max_polls is not None else int(
        os.environ.get("OPENCLAW_TINGWU_MAX_POLLS", DEFAULT_MAX_POLLS)
    )
    resolved_poll_interval = poll_interval_seconds if poll_interval_seconds is not None else int(
        os.environ.get("OPENCLAW_TINGWU_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
    )
    file_url = resolve_file_url(file_value, resolved_api_key)
    resolved_app_id = app_id.strip() if app_id else resolve_optional_app_id(cfg)
    if not resolved_app_id:
        return transcribe_file_legacy(api_key=resolved_api_key, file_url=file_url)

    data_id = create_task(
        api_key=resolved_api_key,
        base_api_url=(base_api_url or resolve_base_api_url(cfg)).strip(),
        model=(model or resolve_model(cfg)).strip(),
        app_id=resolved_app_id.strip(),
        file_url=file_url,
    )
    resolved_base_api_url = (base_api_url or resolve_base_api_url(cfg)).strip()
    resolved_model = (model or resolve_model(cfg)).strip()
    response = poll_task(
        api_key=resolved_api_key,
        base_api_url=resolved_base_api_url,
        model=resolved_model,
        data_id=data_id,
        max_polls=resolved_max_polls,
        poll_interval_seconds=resolved_poll_interval,
    )
    text = extract_text_from_task(response)
    if not text:
        raise TingwuUnavailableError(f"tingwu task completed without transcript text: {json.dumps(response, ensure_ascii=True)}")
    return TingwuTranscript(
        text=text,
        raw_response=response,
        data_id=data_id,
        model=resolved_model,
    )
