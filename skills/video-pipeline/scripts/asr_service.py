#!/usr/bin/env python3
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from functools import lru_cache
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import dashscope
    from dashscope import Files as DashscopeFiles
    from dashscope.audio.asr import Transcription as DashscopeTranscription
except ImportError:
    dashscope = None
    DashscopeFiles = None
    DashscopeTranscription = None


def resolve_binary(name: str, env_var: str | None = None, *, required: bool = True) -> str | None:
    candidates = []
    if env_var and os.environ.get(env_var):
        candidates.append(os.environ[env_var])
    found = shutil.which(name)
    if found:
        candidates.append(found)
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    if required:
        raise RuntimeError(f"未找到命令 {name}。请先安装，或通过环境变量 {env_var or name.upper()} 指定路径")
    return None


FFMPEG = resolve_binary("ffmpeg", "OPENCLAW_FFMPEG")
WHISPER = resolve_binary("whisper", "OPENCLAW_WHISPER", required=False)

OPENAI_CODEX_API_BASE = "https://chatgpt.com/backend-api"
DEFAULT_LOCAL_GEMINI_CLI_PATH = Path.home() / ".gemini" / "g.sh"
LOCAL_GEMINI_PROVIDER = "local-gemini"
LOCAL_GEMINI_MODEL_NAME = "g.sh"
OPENAI_CODEX_PROVIDER = "openai-codex"
OPENAI_CODEX_MODEL_NAME = "gpt-5.4"
SINGLE_PASS_PROOFREAD_MODELS = [
    (LOCAL_GEMINI_PROVIDER, LOCAL_GEMINI_MODEL_NAME),
    (OPENAI_CODEX_PROVIDER, OPENAI_CODEX_MODEL_NAME),
]

CHUNKED_PROOFREAD_MODELS = [
    (LOCAL_GEMINI_PROVIDER, LOCAL_GEMINI_MODEL_NAME),
    (OPENAI_CODEX_PROVIDER, OPENAI_CODEX_MODEL_NAME),
]

TINGWU_SCRIPT_TIMEOUT_SECONDS = 1800
TINGWU_MAX_POLLS = 120
TINGWU_POLL_INTERVAL_SECONDS = 3
CHUNK_SECONDS = 120
MAX_WORKERS = 8

PROOFREAD_SYSTEM_PROMPT = (
    "你是一个专业的语音转写文本校对助手。你的唯一任务是对 whisper 语音识别输出的中文文本进行最小限度的校对修正。\n\n"
    "规则：\n"
    "1. 修复明显的语音识别错误（同音字、近音字误识别）\n"
    "2. 修复断句和标点符号（添加缺失的句号、逗号、问号等）\n"
    "3. 删除重复词语和口头禅残留（如连续重复的片段）\n"
    "4. 修复分片拼接处的断裂：句子被截断后在下一段重复开头的，只保留一次\n"
    "5. 保持原文内容和语义完全不变，不添加、不删除、不改写任何实质内容\n"
    "6. 不要改变说话风格、语气词、口语表达\n"
    "7. 遇到不确定的专有名词、人名、品牌名，保留原文写法\n\n"
    "输出要求：\n"
    "- 输出校对后的完整文本，可以使用 markdown 格式（适度分段、标点）\n"
    "- 不要添加标题、序号等原文没有的结构\n"
    "- 不要写任何说明、总结、注释\n"
    "- 不要在开头或结尾添加任何额外文字"
)


@dataclass
class TranscriptionResult:
    status: str
    backendUsed: str | None
    rawText: str | None
    rawTextPath: str | None
    errorKind: str | None
    errorMessage: str | None
    fallbackTriggered: bool
    fallbackReason: str | None

    def __bool__(self) -> bool:
        return self.status == "success" and self.rawTextPath is not None

    def __eq__(self, other) -> bool:
        if isinstance(other, Path):
            return self.rawTextPath is not None and Path(self.rawTextPath) == other
        return super().__eq__(other)

    @property
    def path(self) -> Path | None:
        if not self.rawTextPath:
            return None
        return Path(self.rawTextPath)

    def __getattr__(self, name: str):
        path = self.path
        if path is None:
            raise AttributeError(name)
        return getattr(path, name)


class TingwuBackendUnavailableError(RuntimeError):
    pass


def load_openclaw_config() -> dict:
    config_path = os.path.expanduser("~/.openclaw/openclaw.json")
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def load_dashscope_key() -> str | None:
    if os.environ.get("DASHSCOPE_API_KEY"):
        return os.environ.get("DASHSCOPE_API_KEY")
    config_path = os.path.expanduser("~/.openclaw/config.json")
    if not os.path.exists(config_path):
        return None
    with open(config_path, encoding="utf-8") as handle:
        return json.load(handle).get("dashscope_api_key")


def load_runtime_config() -> dict[str, Any]:
    config_path = os.path.expanduser("~/.openclaw/config.json")
    if not os.path.exists(config_path):
        return {}
    with open(config_path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def get_default_agent_id() -> str:
    return Path(__file__).resolve().parents[3].name


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def expand_home_path(value: str | None) -> Path | None:
    if value is None:
        return None
    trimmed = str(value).strip()
    if not trimmed:
        return None
    return Path(trimmed).expanduser()


def get_local_gemini_cli_path(config: dict | None = None, explicit_path: str | None = None) -> Path:
    cfg = config or load_openclaw_config()
    configured_path = (
        explicit_path
        or cfg.get("models", {}).get("providers", {}).get(LOCAL_GEMINI_PROVIDER, {}).get("cliPath")
        or cfg.get("env", {}).get("vars", {}).get("OPENCLAW_GEMINI_CLI")
        or os.environ.get("OPENCLAW_GEMINI_CLI")
    )
    return expand_home_path(configured_path) or DEFAULT_LOCAL_GEMINI_CLI_PATH


def get_default_auth_profiles_path(agent_id: str | None = None) -> Path:
    resolved_agent_id = agent_id or get_default_agent_id()
    return Path.home() / ".openclaw" / "agents" / resolved_agent_id / "agent" / "auth-profiles.json"


def get_default_auth_state_path(agent_id: str | None = None) -> Path:
    resolved_agent_id = agent_id or get_default_agent_id()
    return Path.home() / ".openclaw" / "agents" / resolved_agent_id / "agent" / "auth-state.json"


def get_openai_codex_base_url(config: dict | None = None) -> str:
    cfg = config or load_openclaw_config()
    configured_base_url = cfg.get("models", {}).get("providers", {}).get(OPENAI_CODEX_PROVIDER, {}).get("baseUrl")
    base_url = configured_base_url or OPENAI_CODEX_API_BASE
    normalized = str(base_url).rstrip("/")
    if normalized.endswith("/codex/responses"):
        return normalized
    if normalized.endswith("/backend-api"):
        return f"{normalized}/codex/responses"
    if normalized.endswith("/responses"):
        return normalized
    return f"{normalized}/responses"


def get_ordered_openai_codex_profile_ids(
    config: dict,
    auth_profiles_store: dict,
    auth_state_store: dict,
) -> list[str]:
    ordered: list[str] = []

    def append(profile_id: str | None) -> None:
        if not isinstance(profile_id, str):
            return
        trimmed = profile_id.strip()
        if not trimmed or trimmed in ordered:
            return
        ordered.append(trimmed)

    for profile_id in auth_state_store.get("order", {}).get(OPENAI_CODEX_PROVIDER, []) or []:
        append(profile_id)

    append(auth_state_store.get("lastGood", {}).get(OPENAI_CODEX_PROVIDER))

    for profile_id in config.get("auth", {}).get("order", {}).get(OPENAI_CODEX_PROVIDER, []) or []:
        append(profile_id)

    for profile_id, credential in (auth_profiles_store.get("profiles", {}) or {}).items():
        if isinstance(credential, dict) and credential.get("provider") == OPENAI_CODEX_PROVIDER:
            append(profile_id)

    append(f"{OPENAI_CODEX_PROVIDER}:default")
    return ordered


def is_usable_openai_codex_credential(credential: Any) -> bool:
    if not isinstance(credential, dict):
        return False
    if credential.get("provider") != OPENAI_CODEX_PROVIDER:
        return False
    if credential.get("type") != "oauth":
        return False
    access_token = credential.get("access")
    return isinstance(access_token, str) and bool(access_token.strip())


def get_openai_codex_access_token(
    config: dict | None = None,
    *,
    auth_profiles_store: dict | None = None,
    auth_state_store: dict | None = None,
    auth_profiles_path: Path | None = None,
    auth_state_path: Path | None = None,
) -> str | None:
    cfg = config or load_openclaw_config()
    env_token = (
        cfg.get("env", {}).get("vars", {}).get("OPENAI_CODEX_ACCESS_TOKEN")
        or os.environ.get("OPENAI_CODEX_ACCESS_TOKEN")
    )
    if isinstance(env_token, str) and env_token.strip():
        return env_token.strip()

    profiles_store = auth_profiles_store or load_json_file(
        auth_profiles_path or get_default_auth_profiles_path(),
        {"version": 1, "profiles": {}},
    )
    state_store = auth_state_store or load_json_file(
        auth_state_path or get_default_auth_state_path(),
        {"version": 1, "order": {}, "lastGood": {}},
    )
    for profile_id in get_ordered_openai_codex_profile_ids(cfg, profiles_store, state_store):
        credential = (profiles_store.get("profiles", {}) or {}).get(profile_id)
        if not is_usable_openai_codex_credential(credential):
            continue
        return credential["access"].strip()
    return None


def get_tingwu_backend() -> str:
    configured = os.environ.get("OPENCLAW_TINGWU_BACKEND")
    if configured and configured.strip():
        return configured.strip().lower()

    config_backend = load_runtime_config().get("tingwu_backend")
    if isinstance(config_backend, str) and config_backend.strip():
        return config_backend.strip().lower()
    return "dashscope"


def get_tingwu_v2_script_path() -> Path:
    configured = os.environ.get("OPENCLAW_TINGWU_SCRIPT")
    if configured and os.path.exists(configured):
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[2] / "tingwu-asr" / "scripts" / "tingwu_v2_transcribe.py"


def is_whisper_usable() -> bool:
    return bool(WHISPER and os.path.isfile(WHISPER))


def is_cloud_asr_usable() -> bool:
    backend = get_tingwu_backend()
    if backend == "legacy":
        return bool(load_dashscope_key()) and dashscope is not None and DashscopeTranscription is not None and DashscopeFiles is not None
    script_path = get_tingwu_v2_script_path()
    if script_path.exists():
        return True
    return bool(load_dashscope_key()) and dashscope is not None and DashscopeTranscription is not None and DashscopeFiles is not None


def build_proofread_prompt(text: str) -> str:
    return f"以下是一段语音转写文本（由 whisper 分片转写后合并），请按规则校对：\n\n{text}"


def build_local_gemini_prompt(prompt: str, system: str | None = None) -> str:
    sections = [
        "你正在通过本地 Gemini CLI 处理一个受严格约束的中文文本任务。",
    ]
    if system:
        sections.extend(["", "【系统角色】", system])
    sections.extend([
        "",
        "【任务要求】",
        prompt,
        "",
        "【硬性输出约束】",
        "只输出任务要求规定的最终内容，不要输出解释、备注、代码块或额外前后缀。",
    ])
    return "\n".join(sections)


def sanitize_gemini_output(text: str) -> str:
    lines = [
        line for line in (text or "").splitlines()
        if not line.startswith("YOLO mode is enabled.")
    ]
    return "\n".join(lines).strip()


def call_local_gemini_model(model_name: str, prompt: str, system: str | None = None) -> str:
    del model_name
    config = load_openclaw_config()
    cli_path = get_local_gemini_cli_path(config)
    if not cli_path.exists():
        raise RuntimeError(f"未找到本地 Gemini CLI: {cli_path}")

    full_prompt = build_local_gemini_prompt(prompt, system)
    result = subprocess.run(
        ["bash", str(cli_path), "--safe"],
        input=full_prompt,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        raise RuntimeError(stderr or stdout or "local-gemini failed")

    output = sanitize_gemini_output(result.stdout)
    if not output:
        raise RuntimeError("local-gemini returned empty output")
    return output


def parse_openai_output_text(data: dict[str, Any]) -> str:
    output = data.get("output") or []
    if not isinstance(output, list):
        return ""
    for item in output:
        content = item.get("content") if isinstance(item, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                return part["text"].strip()
    return ""


def parse_openai_sse_payload(raw: str) -> str:
    text_delta = []
    completed_text = ""
    response_object: dict[str, Any] | None = None
    data_lines: list[str] = []

    def flush_event() -> None:
        nonlocal completed_text, response_object
        if not data_lines:
            return
        payload = "\n".join(data_lines).strip()
        data_lines.clear()
        if not payload or payload == "[DONE]":
            return
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return
        if isinstance(event, dict):
            event_type = event.get("type")
            if event_type == "response.completed" and isinstance(event.get("response"), dict):
                response_object = event["response"]
                return
            if isinstance(event.get("text"), str) and isinstance(event_type, str) and event_type.endswith("output_text.done"):
                completed_text = event["text"]
                return
            if isinstance(event.get("delta"), str) and isinstance(event_type, str) and event_type.endswith("output_text.delta"):
                text_delta.append(event["delta"])

    for line in str(raw or "").splitlines():
        if not line.strip():
            flush_event()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush_event()

    response_text = parse_openai_output_text(response_object or {})
    return response_text or completed_text.strip() or "".join(text_delta).strip()


def call_openai_codex_model(model_name: str, prompt: str, system: str | None = None) -> str:
    config = load_openclaw_config()
    access_token = get_openai_codex_access_token(config)
    if not access_token:
        raise RuntimeError("未找到 OpenAI Codex OAuth access token")

    request_body = {
        "model": model_name,
        "instructions": system or "",
        "input": [{
            "type": "message",
            "role": "user",
            "content": prompt,
        }],
        "stream": True,
        "store": False,
    }
    req = Request(
        get_openai_codex_base_url(config),
        data=json.dumps(request_body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        method="POST",
    )
    with urlopen(req, timeout=300) as resp:
        raw = resp.read().decode("utf-8", errors="replace").strip()
    if not raw:
        raise RuntimeError("OpenAI Codex 返回空响应")
    if raw.startswith("{"):
        text = parse_openai_output_text(json.loads(raw))
    else:
        text = parse_openai_sse_payload(raw)
    if not text:
        raise RuntimeError("OpenAI Codex 返回空文本")
    return text


def call_model(provider: str, model_name: str, prompt: str, system: str | None = None) -> str:
    if provider == LOCAL_GEMINI_PROVIDER:
        return call_local_gemini_model(model_name, prompt, system)
    if provider == OPENAI_CODEX_PROVIDER:
        return call_openai_codex_model(model_name, prompt, system)
    raise RuntimeError(f"不支持的 provider: {provider}")


def proofread_with_fallback(text: str, candidates: list[tuple[str, str]]) -> str:
    prompt = build_proofread_prompt(text)
    errors = []
    for provider, model_name in candidates:
        try:
            print(f"  📝 LLM 校对中 ({provider}/{model_name})...")
            result = call_model(provider, model_name, prompt, PROOFREAD_SYSTEM_PROMPT)
            if result:
                return result
            raise RuntimeError("返回空文本")
        except (HTTPError, URLError, TimeoutError, RuntimeError, KeyError, IndexError, ValueError) as exc:
            errors.append(f"{provider}/{model_name}: {exc}")
            print(f"  ⚠️  {provider}/{model_name} 校对失败，尝试降级: {exc}")
            continue
    print("  ⚠️  所有校对模型均失败，使用原始转写文本")
    if errors:
        print(f"  失败链路: {' | '.join(errors)}")
    return text


def is_proofread_result_usable(raw_text: str, proofread_text: str) -> bool:
    raw = raw_text.strip()
    revised = proofread_text.strip()
    if not raw or not revised:
        return False
    if len(revised) < max(200, int(len(raw) * 0.8)):
        return False
    return True


def build_raw_transcript_output_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(".txt")


def build_chunk_dir(audio_path: Path) -> Path:
    return audio_path.parent / f".chunks_{audio_path.stem}"


def persist_transcription_result(result: TranscriptionResult, audio_path: Path) -> None:
    metadata_path = audio_path.with_suffix(".transcription.json")
    metadata_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")


def build_transcription_failure(
    *,
    backend_used: str,
    error_kind: str,
    error_message: str,
    fallback_triggered: bool = False,
    fallback_reason: str | None = None,
) -> TranscriptionResult:
    return TranscriptionResult(
        status="failed",
        backendUsed=backend_used,
        rawText=None,
        rawTextPath=None,
        errorKind=error_kind,
        errorMessage=error_message,
        fallbackTriggered=fallback_triggered,
        fallbackReason=fallback_reason,
    )


def build_transcription_success(
    *,
    backend_used: str,
    txt_path: Path,
    error_kind: str | None = None,
    error_message: str | None = None,
    fallback_triggered: bool = False,
    fallback_reason: str | None = None,
) -> TranscriptionResult:
    raw_text = txt_path.read_text(encoding="utf-8").strip() if txt_path.exists() else ""
    return TranscriptionResult(
        status="success",
        backendUsed=backend_used,
        rawText=raw_text,
        rawTextPath=str(txt_path),
        errorKind=error_kind,
        errorMessage=error_message,
        fallbackTriggered=fallback_triggered,
        fallbackReason=fallback_reason,
    )


def get_audio_duration(audio_path: Path) -> float:
    if not FFMPEG:
        return 0.0
    cmd = [
        FFMPEG.replace("ffmpeg", "ffprobe"),
        "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def split_audio(audio_path: Path, chunk_dir: Path) -> list[Path]:
    duration = get_audio_duration(audio_path)
    if duration <= 0:
        return [audio_path]

    n_chunks = math.ceil(duration / CHUNK_SECONDS)
    if n_chunks <= 1:
        return [audio_path]

    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    for index in range(n_chunks):
        start = index * CHUNK_SECONDS
        chunk_path = chunk_dir / f"chunk_{index:04d}.wav"
        cmd = [
            FFMPEG, "-i", str(audio_path),
            "-ss", str(start), "-t", str(CHUNK_SECONDS),
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            "-y", str(chunk_path),
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        if chunk_path.exists() and chunk_path.stat().st_size > 0:
            chunks.append(chunk_path)
    return chunks


def transcribe_chunk(args: tuple[str, str]) -> tuple[int, str]:
    chunk_path, language = args
    idx = int(Path(chunk_path).stem.split("_")[1])
    cmd = [
        WHISPER, chunk_path,
        "--model", "base",
        "--language", language,
        "--output_format", "txt",
        "--output_dir", str(Path(chunk_path).parent),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr[-400:] or f"whisper exited with code {result.returncode}")
    txt_path = Path(chunk_path).with_suffix(".txt")
    text = txt_path.read_text().strip() if txt_path.exists() else ""
    return idx, text


def transcribe_via_whisper(audio_path: Path, language: str = "Chinese") -> Path:
    if not WHISPER:
        raise RuntimeError("whisper unavailable")
    output_dir = audio_path.parent
    txt_path = build_raw_transcript_output_path(audio_path)

    duration = get_audio_duration(audio_path)
    n_chunks = math.ceil(duration / CHUNK_SECONDS) if duration > 0 else 1
    if n_chunks <= 1:
        print(f"  转写中 (whisper base, {language})...")
        cmd = [
            WHISPER, str(audio_path),
            "--model", "base",
            "--language", language,
            "--output_format", "txt",
            "--output_dir", str(output_dir),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "").strip()[-200:] or "whisper transcription failed")
        if txt_path.exists():
            raw_text = txt_path.read_text(encoding="utf-8").strip()
            if raw_text:
                proofread_text = proofread_with_fallback(raw_text, SINGLE_PASS_PROOFREAD_MODELS)
                if is_proofread_result_usable(raw_text, proofread_text):
                    txt_path.write_text(proofread_text, encoding="utf-8")
                else:
                    print("  ⚠️  校对结果明显短于原文，保留原始转写文本")
            return txt_path
        raise RuntimeError("转写完成但找不到输出文件")

    print(f"  转写中 (whisper base, {n_chunks} 片 × {MAX_WORKERS} 并行)...")
    chunk_dir = build_chunk_dir(audio_path)
    chunks = split_audio(audio_path, chunk_dir)
    if not chunks:
        raise RuntimeError("音频分片失败")

    print(f"  分片完成: {len(chunks)} 片，开始并行转写...")
    results = {}
    failures = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(transcribe_chunk, (str(chunk), language)): chunk for chunk in chunks}
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            try:
                idx, text = future.result()
                results[idx] = text
                print(f"  ✓ {done_count}/{len(chunks)} 片完成")
            except Exception as exc:
                chunk_path = futures[future]
                failures.append(f"{chunk_path.name}: {exc}")
                print(f"  ⚠️  分片转写异常: {chunk_path.name}: {exc}")

    missing_indexes = [index for index in range(len(chunks)) if index not in results]
    if failures or missing_indexes:
        details = []
        if failures:
            details.extend(failures)
        if missing_indexes:
            details.append(f"missing chunks: {missing_indexes}")
        raise RuntimeError("分片转写不完整: " + " | ".join(details[:20]))

    full_text = "\n".join(results[index] for index in sorted(results.keys()) if results[index])
    proofread_text = proofread_with_fallback(full_text, CHUNKED_PROOFREAD_MODELS)
    if is_proofread_result_usable(full_text, proofread_text):
        full_text = proofread_text
    else:
        print("  ⚠️  校对结果明显短于原文，保留原始合并文本")

    txt_path.write_text(full_text, encoding="utf-8")
    shutil.rmtree(chunk_dir, ignore_errors=True)
    if txt_path.exists() and txt_path.stat().st_size > 0:
        return txt_path
    raise RuntimeError("合并转写结果失败")


def dashscope_field(value, key: str, default=None):
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def upload_file_to_tingwu(audio_path: Path, api_key: str) -> str:
    if DashscopeFiles is None:
        raise TingwuBackendUnavailableError("dashscope files unavailable")
    upload_response = DashscopeFiles.upload(file_path=str(audio_path.resolve()), purpose="file-extract", api_key=api_key)
    if getattr(upload_response, "status_code", None) != HTTPStatus.OK:
        raise RuntimeError(getattr(upload_response, "message", "tingwu file upload failed"))

    upload_output = dashscope_field(upload_response, "output", {}) or {}
    uploaded_files = upload_output.get("uploaded_files", [])
    if not uploaded_files:
        raise RuntimeError("tingwu file upload returned no uploaded_files")

    file_id = uploaded_files[0].get("file_id")
    if not file_id:
        raise RuntimeError("tingwu file upload returned no file_id")

    detail_response = DashscopeFiles.get(file_id, api_key=api_key)
    if getattr(detail_response, "status_code", None) != HTTPStatus.OK:
        raise RuntimeError(getattr(detail_response, "message", "tingwu file detail fetch failed"))

    detail_output = dashscope_field(detail_response, "output", {}) or {}
    file_url = detail_output.get("url")
    if not file_url:
        raise RuntimeError("tingwu file detail returned no url")
    return file_url


def download_tingwu_text(transcription_url: str) -> str:
    request = Request(transcription_url, headers={"User-Agent": "openclaw-video-pipeline/1.0"})
    with urlopen(request, timeout=120) as response:
        data = json.loads(response.read().decode("utf-8"))

    text_parts: list[str] = []
    for transcript in data.get("transcripts", []) or []:
        text = transcript.get("text", "")
        if text:
            text_parts.append(str(text))
    return "\n".join(text_parts).strip()


def extract_tingwu_text(output) -> str:
    text_parts: list[str] = []
    for item in dashscope_field(output, "results", []) or []:
        transcription = dashscope_field(item, "transcription", "")
        if transcription:
            text_parts.append(str(transcription))
            continue
        transcription_url = dashscope_field(item, "transcription_url", "")
        if transcription_url:
            downloaded_text = download_tingwu_text(str(transcription_url))
            if downloaded_text:
                text_parts.append(downloaded_text)
    return "".join(text_parts).strip()


def wait_for_tingwu_result(task, api_key: str):
    wait_method = getattr(DashscopeTranscription, "wait", None)
    if callable(wait_method):
        return wait_method(task, api_key=api_key)

    fetch_method = getattr(DashscopeTranscription, "fetch", None)
    if callable(fetch_method):
        for _ in range(TINGWU_MAX_POLLS):
            status_response = fetch_method(task, api_key=api_key)
            status_output = dashscope_field(status_response, "output")
            status = dashscope_field(status_output, "task_status")
            if status in {"SUCCEEDED", "FAILED"}:
                return status_response
            time.sleep(TINGWU_POLL_INTERVAL_SECONDS)
        raise RuntimeError(f"tingwu polling timeout after {TINGWU_MAX_POLLS} attempts")

    get_result = getattr(DashscopeTranscription, "async_get_result", None)
    if callable(get_result):
        task_id = dashscope_field(dashscope_field(task, "output"), "task_id") or task
        for _ in range(TINGWU_MAX_POLLS):
            status_response = get_result(task_id)
            status_output = dashscope_field(status_response, "output")
            status = dashscope_field(status_output, "task_status")
            if status in {"SUCCEEDED", "FAILED"}:
                return status_response
            time.sleep(TINGWU_POLL_INTERVAL_SECONDS)
        raise RuntimeError(f"tingwu polling timeout after {TINGWU_MAX_POLLS} attempts")

    raise TingwuBackendUnavailableError("dashscope transcription polling unavailable")


def transcribe_via_tingwu_legacy(audio_path: Path) -> Path:
    if dashscope is None or DashscopeTranscription is None or DashscopeFiles is None:
        raise TingwuBackendUnavailableError("dashscope unavailable")
    api_key = load_dashscope_key()
    if not api_key:
        raise TingwuBackendUnavailableError("dashscope api key missing")
    try:
        dashscope.api_key = api_key
    except Exception as exc:
        raise TingwuBackendUnavailableError(f"dashscope setup failed: {exc}") from exc

    file_url = upload_file_to_tingwu(audio_path, api_key)
    try:
        response = DashscopeTranscription.async_call(model="paraformer-v1", file_urls=[file_url])
    except AttributeError as exc:
        raise TingwuBackendUnavailableError(str(exc)) from exc
    if getattr(response, "status_code", None) != HTTPStatus.OK:
        raise RuntimeError(f"{getattr(response, 'status_code', None)} {getattr(response, 'message', '')}")

    response_output = getattr(response, "output", None)
    task_id = dashscope_field(response_output, "task_id", None)
    if not task_id:
        raise RuntimeError("tingwu task_id missing")

    status_response = wait_for_tingwu_result(response, api_key)
    status_code = getattr(status_response, "status_code", None)
    status_message = getattr(status_response, "message", "")
    status_output = dashscope_field(status_response, "output", None)
    if status_code != HTTPStatus.OK:
        raise RuntimeError(f"{status_code} {status_message}")

    status = dashscope_field(status_output, "task_status", None)
    if status == "FAILED":
        raise RuntimeError(status_message or "tingwu transcription failed")
    if status != "SUCCEEDED":
        raise RuntimeError(f"tingwu finished with unexpected status: {status}")

    text = extract_tingwu_text(status_output)
    if not text:
        raise RuntimeError("tingwu transcription result missing text")
    txt_path = build_raw_transcript_output_path(audio_path)
    txt_path.write_text(text, encoding="utf-8")
    return txt_path


def parse_tingwu_script_output(stdout: str) -> dict[str, Any]:
    lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("tingwu script returned no json payload")


def transcribe_via_tingwu_v2_script(audio_path: Path) -> Path:
    script_path = get_tingwu_v2_script_path()
    if not script_path.exists():
        raise TingwuBackendUnavailableError(f"tingwu script missing: {script_path}")

    result = subprocess.run(
        [sys.executable, str(script_path), str(audio_path)],
        capture_output=True,
        text=True,
        timeout=TINGWU_SCRIPT_TIMEOUT_SECONDS,
    )
    payload = parse_tingwu_script_output(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(str(payload.get("error") or result.stderr.strip() or "tingwu script failed"))

    if payload.get("status") != "success":
        raise RuntimeError(str(payload.get("error") or f"tingwu script returned unexpected status: {payload.get('status')}"))

    txt_path = build_raw_transcript_output_path(audio_path)
    text = payload.get("text")
    text_path = payload.get("textPath")
    if isinstance(text, str) and text.strip():
        txt_path.write_text(text.strip(), encoding="utf-8")
        return txt_path
    if isinstance(text_path, str) and text_path.strip():
        candidate = Path(text_path)
        if candidate.exists():
            content = candidate.read_text(encoding="utf-8").strip()
            if content:
                txt_path.write_text(content, encoding="utf-8")
                return txt_path
    raise RuntimeError("tingwu script payload missing text")


def run_cloud_asr(audio_path: Path) -> tuple[str, Path]:
    preferred_backend = get_tingwu_backend()
    if preferred_backend == "legacy":
        backends = [
            ("tingwu-legacy", transcribe_via_tingwu_legacy),
            ("tingwu-v2", transcribe_via_tingwu_v2_script),
        ]
    else:
        backends = [
            ("tingwu-v2", transcribe_via_tingwu_v2_script),
            ("tingwu-legacy", transcribe_via_tingwu_legacy),
        ]

    last_error: Exception | None = None
    for backend_name, backend in backends:
        try:
            return backend_name, backend(audio_path)
        except Exception as exc:
            last_error = exc
            continue
    if last_error is None:
        raise TingwuBackendUnavailableError("no tingwu backend configured")
    raise last_error


def transcribe_audio(audio_path: Path, language: str = "Chinese") -> TranscriptionResult:
    try:
        backend_used, txt_path = run_cloud_asr(audio_path)
        result = build_transcription_success(
            backend_used=backend_used,
            txt_path=txt_path,
            fallback_triggered=backend_used != "tingwu-v2",
            fallback_reason="tingwu-v2 failed" if backend_used == "tingwu-legacy" else None,
        )
        persist_transcription_result(result, audio_path)
        return result
    except Exception as cloud_exc:
        try:
            txt_path = transcribe_via_whisper(audio_path, language=language)
        except Exception as fallback_exc:
            result = build_transcription_failure(
                backend_used="whisper",
                error_kind="fallback-failed",
                error_message=str(fallback_exc),
                fallback_triggered=True,
                fallback_reason=f"tingwu chain failed: {cloud_exc}",
            )
            persist_transcription_result(result, audio_path)
            return result

        result = build_transcription_success(
            backend_used="whisper",
            txt_path=txt_path,
            fallback_triggered=True,
            fallback_reason=f"tingwu chain failed: {cloud_exc}",
        )
        persist_transcription_result(result, audio_path)
        return result


def coerce_transcription_path(transcription_result: Path | TranscriptionResult | None) -> Path | None:
    if isinstance(transcription_result, TranscriptionResult):
        return transcription_result.path
    return transcription_result
