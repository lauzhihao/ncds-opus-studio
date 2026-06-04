#!/usr/bin/env python3
"""按 beats.js 顺序批量生成 audio/NNNN.mp3，走 DashScope CosyVoice。

- 模型：cosyvoice-v3-flash（plus / v3.5 系列当前 key 未开通）
- 默认音色：longtian_v3（磁性理智男 · 咨询调）
- 默认 rate=1.1，sample_rate=22050，mp3
- 幂等：目标存在则跳过；--force 强制重生
- 全部参数可用环境变量覆写：VOICE / RATE / COSY_MODEL / SAMPLE_RATE

依赖：$DASHSCOPE_API_KEY。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BEATS_JS = HERE / "beats.js"
AUDIO_DIR = HERE / "audio"

URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
MODEL = os.environ.get("COSY_MODEL", "cosyvoice-v3-flash")
VOICE = os.environ.get("VOICE", "longtian_v3")
SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "22050"))
RATE = float(os.environ.get("RATE", "1.1"))

ZH_PATTERN = re.compile(r'\bzh:\s*"((?:[^"\\]|\\.)*)"')


def parse_beats(text: str) -> list[str]:
    return [m.group(1) for m in ZH_PATTERN.finditer(text)]


def synth(text: str, out_path: Path, attempts: int = 4) -> None:
    api_key = os.environ["DASHSCOPE_API_KEY"]
    payload = {
        "model": MODEL,
        "input": {
            "text": text,
            "voice": VOICE,
            "format": "mp3",
            "sample_rate": SAMPLE_RATE,
            "rate": RATE,
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_err: Exception | None = None
    for n in range(1, attempts + 1):
        try:
            req = urllib.request.Request(URL, data=body, method="POST", headers=headers)
            resp = json.load(urllib.request.urlopen(req, timeout=60))
            url = resp.get("output", {}).get("audio", {}).get("url")
            if not url:
                raise RuntimeError(f"no audio.url in response: {resp}")
            tmp = out_path.with_suffix(out_path.suffix + ".part")
            urllib.request.urlretrieve(url, tmp)
            tmp.rename(out_path)
            return
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as e:
            try:
                detail = e.read().decode(errors="replace") if isinstance(e, urllib.error.HTTPError) else str(e)
            except Exception:
                detail = str(e)
            wait = 1.5 * n
            print(f"  retry {n}/{attempts - 1} after {wait:.1f}s ({detail[:200]})", flush=True)
            time.sleep(wait)
            last_err = e
    if last_err is not None:
        raise last_err


def main() -> int:
    if "DASHSCOPE_API_KEY" not in os.environ:
        print("DASHSCOPE_API_KEY env var not set", file=sys.stderr)
        return 2
    force = "--force" in sys.argv[1:]

    beats = parse_beats(BEATS_JS.read_text(encoding="utf-8"))
    if not beats:
        print("No zh: entries found in beats.js", file=sys.stderr)
        return 2

    AUDIO_DIR.mkdir(exist_ok=True)
    total = len(beats)
    width = max(4, len(str(total)))
    new_count = 0
    skip_count = 0

    print(f"target: {total} beats · model={MODEL} voice={VOICE} rate={RATE} sr={SAMPLE_RATE}")
    for i, zh in enumerate(beats, start=1):
        name = f"{i:0{width}d}.mp3"
        out = AUDIO_DIR / name
        if out.exists() and not force:
            skip_count += 1
            continue
        print(f"[{i:>{width}}/{total}] {zh[:32]}{'…' if len(zh) > 32 else ''}", flush=True)
        synth(zh, out)
        new_count += 1
        time.sleep(0.25)  # gentle throttle

    print(f"done. new={new_count} skipped={skip_count} total={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
