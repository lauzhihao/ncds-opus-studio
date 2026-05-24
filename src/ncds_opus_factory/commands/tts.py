"""/tts —— 文本批量转语音（DashScope CosyVoice）。

核心抽象自 templates/paper_card_talk/tts_gen.py，统一为命令式接口：
    run(beats=[...], output_dir="audio", voice=..., on_progress=...)

参数差异点：
- beats 直接传字幕数组（daoer 经 HTTP 调时这是首选）
- 也可传 beats_path（指向 beats.js 文件，模板侧 CLI 兜底）
- 幂等：目标 mp3 已存在则跳过，force=True 强制重生

依赖：$DASHSCOPE_API_KEY。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

ProgressFn = Callable[[str], None]

TTS_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"

DEFAULT_MODEL = "cosyvoice-v3-flash"
DEFAULT_VOICE = "longtian_v3"
DEFAULT_RATE = 1.1
DEFAULT_SAMPLE_RATE = 22050
DEFAULT_HTTP_TIMEOUT = int(os.getenv("NOF_TTS_TIMEOUT", "60"))

# 从 beats.js 源码里提取 zh: "..." —— 字符串容许 \" 转义
ZH_PATTERN = re.compile(r'\bzh:\s*"((?:[^"\\]|\\.)*)"')


def _noop(_text: str) -> None:
    return None


def parse_beats_js(text: str) -> list[str]:
    """从 beats.js 源码里提取所有 zh: "..." 字符串。"""
    return [m.group(1) for m in ZH_PATTERN.finditer(text)]


def _synth_one(
    text: str,
    out_path: Path,
    *,
    voice: str,
    rate: float,
    sample_rate: int,
    model: str,
    attempts: int = 4,
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    on_progress: ProgressFn = _noop,
) -> None:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY env var not set")
    payload = {
        "model": model,
        "input": {
            "text": text,
            "voice": voice,
            "format": "mp3",
            "sample_rate": sample_rate,
            "rate": rate,
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_err: Exception | None = None
    for n in range(1, attempts + 1):
        try:
            req = urllib.request.Request(TTS_URL, data=body, method="POST", headers=headers)
            resp = json.load(urllib.request.urlopen(req, timeout=timeout))
            url = resp.get("output", {}).get("audio", {}).get("url")
            if not url:
                raise RuntimeError(f"no audio.url in response: {resp}")
            tmp = out_path.with_suffix(out_path.suffix + ".part")
            urllib.request.urlretrieve(url, tmp)
            tmp.rename(out_path)
            return
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as exc:
            try:
                detail = (
                    exc.read().decode(errors="replace")
                    if isinstance(exc, urllib.error.HTTPError)
                    else str(exc)
                )
            except Exception:
                detail = str(exc)
            wait = 1.5 * n
            on_progress(f"retry {n}/{attempts} after {wait:.1f}s ({detail[:120]})")
            time.sleep(wait)
            last_err = exc
    raise last_err if last_err else RuntimeError("synth failed for unknown reason")


def run(
    beats: list[str] | None = None,
    beats_path: str | None = None,
    output_dir: str = "audio",
    voice: str = DEFAULT_VOICE,
    rate: float = DEFAULT_RATE,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    model: str = DEFAULT_MODEL,
    force: bool = False,
    sleep_between: float = 0.25,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """批量合成 audio/NNNN.mp3。

    Args:
        beats: 字幕数组（与 beats_path 二选一）
        beats_path: beats.js 文件路径（与 beats 二选一）
        output_dir: mp3 输出目录，按 0001.mp3 编号
        voice / rate / sample_rate / model: CosyVoice 合成参数
        force: 已存在的 mp3 也强制重生（默认跳过 = 幂等）
        sleep_between: 句间节流秒数，规避 rate-limit
        on_progress: 进度回调（server.task_runner 注入）

    Returns:
        {audio_files, output_dir, new_count, skipped, total, model, voice}
    """
    if not beats and not beats_path:
        raise ValueError("either 'beats' or 'beats_path' is required")
    if beats is None:
        bp = Path(beats_path)  # type: ignore[arg-type]
        beats = parse_beats_js(bp.read_text(encoding="utf-8"))
    if not beats:
        raise ValueError("no beats to synthesize")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(beats)
    # 文件名宽度按总数取 max(4, ...)，与模板原行为一致
    width = max(4, len(str(total)))
    new_count = 0
    skip_count = 0
    audio_files: list[str] = []

    on_progress(f"TTS 开始：{total} 段 · voice={voice} rate={rate} model={model}")
    for i, zh in enumerate(beats, start=1):
        name = f"{i:0{width}d}.mp3"
        out = out_dir / name
        if out.exists() and not force:
            audio_files.append(str(out))
            skip_count += 1
            continue
        snippet = (zh[:24] + "…") if len(zh) > 24 else zh
        on_progress(f"[{i}/{total}] {snippet}")
        _synth_one(
            zh,
            out,
            voice=voice,
            rate=rate,
            sample_rate=sample_rate,
            model=model,
            on_progress=on_progress,
        )
        audio_files.append(str(out))
        new_count += 1
        if sleep_between > 0:
            time.sleep(sleep_between)

    on_progress(f"TTS 完成：new={new_count} skipped={skip_count} total={total}")
    return {
        "audio_files": audio_files,
        "output_dir": str(out_dir),
        "new_count": new_count,
        "skipped": skip_count,
        "total": total,
        "model": model,
        "voice": voice,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof tts", description="批量 TTS（DashScope CosyVoice）")
    parser.add_argument("--beats-path", help="beats.js 文件路径")
    parser.add_argument("--beats-json", help='JSON 数组字符串，例如 \'["第一句","第二句"]\'')
    parser.add_argument("--output-dir", default="audio")
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE)
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sleep-between", type=float, default=0.25)
    args = parser.parse_args(argv)

    beats_list = json.loads(args.beats_json) if args.beats_json else None

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        beats=beats_list,
        beats_path=args.beats_path,
        output_dir=args.output_dir,
        voice=args.voice,
        rate=args.rate,
        sample_rate=args.sample_rate,
        model=args.model,
        force=args.force,
        sleep_between=args.sleep_between,
        on_progress=on_progress,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
