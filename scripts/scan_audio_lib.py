#!/usr/bin/env python3
"""扫描本地音源库,生成/更新 library.json 骨架(给伯牙 boya 选素材用)。

把 bgm/*.{mp3,wav} 与 sfx/*.{mp3,wav} 丢进 assets/audio_lib/ 后跑本脚本:
- 用 ffprobe 自动补 duration_s
- 新文件补一份空标签骨架(mood/scene/cue 留给人或伯牙后续填)
- **已存在的条目只更新 duration,不覆盖你已填的标签**

另带 --placeholders:在空库里合成几条合成音(正弦/短促 blip),
用于在没有真素材时把"人声+BGM+音效"端到端管线先跑通。

用法:
  python3 scripts/scan_audio_lib.py                 # 扫默认库 assets/audio_lib
  python3 scripts/scan_audio_lib.py --lib <dir>
  python3 scripts/scan_audio_lib.py --placeholders  # 先造占位素材再扫
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIB = ROOT / "assets" / "audio_lib"
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg")

# 占位 BGM:每条 (文件名, 频率Hz, 时长s, 标签骨架)
PLACEHOLDER_BGM = [
    ("calm_think.mp3", 220, 30, {"mood": ["沉静", "理性"], "energy": 2, "tempo": "slow", "scene": ["认知", "职场"]}),
    ("tension_reveal.mp3", 330, 30, {"mood": ["紧张", "悬念"], "energy": 4, "tempo": "mid", "scene": ["认知"]}),
]
# 占位 SFX:每条 (文件名, 频率Hz, 时长s, cue, gain_db)
PLACEHOLDER_SFX = [
    ("hook.mp3", 880, 0.4, "hook", -4),
    ("golden.mp3", 1320, 0.5, "golden", -6),
    ("reveal.mp3", 660, 0.5, "reveal", -5),
    ("transition.mp3", 520, 0.3, "transition", -8),
    ("close.mp3", 440, 0.8, "close", -6),
]


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, text=True, capture_output=True,
    )
    return round(float(out.stdout.strip()), 2)


def _tone(path: Path, freq: int, sec: float, fade: bool = True) -> None:
    af = "afade=t=in:st=0:d=0.05,afade=t=out:st={}:d=0.1".format(max(0.0, sec - 0.1)) if fade else "anull"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={sec}",
         "-af", af, "-acodec", "libmp3lame", "-q:a", "5", str(path)],
        check=True,
    )


def make_placeholders(lib: Path) -> None:
    """合成占位音 + **种入已知标签**(scan 会保留),让 --placeholders 真正打通含 SFX 的整条管线。"""
    (lib / "bgm").mkdir(parents=True, exist_ok=True)
    (lib / "sfx").mkdir(parents=True, exist_ok=True)
    seed: dict[str, list] = {"bgm": [], "sfx": []}
    for name, freq, sec, tags in PLACEHOLDER_BGM:
        _tone(lib / "bgm" / name, freq, sec)
        seed["bgm"].append({"file": f"bgm/{name}", "loopable": True, **tags})
    for name, freq, sec, cue, gain in PLACEHOLDER_SFX:
        _tone(lib / "sfx" / name, freq, sec)
        seed["sfx"].append({"file": f"sfx/{name}", "cue": cue, "gain_db": gain})
    # 先把标签写进 manifest,随后的 scan() 只补 duration、不覆盖这些标签
    (lib / "library.json").write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[placeholders] 合成 {len(PLACEHOLDER_BGM)} BGM + {len(PLACEHOLDER_SFX)} SFX(含标签) 到 {lib}")


def scan(lib: Path) -> dict:
    manifest = lib / "library.json"
    existing = {"bgm": {}, "sfx": {}}
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for kind in ("bgm", "sfx"):
            for e in data.get(kind, []) or []:
                existing[kind][e["file"]] = e

    result: dict[str, list] = {"bgm": [], "sfx": []}
    for kind in ("bgm", "sfx"):
        d = lib / kind
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in AUDIO_EXT:
                continue
            rel = f"{kind}/{f.name}"
            entry = dict(existing[kind].get(rel, {}))  # 保留已填标签
            entry["file"] = rel
            entry["duration_s"] = ffprobe_duration(f)
            if kind == "bgm":
                entry.setdefault("mood", [])
                entry.setdefault("energy", None)
                entry.setdefault("tempo", None)
                entry.setdefault("scene", [])
                entry.setdefault("loopable", True)
            else:
                entry.setdefault("cue", None)  # hook/golden/reveal/transition/close
                entry.setdefault("gain_db", -6)
            result[kind].append(entry)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="扫描本地音源库,生成 library.json 骨架")
    p.add_argument("--lib", default=str(DEFAULT_LIB))
    p.add_argument("--placeholders", action="store_true", help="先合成占位素材(用于无真素材时打通管线)")
    args = p.parse_args(argv)
    lib = Path(args.lib).resolve()

    if args.placeholders:
        make_placeholders(lib)

    result = scan(lib)
    manifest = lib / "library.json"
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[scan] bgm={len(result['bgm'])} sfx={len(result['sfx'])} -> {manifest}")
    miss = [e["file"] for e in result["sfx"] if not e.get("cue")]
    if miss:
        print(f"[scan] 提醒: {len(miss)} 个 SFX 还没填 cue: {', '.join(miss)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
