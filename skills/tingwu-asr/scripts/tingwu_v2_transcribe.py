#!/usr/bin/env python3
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from dashscope.multimodal.tingwu.tingwu import TingWu
except ImportError:
    TingWu = None


LOGGER = logging.getLogger("tingwu_v2")
if not LOGGER.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False

DEFAULT_BASE_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
DEFAULT_MODEL = "tingwu-meeting"
DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_MAX_POLLS = 180


def load_runtime_config() -> dict[str, Any]:
    config_path = Path.home() / ".openclaw" / "config.json"
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def get_required_config(config: dict[str, Any], env_key: str, config_key: str) -> str:
    env_value = os.environ.get(env_key)
    if env_value and env_value.strip():
        return env_value.strip()
    config_value = config.get(config_key)
    if isinstance(config_value, str) and config_value.strip():
        return config_value.strip()
    raise RuntimeError(f"missing required config: {config_key}")


def get_optional_config(config: dict[str, Any], env_key: str, config_key: str, default: str) -> str:
    env_value = os.environ.get(env_key)
    if env_value and env_value.strip():
        return env_value.strip()
    config_value = config.get(config_key)
    if isinstance(config_value, str) and config_value.strip():
        return config_value.strip()
    return default


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


def extract_output_field(payload: dict[str, Any], key: str, default=None):
    output = payload.get("output")
    if isinstance(output, dict):
        return output.get(key, default)
    return default


def collect_text(value: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(value, dict):
        for key in ("text", "paragraph", "content", "transcript", "sentence"):
            field = value.get(key)
            if isinstance(field, str) and field.strip():
                texts.append(field.strip())
        for nested_key in ("result", "results", "segments", "sentences", "paragraphs", "transcripts"):
            nested = value.get(nested_key)
            texts.extend(collect_text(nested))
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


def create_task(*, api_key: str, base_api_url: str, model: str, app_id: str, file_url: str) -> str:
    if TingWu is None:
        raise RuntimeError("tingwu sdk unavailable")
    response = TingWu.call(
        model=model,
        user_defined_input=build_create_offline_task(app_id, file_url),
        api_key=api_key,
        base_address=base_api_url,
        parameters={},
    )
    if not isinstance(response, dict):
        raise RuntimeError("tingwu createTask returned non-dict response")
    data_id = extract_output_field(response, "dataId")
    if not isinstance(data_id, str) or not data_id.strip():
        raise RuntimeError(f"tingwu createTask missing dataId: {json.dumps(response, ensure_ascii=True)}")
    return data_id


def poll_task(*, api_key: str, base_api_url: str, model: str, data_id: str, max_polls: int, poll_interval_seconds: int) -> dict[str, Any]:
    if TingWu is None:
        raise RuntimeError("tingwu sdk unavailable")
    last_response: dict[str, Any] | None = None
    for _ in range(max_polls):
        response = TingWu.call(
            model=model,
            user_defined_input=build_get_task(data_id),
            api_key=api_key,
            base_address=base_api_url,
        )
        if not isinstance(response, dict):
            raise RuntimeError("tingwu getTask returned non-dict response")
        last_response = response
        task_status = extract_output_field(response, "taskStatus") or extract_output_field(response, "status")
        if isinstance(task_status, str):
            normalized = task_status.strip().upper()
            if normalized in {"SUCCEEDED", "SUCCESS", "COMPLETED", "FINISHED"}:
                return response
            if normalized in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED"}:
                raise RuntimeError(json.dumps(response, ensure_ascii=True))
        time.sleep(poll_interval_seconds)
    raise RuntimeError(
        f"tingwu getTask timeout after {max_polls} polls: {json.dumps(last_response or {}, ensure_ascii=True)}"
    )


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "error": "usage: tingwu_v2_transcribe.py <file_url_or_path>"}))
        return 1

    config = load_runtime_config()
    api_key = get_required_config(config, "OPENCLAW_DASHSCOPE_API_KEY", "dashscope_api_key")
    app_id = get_required_config(config, "OPENCLAW_TINGWU_APP_ID", "tingwu_app_id")
    base_api_url = get_optional_config(config, "OPENCLAW_TINGWU_BASE_API_URL", "tingwu_base_api_url", DEFAULT_BASE_API_URL)
    model = get_optional_config(config, "OPENCLAW_TINGWU_MODEL", "tingwu_model", DEFAULT_MODEL)
    poll_interval_seconds = int(os.environ.get("OPENCLAW_TINGWU_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS))
    max_polls = int(os.environ.get("OPENCLAW_TINGWU_MAX_POLLS", DEFAULT_MAX_POLLS))
    file_value = sys.argv[1]
    file_input = Path(file_value).expanduser()
    file_url = file_input.resolve().as_uri() if file_input.exists() else file_value

    try:
        LOGGER.info("creating tingwu offline task")
        data_id = create_task(
            api_key=api_key,
            base_api_url=base_api_url,
            model=model,
            app_id=app_id,
            file_url=file_url,
        )
        LOGGER.info("polling tingwu task data_id=%s", data_id)
        response = poll_task(
            api_key=api_key,
            base_api_url=base_api_url,
            model=model,
            data_id=data_id,
            max_polls=max_polls,
            poll_interval_seconds=poll_interval_seconds,
        )
        text = extract_text_from_task(response)
        if not text:
            raise RuntimeError(f"tingwu task completed without transcript text: {json.dumps(response, ensure_ascii=True)}")
        print(
            json.dumps(
                {
                    "status": "success",
                    "backend": "tingwu-v2",
                    "dataId": data_id,
                    "text": text,
                    "rawResponse": response,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        LOGGER.error("tingwu v2 transcription failed: %s", exc)
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
