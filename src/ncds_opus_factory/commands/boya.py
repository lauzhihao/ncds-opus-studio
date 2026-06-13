"""/boya —— 伯牙：抖音认知成片的「声音」agent。

俞伯牙,高山流水遇知音 —— 以声写意、为「知音」(目标观众)而奏。
负责一条成片的整条声音床:配音(TTS,复用 tts_gen 产的人声)、配乐(BGM)、关键音效(SFX)。

设计原则(和柳永/吴道子对仗):
- 不生成、不下载素材。BGM / SFX 一律从**用户维护的本地库**(assets/audio_lib)里「选 + 排 + 混」。
- 三件事都带判断:BGM 按内容情绪选;SFX 按 beat 类型(钩子/金句/反转/转场/收尾)在时间戳上点睛;
  人声做一轮听感判断(语速/总时长越界标注)。
- 产物 = 混好的 master.mp3 + audio_plan.json(选了啥/为啥/排在哪),交给 render 层直接 mux。

输入契约(一个成片 job 目录):
- audio/NNNN.mp3 —— 逐 beat 人声(tts_gen 产出),**必需**:既是人声主轨,也是时间线的真相来源。
- beats.json (优先) 或 beats.js —— beat 元数据(可选 kind/sfx/emphasis);缺了就按位置兜底(首=钩子,尾=收尾)。

音源库契约见 assets/audio_lib/README.md。
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LIBRARY = ROOT / "assets" / "audio_lib"

# 时间线常量:和 render_stick.mjs 对齐(否则 SFX cue 时间戳会和成片错位)。
INTRO_S = 0.300
GAP_S = 0.080
ENDING_S = 1.500

# beat 类型 -> 想要的 SFX cue 名(库里 sfx[].cue 与之匹配)。
KIND_TO_CUE = {
    "hook": "hook",            # 开场冒犯硬钩
    "golden": "golden",        # 金句
    "reveal": "reveal",        # 反转/反常识
    "transition": "transition",  # 句间转场
    "close": "close",          # 收尾赋能
}

ZH_PATTERN = re.compile(r'\bzh:\s*"((?:[^"\\]|\\.)*)"')

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


# --------------------------------------------------------------------------- #
# ffmpeg / ffprobe 小工具
# --------------------------------------------------------------------------- #
def _ffprobe_duration(path: Path) -> float:
    """读音频时长(秒)。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, text=True, capture_output=True,
    )
    return float(out.stdout.strip())


def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args],
                          text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {proc.stderr[-600:]}")


@functools.lru_cache(maxsize=1)
def _amix_supports_normalize() -> bool:
    """本机 ffmpeg 的 amix 是否支持 normalize 选项（4.4+ 才加入）。

    老 ffmpeg（如 4.1）的 amix 没有 normalize、且强制做 1/N 归一；带上该选项会
    'Option normalize not found' 直接报错。检测一次缓存，给 _mix_audio 选滤镜写法。
    """
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "filter=amix"],
            text=True, capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "normalize" in (out.stdout + out.stderr)


# --------------------------------------------------------------------------- #
# 库与 beats 加载
# --------------------------------------------------------------------------- #
def load_library(library_dir: Path) -> dict[str, list[dict]]:
    """读 library.json。文件不在就报清楚的指引(让用户去建库),不静默兜空。"""
    manifest = library_dir / "library.json"
    if not manifest.exists():
        raise FileNotFoundError(
            f"音源库清单不存在: {manifest}\n"
            f"  先把 bgm/*.mp3 与 sfx/*.mp3 放进 {library_dir},再跑:\n"
            f"  python3 scripts/scan_audio_lib.py    # 自动生成 manifest 骨架"
        )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return {"bgm": data.get("bgm", []) or [], "sfx": data.get("sfx", []) or []}


def voice_clips(job_dir: Path) -> list[Path]:
    """job/audio 下逐 beat 人声,数值序(避免 1/2/10 字典序乱序)。"""
    adir = job_dir / "audio"
    if not adir.is_dir():
        raise FileNotFoundError(f"没有人声目录 {adir};先跑 tts 产 audio/NNNN.mp3")
    files = [p for p in adir.iterdir() if re.fullmatch(r"\d+\.mp3", p.name)]
    files.sort(key=lambda p: int(p.stem))
    if not files:
        raise FileNotFoundError(f"{adir} 里没有 NNNN.mp3 人声")
    return files


def ensure_voice(
    job_dir: Path,
    beats_meta: list[dict],
    *,
    voice: str,
    rate: float,
    regen: bool,
    provider: str | None,
    on_progress: ProgressFn,
) -> None:
    """缺人声时,复用 commands.tts.run() 从 beats 的 zh 生成(伯牙决定音色/语速)。

    不重复造 TTS 轮子:统一走 tts.run(它内部经 provider 抽象屏蔽 API 细节)。
    已有人声且 regen=False 时直接返回(幂等),此时不需要 DASHSCOPE_API_KEY。
    """
    adir = job_dir / "audio"
    has = adir.is_dir() and any(re.fullmatch(r"\d+\.mp3", p.name) for p in adir.iterdir())
    if has and not regen:
        return
    texts = [m.get("zh", "") for m in beats_meta if (m or {}).get("zh")]
    if not texts:
        return  # 没文案也没人声 -> 交给 voice_clips() 抛清楚的错
    from ncds_opus_factory.commands import tts  # 延迟 import,避免无谓加载
    on_progress(f"配音: 库内无人声,伯牙合成 {len(texts)} 句(voice={voice} rate={rate})")
    tts.run(beats=texts, output_dir=str(adir), voice=voice, rate=rate,
            force=regen, provider=provider, on_progress=on_progress)


def load_beat_meta(job_dir: Path) -> list[dict]:
    """beat 元数据:优先 beats.json(完整字段),否则从 beats.js 抠 zh(只够拿条数+文案)。"""
    bj = job_dir / "beats.json"
    if bj.exists():
        data = json.loads(bj.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("beats", [])
    bjs = job_dir / "beats.js"
    if bjs.exists():
        return [{"zh": z} for z in ZH_PATTERN.findall(bjs.read_text(encoding="utf-8"))]
    return []


# --------------------------------------------------------------------------- #
# 时间线:逐 beat 的起始时间(纯函数,便于测试)
# --------------------------------------------------------------------------- #
def beat_timeline(durations: list[float]) -> list[float]:
    """给每个 beat 算成片里的起始秒。规则镜像 render:INTRO + 顺序clip + 句间GAP。"""
    starts: list[float] = []
    t = INTRO_S
    for i, d in enumerate(durations):
        starts.append(round(t, 3))
        t += d
        if i < len(durations) - 1:
            t += GAP_S
    return starts


def total_duration(durations: list[float]) -> float:
    """整条音床时长 = INTRO + 所有clip + 句间GAP + ENDING。"""
    body = sum(durations) + GAP_S * max(0, len(durations) - 1)
    return round(INTRO_S + body + ENDING_S, 3)


# --------------------------------------------------------------------------- #
# 选择:BGM 与 SFX(规则版;后续可换 LLM 驱动,接口不变)
# --------------------------------------------------------------------------- #
def infer_kind(index: int, count: int, meta: dict) -> str:
    """没显式标 kind 时,按位置+文案兜底推断 beat 类型。"""
    if meta.get("kind"):
        return str(meta["kind"])
    if index == 0:
        return "hook"
    if index == count - 1:
        return "close"
    text = meta.get("zh", "")
    if any(k in text for k in ("其实", "真相", "恰恰", "反而", "根本不是")):
        return "reveal"
    return "body"


def select_bgm(library: list[dict], scene: str | None, mood: str | None) -> dict | None:
    """按赛道/情绪选一条 BGM。打分:scene 命中 +2,mood 命中 +2,可循环 +1。空库返回 None。"""
    if not library:
        return None

    def score(b: dict) -> int:
        s = 0
        if scene and scene in (b.get("scene") or []):
            s += 2
        if mood and mood in (b.get("mood") or []):
            s += 2
        if b.get("loopable"):
            s += 1
        return s

    best = max(library, key=score)
    chosen = dict(best)
    chosen["_reason"] = (
        f"scene={scene or '-'} mood={mood or '-'} 命中度最高"
        f"(energy={best.get('energy')}, tempo={best.get('tempo')})"
    )
    return chosen


def _pick_sfx(library: list[dict], cue: str) -> dict | None:
    for s in library:
        if s.get("cue") == cue:
            return s
    return None


def plan_sfx(beats_meta: list[dict], starts: list[float], sfx_lib: list[dict]) -> list[dict]:
    """按每个 beat 的 kind 映射 cue,在该 beat 起始时间落 SFX。库里没有对应 cue 就跳过。"""
    cues: list[dict] = []
    count = len(starts)
    for i, t in enumerate(starts):
        meta = beats_meta[i] if i < len(beats_meta) else {}
        kind = infer_kind(i, count, meta)
        # beat 上显式写了 sfx 优先;否则按 kind 找 cue
        cue_name = meta.get("sfx") or KIND_TO_CUE.get(kind)
        if not cue_name:
            continue
        s = _pick_sfx(sfx_lib, cue_name)
        if not s:
            continue
        cues.append({
            "beat": i + 1,
            "kind": kind,
            "cue": cue_name,
            "time_s": t,
            "file": s["file"],
            "gain_db": s.get("gain_db", -6),
            "_reason": f"beat#{i+1} 判为 {kind} -> cue={cue_name}",
        })
    return cues


# --------------------------------------------------------------------------- #
# 混音:人声主轨 + ducked BGM + 时间戳 SFX -> master.mp3
# --------------------------------------------------------------------------- #
def _build_voice_track(clips: list[Path], durations: list[float], out: Path, work: Path) -> None:
    """拼接逐 beat 人声 + INTRO/GAP/ENDING 静音,等价 render 的 buildAudioTrack。"""
    def silence(sec: float, dst: Path) -> None:
        _ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{sec}",
                 "-acodec", "libmp3lame", "-q:a", "9", str(dst)])

    si, sg, st = work / "si.mp3", work / "sg.mp3", work / "st.mp3"
    silence(INTRO_S, si)
    silence(GAP_S, sg)
    silence(ENDING_S, st)
    lines = [f"file '{si}'"]
    for i, c in enumerate(clips):
        lines.append(f"file '{c}'")
        if i < len(clips) - 1:
            lines.append(f"file '{sg}'")
    lines.append(f"file '{st}'")
    listf = work / "concat.txt"
    listf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listf),
             "-acodec", "libmp3lame", "-b:a", "160k", "-ar", "44100", "-ac", "1", str(out)])


def mix_master(
    voice: Path,
    bgm: dict | None,
    sfx_cues: list[dict],
    library_dir: Path,
    duration: float,
    out: Path,
    work: Path,
    bgm_volume_db: float = -18.0,
    fade_in_s: float = 0.8,
    fade_out_s: float = 1.2,
) -> None:
    """把人声、压低的 BGM、各时间戳 SFX 用 filter_complex 一次混成 master。

    BGM 走静态压低 + 淡入淡出(sidechain ducking 留作后续升级)。
    amix normalize=0 保留各路真实电平,不让 ffmpeg 自动均衡掉人声。
    """
    inputs = ["-i", str(voice)]
    fil: list[str] = []
    mix_labels = ["[0:a]"]
    idx = 1

    # BGM:循环铺满全长 + 压低 + 首尾淡
    if bgm:
        bgm_path = library_dir / bgm["file"]
        inputs += ["-stream_loop", "-1", "-i", str(bgm_path)]
        fo_start = max(0.0, duration - fade_out_s)
        fil.append(
            f"[{idx}:a]atrim=0:{duration},volume={bgm_volume_db}dB,"
            f"afade=t=in:st=0:d={fade_in_s},afade=t=out:st={fo_start}:d={fade_out_s}[bgm]"
        )
        mix_labels.append("[bgm]")
        idx += 1

    # SFX:每条按 gain 调整 + adelay 到时间戳
    for n, cue in enumerate(sfx_cues):
        inputs += ["-i", str(library_dir / cue["file"])]
        delay_ms = int(round(cue["time_s"] * 1000))
        fil.append(
            f"[{idx}:a]volume={cue['gain_db']}dB,adelay={delay_ms}|{delay_ms}[sfx{n}]"
        )
        mix_labels.append(f"[sfx{n}]")
        idx += 1

    n_in = len(mix_labels)
    if n_in == 1:
        # 只有人声:直接转码输出,不走 amix
        _ffmpeg([*inputs, "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", str(out)])
        return
    # normalize=0 保留各路真实电平(已在各路 volume 里调好,不让 amix 再均衡掉人声)。
    # 老 ffmpeg(4.1)的 amix 无此选项、强制做 1/N 归一 -> 用 ,volume=N 抵消还原成等价"求和"。
    if _amix_supports_normalize():
        mix = f"amix=inputs={n_in}:normalize=0:duration=first[a]"
    else:
        mix = f"amix=inputs={n_in}:duration=first,volume={n_in}[a]"
    filt = ";".join(fil) + ";" + "".join(mix_labels) + mix
    _ffmpeg([*inputs, "-filter_complex", filt, "-map", "[a]",
             "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", str(out)])


# --------------------------------------------------------------------------- #
# 听感质检(辨音):规则版,仅标注不打回(校准期,和柳永质检对仗)
# --------------------------------------------------------------------------- #
def audition(durations: list[float], beats_meta: list[dict]) -> dict:
    """对人声做听感判断:总时长、单 beat 语速过快/过慢的粗筛。校准期只标注。"""
    notes: list[str] = []
    total = sum(durations)
    for i, (d, m) in enumerate(zip(durations, beats_meta or [{}] * len(durations))):
        zh = (m or {}).get("zh", "")
        if zh and d > 0:
            cps = len(zh) / d  # 每秒字数(中文)
            if cps > 8.5:
                notes.append(f"beat#{i+1} 语速偏快({cps:.1f}字/秒),可放慢")
            elif cps < 3.0:
                notes.append(f"beat#{i+1} 语速偏慢({cps:.1f}字/秒),可收紧")
    verdict = "warn" if notes else "ok"
    return {"verdict": verdict, "voice_total_s": round(total, 2), "notes": notes}


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #
def run(
    job_dir: str | Path,
    library_dir: str | Path = DEFAULT_LIBRARY,
    scene: str | None = None,
    mood: str | None = None,
    bgm_volume_db: float = -18.0,
    voice: str = "longtian_v3",
    rate: float = 1.1,
    regen_voice: bool = False,
    tts_provider: str | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """为一个成片 job 配齐声音床。返回 audio_plan(同时落盘 audio_plan.json + master.mp3)。

    伯牙统管声音:缺人声时按 voice/rate 复用 tts.run() 合成,再选 BGM、排 SFX、混音、听感质检。
    """
    job = Path(job_dir).resolve()
    lib = Path(library_dir).resolve()
    if not job.is_dir():
        raise FileNotFoundError(f"job 目录不存在: {job}")

    library = load_library(lib)
    beats_meta = load_beat_meta(job)
    ensure_voice(job, beats_meta, voice=voice, rate=rate, regen=regen_voice,
                 provider=tts_provider, on_progress=on_progress)
    clips = voice_clips(job)
    on_progress(f"伯牙启动: {len(clips)} 句人声 · 库 bgm={len(library['bgm'])}/sfx={len(library['sfx'])}")

    durations = [_ffprobe_duration(c) for c in clips]
    starts = beat_timeline(durations)
    dur = total_duration(durations)

    # 选 BGM(可能空库)+ 排 SFX cue
    scene = scene or _guess_scene(beats_meta)
    bgm = select_bgm(library["bgm"], scene, mood)
    on_progress(f"配乐: {bgm['file'] if bgm else '(库内无可用 BGM,跳过)'}")
    sfx_cues = plan_sfx(beats_meta, starts, library["sfx"])
    on_progress(f"音效: 排了 {len(sfx_cues)} 个 cue")

    work = job / ".boya_work"
    work.mkdir(exist_ok=True)
    voice = work / "voice.mp3"
    _build_voice_track(clips, durations, voice, work)

    master = job / "master.mp3"
    mix_master(voice, bgm, sfx_cues, lib, dur, master, work, bgm_volume_db=bgm_volume_db)
    on_progress(f"混音完成: {master.name} ({dur}s)")

    qc = audition(durations, beats_meta)
    if qc["verdict"] == "warn":
        on_progress(f"听感质检: warn - {'; '.join(qc['notes'])}")
    else:
        on_progress("听感质检: ok")

    plan = {
        "job": str(job),
        "generated_at": int(time.time()),
        "scene": scene,
        "voice": {"clips": len(clips), "duration_s": dur, "track": "master.mp3"},
        "bgm": None if not bgm else {
            "file": bgm["file"], "volume_db": bgm_volume_db,
            "reason": bgm.get("_reason"),
        },
        "sfx": [{k: v for k, v in c.items() if not k.startswith("_")} | {"reason": c.get("_reason")}
                for c in sfx_cues],
        "audition": qc,
        "master": "master.mp3",
    }
    (job / "audio_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    on_progress("伯牙完成: audio_plan.json + master.mp3")
    return plan


def _guess_scene(beats_meta: list[dict]) -> str | None:
    """从 beat 的 tag 里粗猜赛道(认知/职场...),给 BGM 选择当线索。"""
    for m in beats_meta:
        tag = (m or {}).get("tag", "")
        for s in ("认知", "职场", "成长", "心理", "健康"):
            if s in tag:
                return s
    return None


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof boya", description="伯牙: 成片声音(配音/配乐/音效)agent")
    parser.add_argument("--job", required=True, help="成片 job 目录(含 audio/NNNN.mp3)")
    parser.add_argument("--library", default=str(DEFAULT_LIBRARY), help="本地音源库目录")
    parser.add_argument("--scene", default=None, help="赛道(认知/职场...),省略则从 beats.tag 猜")
    parser.add_argument("--mood", default=None, help="情绪标签,辅助选 BGM")
    parser.add_argument("--bgm-db", type=float, default=-18.0, help="BGM 压低电平(dB)")
    parser.add_argument("--voice", default="longtian_v3", help="配音音色(缺人声时合成用)")
    parser.add_argument("--rate", type=float, default=1.1, help="配音语速")
    parser.add_argument("--regen-voice", action="store_true", help="强制重生人声(覆盖已有 audio/)")
    parser.add_argument("--tts-provider", default=None, help="TTS 引擎(默认 env NOF_TTS_PROVIDER → cosyvoice)")
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    plan = run(
        job_dir=args.job,
        library_dir=args.library,
        scene=args.scene,
        mood=args.mood,
        bgm_volume_db=args.bgm_db,
        voice=args.voice,
        rate=args.rate,
        regen_voice=args.regen_voice,
        tts_provider=args.tts_provider,
        on_progress=on_progress,
    )
    print(json.dumps({"master": plan["master"], "bgm": plan["bgm"], "sfx_count": len(plan["sfx"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
