#!/usr/bin/env python3
"""
video_pipeline.py — 多平台媒体下载 + 转写 Pipeline
支持：抖音、YouTube、B站、小红书
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from asr_service import (
    TranscriptionResult as AsrTranscriptionResult,
    call_model as asr_call_model,
    coerce_transcription_path as asr_coerce_transcription_path,
    get_local_gemini_cli_path as asr_get_local_gemini_cli_path,
    is_cloud_asr_usable as asr_is_cloud_usable,
    is_whisper_usable as asr_is_whisper_usable,
    transcribe_audio as asr_transcribe_audio,
)


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
        raise RuntimeError(
            f"未找到命令 {name}。请先安装，或通过环境变量 {env_var or name.upper()} 指定路径"
        )
    return None


@lru_cache(maxsize=1)
def get_openclaw_npm_root() -> Path:
    configured = os.environ.get("OPENCLAW_NPM_ROOT")
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return path

    npm_bin = shutil.which("npm")
    if not npm_bin:
        raise RuntimeError("未找到 npm，无法定位 OpenClaw 原生技能目录")

    result = subprocess.run([npm_bin, "root", "-g"], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"执行 npm root -g 失败: {result.stderr.strip()}")

    path = Path(result.stdout.strip())
    if not path.exists():
        raise RuntimeError(f"npm 全局目录不存在: {path}")
    return path


@lru_cache(maxsize=1)
def get_tikhub_script() -> str:
    configured = os.environ.get("OPENCLAW_TIKHUB_SCRIPT")
    if configured and os.path.exists(configured):
        return configured

    candidates = [
        # 优先使用当前 agent workspace 内的 skill，避免依赖全局 npm 安装位置。
        SCRIPT_DIR.parent.parent / "douyin-downloader" / "scripts" / "douyin_download.py",
        Path.home() / ".openclaw" / "skills" / "douyin-downloader" / "scripts" / "douyin_download.py",
        Path.home() / ".openclaw" / "workspace" / "skills" / "douyin-downloader" / "scripts" / "douyin_download.py",
    ]
    try:
        candidates.append(
            get_openclaw_npm_root()
            / "openclaw"
            / "skills"
            / "douyin-downloader"
            / "scripts"
            / "douyin_download.py"
        )
    except RuntimeError:
        pass

    for script in candidates:
        if script.exists():
            return str(script)

    searched = ", ".join(str(script) for script in candidates)
    raise RuntimeError(f"未找到 TikHub 下载脚本。已检查: {searched}")


# 工具路径（支持 Linux/macOS 自动发现）
YT_DLP = resolve_binary("yt-dlp", "OPENCLAW_YT_DLP")
FFMPEG = resolve_binary("ffmpeg", "OPENCLAW_FFMPEG")
LOCAL_GEMINI_PROVIDER = "local-gemini"
LOCAL_GEMINI_MODEL_NAME = "g.sh"
LOCAL_GEMINI_MODEL_REF = "local-gemini/g.sh"
OPENAI_CODEX_PROVIDER = "openai-codex"
OPENAI_CODEX_MODEL_NAME = "gpt-5.4"
OPENAI_CODEX_MODEL_REF = "openai-codex/gpt-5.4"

# 直接复用 asr_service.py 的 provider 实现，避免同一条文字链路维护两套分叉逻辑。
call_model = asr_call_model
get_local_gemini_cli_path = asr_get_local_gemini_cli_path


def load_openclaw_config() -> dict:
    config_path = os.path.expanduser("~/.openclaw/openclaw.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return {}


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


def build_proofread_prompt(text: str) -> str:
    return f"以下是一段语音转写文本（由 whisper 分片转写后合并），请按规则校对：\n\n{text}"


MODEL_POLISH_SYSTEM_PROMPT = (
    "你是一个中文内容润色助手。你的任务是把转写文本整理成更通顺、更清晰、更适合阅读的版本。\n\n"
    "要求：\n"
    "1. 保留原始事实、观点、顺序与语气，不编造信息\n"
    "2. 修正明显病句、错别字、口语残片和重复\n"
    "3. 调整标点与分段，让文本更易读\n"
    "4. 不要添加标题、总结、解释或额外说明\n"
    "5. 只输出润色后的正文"
)

MODEL_REWRITE_SYSTEM_PROMPT = (
    "你是一个中文新媒体改写助手。你的任务是基于已有润色稿，输出更适合发布和传播的版本。\n\n"
    "要求：\n"
    "1. 忠实保留原始事实和核心意思，不编造、不夸大\n"
    "2. 语言更紧凑、更有节奏，适合内容发布\n"
    "3. 允许重组句子和段落，但不要脱离原文\n"
    "4. 不要添加标题、序号、总结或说明\n"
    "5. 只输出改写后的正文"
)

POLISH_SYSTEM_PROMPT = MODEL_POLISH_SYSTEM_PROMPT + "\n6. 输出使用简体中文。"

CANONICAL_MODEL_REGISTRY = [
    {
        "modelId": "gemini",
        "provider": LOCAL_GEMINI_PROVIDER,
        "modelName": LOCAL_GEMINI_MODEL_NAME,
        "modelRef": LOCAL_GEMINI_MODEL_REF,
    },
    {
        "modelId": "gpt54",
        "provider": OPENAI_CODEX_PROVIDER,
        "modelName": OPENAI_CODEX_MODEL_NAME,
        "modelRef": OPENAI_CODEX_MODEL_REF,
    },
]

MODEL_OUTPUT_PREFERENCE = ["gemini", "gpt54"]


def build_polish_prompt(raw_text: str) -> str:
    return f"请对下面这份转写文本进行忠实润色，让表达更通顺、更清晰，但不要改变原意：\n\n{raw_text}"


def build_rewrite_prompt(polished_text: str) -> str:
    return f"请基于下面这份润色稿进行发布向改写，让节奏更好、表达更凝练，但不要改变原意或添加新事实：\n\n{polished_text}"


def get_configured_model_refs() -> set[str]:
    cfg = load_openclaw_config()
    models = cfg.get("agents", {}).get("defaults", {}).get("models", {})
    if not isinstance(models, dict):
        return set()
    return set(models.keys())


def build_model_registry() -> list[dict]:
    cfg = load_openclaw_config()
    configured_refs = get_configured_model_refs()
    local_gemini_cli_path = get_local_gemini_cli_path(cfg)
    registry = []
    for model in CANONICAL_MODEL_REGISTRY:
        item: dict[str, Any] = dict(model)
        if item["provider"] == LOCAL_GEMINI_PROVIDER:
            item["configured"] = local_gemini_cli_path.exists()
        else:
            item["configured"] = item["modelRef"] in configured_refs
        registry.append(item)
    return registry


def is_candidate_output_usable(source_text: str, candidate_text: str) -> bool:
    source = (source_text or "").strip()
    candidate = (candidate_text or "").strip()
    if not source or not candidate:
        return False
    if len(candidate) < max(20, int(len(source) * 0.5)):
        return False
    return True


def build_variant_result(
    *,
    model_id: str,
    status: str,
    path: Path | None = None,
    error_kind: str | None = None,
    reason: str | None = None,
) -> dict:
    return {
        "modelId": model_id,
        "path": str(path) if path else None,
        "status": status,
        "errorKind": error_kind,
        "reason": reason,
    }


def run_model_polish(args: dict) -> dict:
    model = args["model"]
    raw_text = args["raw_text"]
    raw_transcript_path = args["raw_transcript_path"]
    deliverables_dir = args["deliverables_dir"]

    if not model.get("configured"):
        return build_variant_result(model_id=model["modelId"], status="skipped", reason="missing_model_config")

    try:
        polished_text = call_model(
            model["provider"],
            model["modelName"],
            build_polish_prompt(raw_text),
            MODEL_POLISH_SYSTEM_PROMPT,
        ).strip()
    except Exception as exc:
        return build_variant_result(
            model_id=model["modelId"],
            status="failed",
            error_kind="model_error",
            reason=str(exc),
        )

    if not is_candidate_output_usable(raw_text, polished_text):
        return build_variant_result(
            model_id=model["modelId"],
            status="failed",
            error_kind="unusable_output",
            reason="unusable_candidate",
        )

    output_path = build_model_polished_output_path(raw_transcript_path, deliverables_dir, model["modelId"])
    output_path.write_text(polished_text, encoding="utf-8")
    print(format_success_line(f"润色({model['modelId']})", output_path))
    return build_variant_result(model_id=model["modelId"], status="success", path=output_path)


def run_model_rewrite(args: dict) -> dict:
    model = args["model"]
    polished_variant = args["polished_variant"]
    raw_transcript_path = args["raw_transcript_path"]
    deliverables_dir = args["deliverables_dir"]

    if not model.get("configured"):
        return build_variant_result(model_id=model["modelId"], status="skipped", reason="missing_model_config")

    if polished_variant.get("status") != "success" or not polished_variant.get("path"):
        reason = polished_variant.get("reason")
        if reason != "missing_model_config":
            reason = "missing_polished_input"
        return build_variant_result(model_id=model["modelId"], status="skipped", reason=reason)

    polished_path = Path(polished_variant["path"])
    polished_text = polished_path.read_text(encoding="utf-8").strip()

    try:
        rewrite_text = call_model(
            model["provider"],
            model["modelName"],
            build_rewrite_prompt(polished_text),
            MODEL_REWRITE_SYSTEM_PROMPT,
        ).strip()
    except Exception as exc:
        return build_variant_result(
            model_id=model["modelId"],
            status="failed",
            error_kind="model_error",
            reason=str(exc),
        )

    if not is_candidate_output_usable(polished_text, rewrite_text):
        return build_variant_result(
            model_id=model["modelId"],
            status="failed",
            error_kind="unusable_output",
            reason="unusable_candidate",
        )

    output_path = build_model_rewrite_output_path(raw_transcript_path, deliverables_dir, model["modelId"])
    output_path.write_text(rewrite_text, encoding="utf-8")
    print(format_success_line(f"改写({model['modelId']})", output_path))
    return build_variant_result(model_id=model["modelId"], status="success", path=output_path)


def run_parallel_model_outputs(raw_text: str, raw_transcript_path: Path, deliverables_dir: Path) -> tuple[list[dict], list[dict]]:
    registry = build_model_registry()
    if not registry:
        return [], []

    polished_by_id = {}
    with ThreadPoolExecutor(max_workers=len(registry)) as pool:
        futures = {
            pool.submit(
                run_model_polish,
                {
                    "model": model,
                    "raw_text": raw_text,
                    "raw_transcript_path": raw_transcript_path,
                    "deliverables_dir": deliverables_dir,
                },
            ): model["modelId"]
            for model in registry
        }
        for future in as_completed(futures):
            variant = future.result()
            polished_by_id[variant["modelId"]] = variant

    rewrite_by_id = {}
    with ThreadPoolExecutor(max_workers=len(registry)) as pool:
        futures = {
            pool.submit(
                run_model_rewrite,
                {
                    "model": model,
                    "polished_variant": polished_by_id.get(model["modelId"]),
                    "raw_transcript_path": raw_transcript_path,
                    "deliverables_dir": deliverables_dir,
                },
            ): model["modelId"]
            for model in registry
        }
        for future in as_completed(futures):
            variant = future.result()
            rewrite_by_id[variant["modelId"]] = variant

    ordered_polished = [polished_by_id[model["modelId"]] for model in registry]
    ordered_rewrite = [rewrite_by_id[model["modelId"]] for model in registry]
    return ordered_polished, ordered_rewrite


def select_main_variant(results: list[dict], preferred_order: list[str]) -> dict | None:
    by_model_id = {item.get("modelId"): item for item in results if item.get("status") == "success" and item.get("path")}
    for model_id in preferred_order:
        if model_id in by_model_id:
            return by_model_id[model_id]
    return None


def collect_failure_reasons(polished_variants: list[dict], rewrite_variants: list[dict]) -> dict:
    def collect(items: list[dict]) -> dict:
        payload = {}
        for item in items:
            if item["status"] == "success":
                continue
            payload[item["modelId"]] = item.get("reason") or item.get("errorKind")
        return payload

    return {
        "polished": collect(polished_variants),
        "rewrite": collect(rewrite_variants),
    }


def maybe_write_main_variant(source_variant: dict | None, output_path: Path | None, label: str) -> Path | None:
    if not source_variant or not output_path:
        return None
    source_path = source_variant.get("path")
    if not source_path:
        return None
    output_path.write_text(Path(source_path).read_text(encoding="utf-8"), encoding="utf-8")
    print(format_success_line(label, output_path))
    return output_path


def generate_model_outputs(raw_transcript_path: Path) -> dict:
    deliverables_dir = ensure_deliverables_dir(resolve_job_root_from_raw_path(raw_transcript_path))
    raw_text = raw_transcript_path.read_text(encoding="utf-8").strip()
    polished_variants, rewrite_variants = run_parallel_model_outputs(raw_text, raw_transcript_path, deliverables_dir)
    selected_polished = select_main_variant(polished_variants, MODEL_OUTPUT_PREFERENCE)
    selected_rewrite = select_main_variant(rewrite_variants, MODEL_OUTPUT_PREFERENCE)

    polished_output_path = maybe_write_main_variant(
        selected_polished,
        build_main_polished_output_path(raw_transcript_path, deliverables_dir) if selected_polished else None,
        "润色",
    )
    rewrite_output_path = maybe_write_main_variant(
        selected_rewrite,
        build_main_rewrite_output_path(raw_transcript_path, deliverables_dir) if selected_rewrite else None,
        "改写",
    )

    return {
        "transcript": str(raw_transcript_path.resolve()),
        "selectedPolishedModelId": selected_polished["modelId"] if selected_polished else None,
        "selectedRewriteModelId": selected_rewrite["modelId"] if selected_rewrite else None,
        "polishedTranscriptPath": polished_output_path,
        "rewritePath": rewrite_output_path,
        "failureReasons": collect_failure_reasons(polished_variants, rewrite_variants),
        "polishedVariants": polished_variants,
        "rewriteVariants": rewrite_variants,
    }


def proofread_with_fallback(text: str, candidates: list[tuple[str, str]]) -> str:
    prompt = build_proofread_prompt(text)
    system = PROOFREAD_SYSTEM_PROMPT
    errors = []
    for provider, model_name in candidates:
        try:
            print(f"  📝 LLM 校对中 ({provider}/{model_name})...")
            result = call_model(provider, model_name, prompt, system)
            if result:
                return result
            raise RuntimeError("返回空文本")
        except (HTTPError, URLError, TimeoutError, RuntimeError, KeyError, IndexError, ValueError) as e:
            errors.append(f"{provider}/{model_name}: {e}")
            print(f"  ⚠️  {provider}/{model_name} 校对失败，尝试降级: {e}")
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


# 平台识别正则
PLATFORM_PATTERNS = {
    "douyin": re.compile(r"(douyin\.com|iesdouyin\.com)"),
    "youtube": re.compile(r"(youtube\.com|youtu\.be)"),
    "bilibili": re.compile(r"(bilibili\.com|b23\.tv)"),
    "xiaohongshu": re.compile(r"(xiaohongshu\.com|xhslink\.com)"),
    "podcast": re.compile(r"(listennotes\.com|lnns\.co|xiaoyuzhoufm\.com|podcasts\.apple\.com|open\.spotify\.com/episode|ximalaya\.com)"),
}

# 需要 Chrome cookies 的平台
COOKIE_PLATFORMS = {"douyin", "xiaohongshu"}

def detect_platform(url: str) -> str:
    for name, pattern in PLATFORM_PATTERNS.items():
        if pattern.search(url):
            return name
    return "unknown"


def extract_url(text: str) -> str:
    """从文本中提取 URL。处理抖音口令等包含链接的分享文本。"""
    m = re.search(r'https?://\S+', text)
    return m.group(0).rstrip('/') + '/' if m else text


def format_success_line(label: str, file_path: Path) -> str:
    return f"✅ {label}: {file_path.resolve()}"


def build_download_target_path(output_dir: Path, platform: str, media_id: str, extension: str) -> Path:
    return output_dir / f"{platform}_{media_id}.{extension}"


def build_audio_output_path(video_path: Path) -> Path:
    return video_path.with_suffix(".wav")


def ensure_raw_dir(job_root: Path) -> Path:
    raw_dir = job_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir


def ensure_deliverables_dir(job_root: Path) -> Path:
    deliverables_dir = job_root / "deliverables"
    deliverables_dir.mkdir(parents=True, exist_ok=True)
    return deliverables_dir


def build_raw_transcript_output_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(".txt")


def resolve_job_root_from_raw_path(raw_path: Path) -> Path:
    if raw_path.parent.name == "raw":
        return raw_path.parent.parent
    return raw_path.parent


def build_main_polished_output_path(raw_transcript_path: Path, deliverables_dir: Path | None = None) -> Path:
    deliverables_dir = deliverables_dir or ensure_deliverables_dir(resolve_job_root_from_raw_path(raw_transcript_path))
    return deliverables_dir / f"{raw_transcript_path.stem}.polished.txt"


def build_polished_transcript_output_path(raw_transcript_path: Path, deliverables_dir: Path) -> Path:
    return build_main_polished_output_path(raw_transcript_path, deliverables_dir)


def build_main_rewrite_output_path(raw_transcript_path: Path, deliverables_dir: Path | None = None) -> Path:
    deliverables_dir = deliverables_dir or ensure_deliverables_dir(resolve_job_root_from_raw_path(raw_transcript_path))
    return deliverables_dir / f"{raw_transcript_path.stem}.rewrite.txt"


def sanitize_model_id(model_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model_id).strip("-") or "model"


def build_model_polished_output_path(raw_transcript_path: Path, deliverables_dir: Path, model_id: str) -> Path:
    return deliverables_dir / f"{raw_transcript_path.stem}.{sanitize_model_id(model_id)}.polished.txt"


def build_model_rewrite_output_path(raw_transcript_path: Path, deliverables_dir: Path, model_id: str) -> Path:
    return deliverables_dir / f"{raw_transcript_path.stem}.{sanitize_model_id(model_id)}.rewrite.txt"


def build_transcript_output_path(audio_path: Path) -> Path:
    return build_raw_transcript_output_path(audio_path)


def transcribe(audio_path: Path, language: str = "Chinese") -> Path | AsrTranscriptionResult | None:
    return asr_transcribe_audio(audio_path, language=language)


def build_chunk_dir(audio_path: Path) -> Path:
    return audio_path.parent / f".chunks_{audio_path.stem}"


def build_ytdlp_cmd(url: str, platform: str, output_path: str) -> list[str]:
    cmd = [YT_DLP, "-o", output_path, "--no-warnings", "--print", "before_dl:title"]

    if platform in COOKIE_PLATFORMS:
        cmd += ["--cookies-from-browser", "chrome"]

    if platform == "youtube":
        cmd += ["-f", "bestvideo[height<=720]+bestaudio/best[height<=720]"]

    cmd.append(url)
    return cmd


# 优先使用 TikHub 的平台（cookies 不可靠）
TIKHUB_FIRST_PLATFORMS = {"douyin"}


def extract_video_id_from_url(url: str) -> str | None:
    """从抖音 URL 中提取视频 ID（modal_id / aweme_id）"""
    # 标准格式: /video/1234567890
    m = re.search(r'/video/(\d+)', url)
    if m:
        return m.group(1)
    # modal_id 参数
    m = re.search(r'modal_id=(\d+)', url)
    if m:
        return m.group(1)
    return None


def resolve_short_url(url: str) -> str:
    """跟随短链重定向，获取最终 URL"""
    try:
        cmd = ["curl", "-Ls", "-o", "/dev/null", "-w", "%{url_effective}", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.startswith("http"):
            return result.stdout.strip()
    except Exception:
        pass
    return url


def download_via_tikhub(url: str, platform: str, output_dir: Path) -> Path | None:
    """通过 TikHub API 下载抖音视频"""
    # 先解析短链拿到视频 ID
    resolved = url
    if "v.douyin.com" in url or "iesdouyin.com" in url:
        print("  解析短链...")
        resolved = resolve_short_url(url)

    video_id = extract_video_id_from_url(resolved)
    if not video_id:
        print(f"  ⚠️  无法从 URL 提取视频 ID: {resolved[:80]}")
        return None

    print(f"  TikHub 下载中... (video_id: {video_id})")
    tikhub_script = get_tikhub_script()
    result = subprocess.run(
        [sys.executable, tikhub_script, video_id, "--download"],
        capture_output=True, text=True, cwd=str(output_dir),
    )

    if result.returncode != 0:
        stderr_text = result.stderr.strip()
        stdout_text = result.stdout.strip()
        error_text = stderr_text or stdout_text
        print(f"  ❌ TikHub 下载失败: {error_text[-400:] if error_text else '(empty subprocess output)'}")
        return None

    # 找到下载的文件
    video_exts = {".mp4", ".mkv", ".webm", ".flv"}
    candidates = [f for f in output_dir.iterdir() if f.suffix in video_exts]
    if candidates:
        downloaded = max(candidates, key=lambda f: f.stat().st_mtime)
        # 重命名为统一格式
        target = output_dir / f"{platform}_{video_id}.mp4"
        if downloaded != target:
            downloaded.rename(target)
        return target

    print("  ❌ TikHub 下载完成但找不到文件")
    return None


def download_video(url: str, platform: str, output_dir: Path) -> Path | None:
    """下载视频，根据平台选择最优策略"""

    if platform in TIKHUB_FIRST_PLATFORMS:
        # 抖音：优先 TikHub，失败再 yt-dlp
        print(f"  下载中... ({platform}, TikHub 优先)")
        result = download_via_tikhub(url, platform, output_dir)
        if result:
            return result
        print("  ⚠️  TikHub 失败，尝试 yt-dlp...")

    # 其他平台 / TikHub 失败后：yt-dlp
    output_template = str(build_download_target_path(output_dir, platform, "%(id)s", "%(ext)s"))
    cmd = build_ytdlp_cmd(url, platform, output_template)

    print(f"  下载中... ({platform}, yt-dlp)")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        stderr = result.stderr.strip()[-200:]
        # 非 TikHub 优先平台，yt-dlp 失败后尝试 TikHub fallback
        if platform not in TIKHUB_FIRST_PLATFORMS and platform == "douyin":
            print(f"  ⚠️  yt-dlp 失败，尝试 TikHub fallback...")
            return download_via_tikhub(url, platform, output_dir)
        print(f"  ❌ 下载失败: {stderr}")
        return None

    # 提取 title（--print title 输出在 stdout 第一行，在 [download] 之前）
    stdout_lines = result.stdout.splitlines()
    for line in stdout_lines:
        if line.strip() and not line.startswith("["):
            print(f"  标题: {line.strip()}")
            break

    # 找到下载的文件
    for line in stdout_lines:
        if "Destination:" in line:
            path = line.split("Destination:", 1)[1].strip()
            if os.path.exists(path):
                return Path(path)
        if "has already been downloaded" in line:
            match = re.search(r"\[download\] (.+?) has already", line)
            if match and os.path.exists(match.group(1)):
                return Path(match.group(1))

    # fallback: 找输出目录中最新的媒体文件（含音频，兼容播客等纯音频平台）
    media_exts = {".mp4", ".mkv", ".webm", ".flv"} | AUDIO_EXTENSIONS
    candidates = [f for f in output_dir.iterdir() if f.suffix.lower() in media_exts and platform in f.name]
    if candidates:
        return max(candidates, key=lambda f: f.stat().st_mtime)

    print("  ❌ 下载完成但找不到文件")
    return None


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".wma"}


def extract_audio(video_path: Path) -> Path | None:
    audio_path = build_audio_output_path(video_path)

    is_audio = video_path.suffix.lower() in AUDIO_EXTENSIONS
    label = "转换音频格式" if is_audio else "提取音频"
    print(f"  {label}...")

    cmd = [
        FFMPEG, "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        "-y", str(audio_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  ❌ {label}失败: {result.stderr.strip()[-200:]}")
        return None

    return audio_path


# ASR implementation lives in `asr_service.py`.


def write_result_json(raw_output_dir: Path, transcript_path: Path | None, model_outputs: dict | None = None) -> None:
    job_root = resolve_job_root_from_raw_path(raw_output_dir)
    deliverables_dir = ensure_deliverables_dir(job_root)

    def normalize_path(value: Path | str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, Path):
            return str(value.resolve())
        return str(Path(value).resolve())

    if model_outputs is None:
        payload = {
            "transcript": normalize_path(transcript_path),
            "rawTranscriptPath": normalize_path(transcript_path),
            "selectedPolishedModelId": None,
            "selectedRewriteModelId": None,
            "polishedTranscriptPath": None,
            "rewritePath": None,
            "failureReasons": {"polished": {}, "rewrite": {}},
            "polishedVariants": [],
            "rewriteVariants": [],
        }
    else:
        payload = {
            "transcript": model_outputs.get("transcript") or normalize_path(transcript_path),
            "rawTranscriptPath": normalize_path(transcript_path),
            "selectedPolishedModelId": model_outputs.get("selectedPolishedModelId"),
            "selectedRewriteModelId": model_outputs.get("selectedRewriteModelId"),
            "polishedTranscriptPath": normalize_path(model_outputs.get("polishedTranscriptPath")),
            "rewritePath": normalize_path(model_outputs.get("rewritePath")),
            "failureReasons": model_outputs.get("failureReasons", {"polished": {}, "rewrite": {}}),
            "polishedVariants": model_outputs.get("polishedVariants", []),
            "rewriteVariants": model_outputs.get("rewriteVariants", []),
        }
    (deliverables_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def process_url(url: str, output_dir: Path, no_transcribe: bool = False, language: str = "Chinese") -> dict:
    url = extract_url(url)
    platform = detect_platform(url)
    print(f"\n▶ {url}")
    print(f"  平台: {platform}")

    if platform == "unknown":
        print("  ⚠️  无法识别平台，尝试直接下载...")

    video_path = download_video(url, platform, output_dir)
    if not video_path:
        return {
            "input": url,
            "platform": platform,
            "status": "failed",
            "stage": "download",
            "error": "download failed: media file unavailable after downloader step",
        }

    print(format_success_line("下载", video_path))

    if no_transcribe:
        return {
            "input": url,
            "platform": platform,
            "status": "success",
            "stage": "download_only",
            "videoPath": str(video_path.resolve()),
        }

    audio_path = extract_audio(video_path)
    if not audio_path:
        return {
            "input": url,
            "platform": platform,
            "status": "failed",
            "stage": "extract_audio",
            "videoPath": str(video_path.resolve()),
            "error": "extract audio failed",
        }

    transcription_result = transcribe(audio_path, language=language)
    txt_path = asr_coerce_transcription_path(transcription_result)
    if txt_path:
        print(format_success_line("转写", txt_path))
        model_outputs = None
        try:
            model_outputs = generate_model_outputs(txt_path)
        except Exception as exc:
            print(f"  ⚠️  生成清洗/改写产物失败: {exc}")

        write_result_json(output_dir, txt_path, model_outputs)
        # 清理临时音频文件
        audio_path.unlink(missing_ok=True)
        result = {
            "input": url,
            "platform": platform,
            "status": "success",
            "stage": "transcribe",
            "videoPath": str(video_path.resolve()),
            "audioPath": str(audio_path.resolve()),
            "transcriptPath": str(txt_path.resolve()),
        }
        if model_outputs:
            result.update({
                "polishedTranscriptPath": model_outputs.get("polishedTranscriptPath"),
                "rewritePath": model_outputs.get("rewritePath"),
                "selectedPolishedModelId": model_outputs.get("selectedPolishedModelId"),
                "selectedRewriteModelId": model_outputs.get("selectedRewriteModelId"),
            })
        return result
    else:
        print(f"  ⚠️  音频保留: {audio_path.name}")
        error_message = "transcription finished without transcript output"
        if isinstance(transcription_result, AsrTranscriptionResult):
            error_message = transcription_result.errorMessage or error_message
        return {
            "input": url,
            "platform": platform,
            "status": "failed",
            "stage": "transcribe",
            "videoPath": str(video_path.resolve()),
            "audioPath": str(audio_path.resolve()),
            "error": error_message,
        }


def check_tools() -> bool:
    ok = True
    for name, path in [("yt-dlp", YT_DLP), ("ffmpeg", FFMPEG)]:
        if path is None or not os.path.isfile(path):
            print(f"❌ {name} 未找到: {path}")
            ok = False
    if not ok:
        return False

    tingwu_usable = asr_is_cloud_usable()
    whisper_usable = asr_is_whisper_usable()
    if not whisper_usable:
        print("❌ 本地 ASR fallback 不可用")
    if not tingwu_usable:
        print("❌ 云端 ASR backend 不可用")
    if not whisper_usable and not tingwu_usable:
        return False
    return True


def main():
    parser = argparse.ArgumentParser(
        description="多平台媒体下载+转写 Pipeline（抖音/YouTube/B站/小红书/播客）"
    )
    parser.add_argument("urls", nargs="*", help="媒体 URL（支持多个）")
    parser.add_argument("-o", "--output", default=".", help="任务根目录（默认当前目录；脚本会在其下使用 raw/ 与 deliverables/）")
    parser.add_argument("--no-transcribe", action="store_true", help="仅下载，不转写")
    parser.add_argument("--language", default="Chinese", help="转写语言（默认 Chinese）")
    parser.add_argument("--check", action="store_true", help="检查工具是否就绪")

    args = parser.parse_args()

    if args.check:
        sys.exit(0 if check_tools() else 1)

    if not args.urls:
        parser.print_help()
        sys.exit(1)

    if not check_tools():
        print("\n请先安装缺失的工具。")
        sys.exit(1)

    job_root = Path(args.output).resolve()
    job_root.mkdir(parents=True, exist_ok=True)
    output_dir = ensure_raw_dir(job_root)
    ensure_deliverables_dir(job_root)

    print(f"输出目录: {job_root}")
    print(f"待处理: {len(args.urls)} 个链接")

    results = []
    has_failure = False

    for url in args.urls:
        result = process_url(url.strip(), output_dir, args.no_transcribe, language=args.language)
        if not isinstance(result, dict):
            result = {
                "input": url.strip(),
                "status": "failed",
                "stage": "unknown",
                "error": "process_url returned no result",
            }
        results.append(result)
        if result.get("status") != "success":
            has_failure = True
            print(f"  ❌ 处理失败: {result.get('stage', 'unknown')} | {result.get('error', 'unknown error')}")

    if has_failure:
        print("\n❌ 存在失败项")
        sys.exit(1)

    print("\n✅ 全部完成")


if __name__ == "__main__":
    main()
