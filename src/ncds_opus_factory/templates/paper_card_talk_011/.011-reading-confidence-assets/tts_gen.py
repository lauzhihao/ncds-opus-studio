#!/usr/bin/env python3
"""按 episode.json 的 beats 顺序批量生成 audio/NNNN.mp3，走 DashScope CosyVoice。

数据源：episode.json（audio.tts.{model,voice,sampleRate,rate,format} + beats[].zh）
- 默认配置在 episode.json 里；环境变量仍可覆写：VOICE / RATE / COSY_MODEL / SAMPLE_RATE
- 幂等：目标存在则跳过；--force 强制重生

依赖：$DASHSCOPE_API_KEY。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
EPISODE_JSON = HERE / "episode.json"
AUDIO_DIR = HERE / "audio"

URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"


def load_episode() -> dict:
    return json.loads(EPISODE_JSON.read_text(encoding="utf-8"))


def load_tts_config(episode: dict) -> dict:
    """合并 episode.audio.tts 与环境变量（环境变量优先）。"""
    tts = (episode.get("audio") or {}).get("tts") or {}
    return {
        "model":       os.environ.get("COSY_MODEL")  or tts.get("model")      or "cosyvoice-v3-flash",
        "voice":       os.environ.get("VOICE")       or tts.get("voice")      or "longtian_v3",
        "sample_rate": int(os.environ.get("SAMPLE_RATE") or tts.get("sampleRate") or 22050),
        "rate":        float(os.environ.get("RATE")  or tts.get("rate")       or 1.0),
        "format":      tts.get("format") or "mp3",
    }


def synth(text: str, out_path: Path, *, cfg: dict, attempts: int = 4) -> None:
    api_key = os.environ["DASHSCOPE_API_KEY"]
    payload = {
        "model": cfg["model"],
        "input": {
            "text": text,
            "voice": cfg["voice"],
            "format": cfg["format"],
            "sample_rate": cfg["sample_rate"],
            "rate": cfg["rate"],
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

    episode = load_episode()
    cfg = load_tts_config(episode)
    beats_zh = [b.get("zh", "") for b in episode.get("beats", []) if b.get("zh")]
    if not beats_zh:
        print("No zh entries found in episode.json beats[]", file=sys.stderr)
        return 2

    AUDIO_DIR.mkdir(exist_ok=True)
    total = len(beats_zh)
    width = max(4, len(str(total)))
    new_count = 0
    skip_count = 0

    print(f"target: {total} beats · model={cfg['model']} voice={cfg['voice']} rate={cfg['rate']} sr={cfg['sample_rate']}")
    for i, zh in enumerate(beats_zh, start=1):
        name = f"{i:0{width}d}.mp3"
        out = AUDIO_DIR / name
        if out.exists() and not force:
            skip_count += 1
            continue
        print(f"[{i:>{width}}/{total}] {zh[:32]}{'…' if len(zh) > 32 else ''}", flush=True)
        synth(zh, out, cfg=cfg)
        new_count += 1
        time.sleep(0.25)  # gentle throttle

    print(f"done. new={new_count} skipped={skip_count} total={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
