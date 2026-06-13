#!/usr/bin/env python3
"""按 scene 整段合成（带字级时间戳），更连贯的韵律。

数据流：
  1. episode.json 按 scene 分组 beats[].zh，拼成整段文本
  2. 调 dashscope SDK v2 (WebSocket) SpeechSynthesizer，启用 word_timestamp_enabled
  3. 落盘：
       audio/scene-<sid>.mp3           — 整段音频
       audio/scene-<sid>.timestamps.json — 字级时间戳 + per-beat fold 结果
  4. 把每 beat 的 audioFile / audioStart / audioEnd（ms）写回 episode.json

参数：
  --force      强制重生（默认幂等：mp3+timestamps 都存在则跳过）
  --workers N  并发请求数（默认 6）
  --only sid[,sid...]  只跑指定 scene
  --no-write   不写回 episode.json（仅生成 mp3 + timestamps）

依赖：$DASHSCOPE_API_KEY、dashscope Python SDK（pip install dashscope）
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback, AudioFormat

HERE = Path(__file__).resolve().parent
EPISODE_JSON = HERE / "episode.json"
AUDIO_DIR = HERE / "audio"

# CosyVoice v3-flash 字级时间戳行为：同一段输入可能发多个 sentence.index
# （一份 sentence-synthesis 累积 + 一份 sentence-end 汇总），需按 begin_index 去重。
# 句尾标点（"。"）也会出现在 words 里，end_index 等于完整文本长度。


class _Collector(ResultCallback):
    def __init__(self):
        self.audio = bytearray()
        self.events = []
        self.error = None
        self.done = threading.Event()

    def on_open(self): pass
    def on_data(self, data: bytes): self.audio.extend(data)
    def on_event(self, message):
        try:
            obj = json.loads(message) if isinstance(message, str) else message
        except Exception:
            obj = {"raw": str(message)}
        self.events.append(obj)
    def on_complete(self): self.done.set()
    def on_error(self, message):
        self.error = str(message)
        self.done.set()
    def on_close(self): pass


def _extract_words(events):
    """每次 result-generated 都重发当前 sentence 累积 words；sentence-end 是最终汇总。
    多个 sentence.index 的 begin_index 重叠（同一句的中间态/终态），按 begin_index 去重。
    """
    final = {}   # idx → words from sentence-end
    longest = {} # idx → longest words seen
    for ev in events:
        if not isinstance(ev, dict):
            continue
        h = ev.get("header") or {}
        if h.get("event") != "result-generated":
            continue
        out = (ev.get("payload") or {}).get("output") or {}
        sent = out.get("sentence") or {}
        idx = sent.get("index")
        ws = sent.get("words") or []
        if idx is None or not ws:
            continue
        if out.get("type") == "sentence-end":
            final[idx] = ws
        if len(ws) > len(longest.get(idx) or []):
            longest[idx] = ws

    by_begin = {}
    for idx in sorted(set(list(final.keys()) + list(longest.keys()))):
        for w in (longest.get(idx) or []):
            by_begin.setdefault(w.get("begin_index"), w)
    for idx in sorted(set(list(final.keys()) + list(longest.keys()))):
        for w in (final.get(idx) or []):
            by_begin[w.get("begin_index")] = w
    return [by_begin[k] for k in sorted(by_begin.keys())]


def _fold_per_beat(words, beat_ranges):
    """beat_ranges: [(beat_idx, zh, char_start, char_end), ...]
    返回 [(beat_idx, audioStart_ms, audioEnd_ms), ...]，命中 0 字的 beat 取相邻 beat 的边界兜底。
    """
    out = []
    for (bi, zh, cs, ce) in beat_ranges:
        in_range = [w for w in words
                    if w.get("begin_index", -1) >= cs
                    and w.get("end_index",   -1) <= ce]
        if in_range:
            out.append({
                "beat": bi,
                "audioStart": min(w["begin_time"] for w in in_range),
                "audioEnd":   max(w["end_time"]   for w in in_range),
                "chars": ce - cs,
                "wordsFound": len(in_range),
            })
        else:
            out.append({"beat": bi, "audioStart": None, "audioEnd": None,
                        "chars": ce - cs, "wordsFound": 0})
    # 兜底：如果某 beat 没字（罕见），用前后 beat 边界
    last_end = 0
    for i, e in enumerate(out):
        if e["audioStart"] is None:
            e["audioStart"] = last_end
            # 下一 beat 的 audioStart 作为 end，没有就 + 200ms
            nxt_start = next((out[j]["audioStart"] for j in range(i+1, len(out))
                              if out[j]["audioStart"] is not None), None)
            e["audioEnd"] = nxt_start if nxt_start is not None else last_end + 200
        last_end = e["audioEnd"]
    return out


def synth_scene(scene_id, beats, *, cfg, lock=None):
    """合成一个 scene 的整段音频 + 时间戳。返回 (audio_bytes, words, per_beat)。"""
    parts, ranges = [], []
    cursor = 0
    for (bi, zh) in beats:
        parts.append(zh)
        ranges.append((bi, zh, cursor, cursor + len(zh)))
        cursor += len(zh)
    combined = "".join(parts)

    fmt_map = {
        22050: AudioFormat.MP3_22050HZ_MONO_256KBPS,
        24000: AudioFormat.MP3_24000HZ_MONO_256KBPS,
    }
    audio_format = fmt_map.get(cfg["sample_rate"], AudioFormat.MP3_22050HZ_MONO_256KBPS)

    last_err = None
    for attempt in range(1, 4):
        cb = _Collector()
        synth = SpeechSynthesizer(
            model=cfg["model"],
            voice=cfg["voice"],
            format=audio_format,
            callback=cb,
            additional_params={"word_timestamp_enabled": True},
        )
        try:
            synth.call(combined, timeout_millis=180000)
            if not cb.done.wait(timeout=180):
                raise TimeoutError("on_complete/on_error not fired in 180s")
            if cb.error:
                raise RuntimeError(cb.error)
            if not cb.audio:
                raise RuntimeError("empty audio")
            words = _extract_words(cb.events)
            if not words:
                raise RuntimeError("no word timestamps in events")
            per_beat = _fold_per_beat(words, ranges)
            return bytes(cb.audio), words, per_beat, combined
        except Exception as e:
            last_err = e
            if lock:
                with lock:
                    print(f"  retry {scene_id} attempt {attempt}: {e}", flush=True)
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"synth {scene_id} failed after 3 attempts: {last_err}")


def _mp3_duration_ms(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return int(round(float(out.strip()) * 1000))


def _fill_scene_gaps(per_beat, mp3_dur_ms):
    """让一个 scene 内 beat 区间在 [0, mp3_dur] 上完全连续无缝：
       - first.audioStart = 0（吃掉前奏静音）
       - 中间 beat.audioEnd 扩到下一 beat.audioStart（吃掉字间停顿）
       - last.audioEnd = mp3_dur（吃掉末尾静音）
    保证 sum(end-start) == mp3 duration，render audio = scene mp3 拼接时
    跟 player 按 beat duration 推进的 timeline 完全等长。
    """
    if not per_beat:
        return
    per_beat[0]["audioStart"] = 0
    for j in range(len(per_beat) - 1):
        if per_beat[j + 1]["audioStart"] > per_beat[j]["audioEnd"]:
            per_beat[j]["audioEnd"] = per_beat[j + 1]["audioStart"]
    if mp3_dur_ms > per_beat[-1]["audioEnd"]:
        per_beat[-1]["audioEnd"] = mp3_dur_ms


def write_per_scene(scene_id, audio_bytes, words, per_beat, combined):
    AUDIO_DIR.mkdir(exist_ok=True)
    mp3 = AUDIO_DIR / f"scene-{scene_id}.mp3"
    ts  = AUDIO_DIR / f"scene-{scene_id}.timestamps.json"
    mp3.write_bytes(audio_bytes)
    try:
        dur_ms = _mp3_duration_ms(mp3)
        _fill_scene_gaps(per_beat, dur_ms)
    except Exception as e:
        print(f"  warn: cannot probe {mp3.name}: {e}", flush=True)
    ts.write_text(json.dumps({
        "scene": scene_id,
        "text": combined,
        "words": words,
        "perBeat": per_beat,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def is_done(scene_id):
    mp3 = AUDIO_DIR / f"scene-{scene_id}.mp3"
    ts  = AUDIO_DIR / f"scene-{scene_id}.timestamps.json"
    return mp3.exists() and ts.exists()


def group_by_scene(ep):
    """按 scene 出现顺序分组 beats。返回 [(scene_id, [(beat_idx, zh), ...]), ...]"""
    seen = {}
    order = []
    for i, b in enumerate(ep["beats"]):
        sid = b.get("scene")
        zh = b.get("zh", "")
        if not sid or not zh:
            continue
        if sid not in seen:
            seen[sid] = []
            order.append(sid)
        seen[sid].append((i, zh))
    return [(sid, seen[sid]) for sid in order]


def load_cfg(ep):
    tts = (ep.get("audio") or {}).get("tts") or {}
    return {
        "model":       os.environ.get("COSY_MODEL") or tts.get("model") or "cosyvoice-v3-flash",
        "voice":       os.environ.get("VOICE")      or tts.get("voice") or "longtian_v3",
        "sample_rate": int(os.environ.get("SAMPLE_RATE") or tts.get("sampleRate") or 22050),
        "format":      tts.get("format") or "mp3",
    }


def inject_episode(ep, per_scene_results):
    """把 per beat 的 audioFile / audioStart / audioEnd 写回 ep["beats"]。"""
    by_beat = {}
    for sid, per_beat in per_scene_results.items():
        for e in per_beat:
            by_beat[e["beat"]] = {
                "audioFile": f"audio/scene-{sid}.mp3",
                "audioStart": int(round(e["audioStart"])),
                "audioEnd":   int(round(e["audioEnd"])),
            }
    for i, b in enumerate(ep["beats"]):
        info = by_beat.get(i)
        if info:
            b["audioFile"]  = info["audioFile"]
            b["audioStart"] = info["audioStart"]
            b["audioEnd"]   = info["audioEnd"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--only", type=str, default=None, help="只跑指定 scene，逗号分隔")
    p.add_argument("--no-write", action="store_true", help="不写回 episode.json")
    p.add_argument("--episode", default=None, help="episode.json 路径（默认模板自带）")
    p.add_argument("--audio-dir", default=None, help="音频输出目录（默认模板 audio/）")
    args = p.parse_args()

    # 允许指向 job 目录（pipeline_runner._execute_tts 调用）：覆盖模块级默认路径
    global EPISODE_JSON, AUDIO_DIR
    if args.episode:
        EPISODE_JSON = Path(args.episode).resolve()
    if args.audio_dir:
        AUDIO_DIR = Path(args.audio_dir).resolve()

    if "DASHSCOPE_API_KEY" not in os.environ:
        print("DASHSCOPE_API_KEY env var not set", file=sys.stderr)
        return 2
    dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]

    ep = json.loads(EPISODE_JSON.read_text(encoding="utf-8"))
    cfg = load_cfg(ep)
    groups = group_by_scene(ep)

    only_set = set(s.strip() for s in args.only.split(",")) if args.only else None
    if only_set:
        groups = [g for g in groups if g[0] in only_set]
        if not groups:
            print(f"no scenes matched --only {args.only}", file=sys.stderr)
            return 2

    AUDIO_DIR.mkdir(exist_ok=True)
    todo = [(sid, beats) for sid, beats in groups if args.force or not is_done(sid)]
    skipped = len(groups) - len(todo)
    total_beats = sum(len(b) for _, b in todo)
    print(f"target: {len(todo)} scenes ({total_beats} beats), skipped {skipped} done"
          f" · model={cfg['model']} voice={cfg['voice']} sr={cfg['sample_rate']} workers={args.workers}")

    lock = threading.Lock()
    results = {}   # sid → per_beat

    def _one(sid, beats):
        audio, words, per_beat, combined = synth_scene(sid, beats, cfg=cfg, lock=lock)
        write_per_scene(sid, audio, words, per_beat, combined)
        return sid, per_beat

    new_count = 0
    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_one, sid, beats): sid for sid, beats in todo}
            for fut in as_completed(futs):
                sid = futs[fut]
                try:
                    s, pb = fut.result()
                    results[s] = pb
                    new_count += 1
                    dur = pb[-1]["audioEnd"] / 1000 if pb else 0
                    with lock:
                        print(f"  [{new_count}/{len(todo)}] {s}  {len(pb)} beats  {dur:.2f}s", flush=True)
                except Exception as e:
                    with lock:
                        print(f"  FAIL {sid}: {e}", file=sys.stderr, flush=True)

    # 复用已存在的 timestamps.json（包括 --force 之外跳过的，以及本次新生成的）
    for sid, beats in groups:
        if sid in results:
            continue
        ts = AUDIO_DIR / f"scene-{sid}.timestamps.json"
        if ts.exists():
            data = json.loads(ts.read_text(encoding="utf-8"))
            results[sid] = data.get("perBeat") or []

    if not args.no_write:
        inject_episode(ep, results)
        EPISODE_JSON.write_text(
            json.dumps(ep, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"episode.json updated ({sum(len(b) for _,b in groups)} beats annotated)")

    print(f"done. new={new_count} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
