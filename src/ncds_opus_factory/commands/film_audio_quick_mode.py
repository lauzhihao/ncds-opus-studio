"""Film 快速去解说：用鬼谷子时间掩码处理 Demucs vocals，再与 bgm 混合。"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

NARRATION_ROLE = "replaceable_narration"
DEFAULT_DUCK_DB = -30.0
DEFAULT_FADE_MS = 60
ProgressFn = Callable[[str], None]


def _noop(_message: str) -> None:
    return None


def narration_intervals(
    segments: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    """合并解说段，避免为数百个相邻 ASR segment 重复创建音量窗口。"""
    ranges: list[tuple[int, int]] = []
    for segment in segments:
        if segment.get("role") != NARRATION_ROLE:
            continue
        try:
            start_ms = int(segment["start_ms"])
            end_ms = int(segment["end_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("film narration segment has invalid timing") from exc
        if start_ms < 0 or end_ms <= start_ms:
            raise RuntimeError(
                "film narration segment has invalid timing: "
                f"start_ms={start_ms}, end_ms={end_ms}"
            )
        ranges.append((start_ms, end_ms))

    merged: list[list[int]] = []
    for start_ms, end_ms in sorted(ranges):
        if not merged or start_ms > merged[-1][1]:
            merged.append([start_ms, end_ms])
            continue
        merged[-1][1] = max(merged[-1][1], end_ms)
    return [(start_ms, end_ms) for start_ms, end_ms in merged]


def clip_intervals(
    ranges_ms: list[tuple[int, int]],
    *,
    start_ms: int,
    end_ms: int,
) -> list[tuple[float, float]]:
    """裁到预览窗口，并转换为从 0 开始的 ffmpeg 秒时间。"""
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError("preview range must satisfy 0 <= start_ms < end_ms")
    clipped: list[tuple[float, float]] = []
    for range_start_ms, range_end_ms in ranges_ms:
        clipped_start_ms = max(start_ms, range_start_ms)
        clipped_end_ms = min(end_ms, range_end_ms)
        if clipped_end_ms <= clipped_start_ms:
            continue
        clipped.append((
            (clipped_start_ms - start_ms) / 1000,
            (clipped_end_ms - start_ms) / 1000,
        ))
    return clipped


def _number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _volume_expression(
    ranges_s: list[tuple[float, float]],
    *,
    narration_gain: float,
    fade_s: float,
) -> str:
    if not 0 <= narration_gain <= 1:
        raise ValueError("narration_gain must be between 0 and 1")
    if fade_s < 0:
        raise ValueError("fade_s must be non-negative")

    expression = "1"
    gain = _number(narration_gain)
    gain_delta = _number(1 - narration_gain)
    for start_s, end_s in reversed(ranges_s):
        if end_s <= start_s:
            continue
        fade = min(fade_s, (end_s - start_s) / 2)
        start = _number(start_s)
        end = _number(end_s)
        if fade <= 0:
            inside = gain
        else:
            fade_end = _number(start_s + fade)
            fade_start = _number(end_s - fade)
            fade_value = _number(fade)
            fade_in = (
                f"1-{gain_delta}*(t-{start})/{fade_value}"
            )
            fade_out = (
                f"{gain}+{gain_delta}*(t-{fade_start})/{fade_value}"
            )
            inside = (
                f"if(lt(t,{fade_end}),{fade_in},"
                f"if(gt(t,{fade_start}),{fade_out},{gain}))"
            )
        expression = (
            f"if(between(t,{start},{end}),{inside},{expression})"
        )
    return expression


def _codec_args(output_path: Path) -> list[str]:
    if output_path.suffix.lower() == ".wav":
        return ["-c:a", "pcm_s16le"]
    return ["-c:a", "libmp3lame", "-b:a", "192k"]


def _run_ffmpeg(args: list[str]) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required for film audio quick mode")
    subprocess.run(  # noqa: S603
        [ffmpeg, "-y", "-loglevel", "error", *args],
        check=True,
        timeout=600,
    )


def render_quick_bed(
    *,
    vocals_path: str | Path,
    bgm_path: str | Path,
    output_path: str | Path,
    narration_ranges_ms: list[tuple[int, int]],
    start_ms: int,
    end_ms: int,
    narration_gain: float,
    fade_ms: int = DEFAULT_FADE_MS,
) -> Path:
    """渲染一段去解说音床；fade 只发生在解说区间内部。"""
    vocals = Path(vocals_path)
    bgm = Path(bgm_path)
    output = Path(output_path)
    for source in (vocals, bgm):
        if not source.is_file():
            raise FileNotFoundError(source)
    if fade_ms < 0:
        raise ValueError("fade_ms must be non-negative")

    output.parent.mkdir(parents=True, exist_ok=True)
    start_s = start_ms / 1000
    end_s = end_ms / 1000
    ranges_s = clip_intervals(
        narration_ranges_ms,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    volume_expression = _volume_expression(
        ranges_s,
        narration_gain=narration_gain,
        fade_s=fade_ms / 1000,
    )
    filter_complex = (
        f"[0:a]atrim=start={_number(start_s)}:end={_number(end_s)},"
        "asetpts=PTS-STARTPTS,"
        f"volume='{volume_expression}':eval=frame[voice];"
        f"[1:a]atrim=start={_number(start_s)}:end={_number(end_s)},"
        "asetpts=PTS-STARTPTS[bed];"
        "[voice][bed]amix=inputs=2:duration=shortest:"
        "dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95:level=false:latency=true[out]"
    )
    _run_ffmpeg([
        "-i",
        str(vocals),
        "-i",
        str(bgm),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        *_codec_args(output),
        str(output),
    ])
    return output


def _render_reference(
    *,
    original_path: str | Path,
    output_path: str | Path,
    start_ms: int,
    end_ms: int,
) -> None:
    original = Path(original_path)
    output = Path(output_path)
    if not original.is_file():
        raise FileNotFoundError(original)
    output.parent.mkdir(parents=True, exist_ok=True)
    filter_audio = (
        f"atrim=start={_number(start_ms / 1000)}:"
        f"end={_number(end_ms / 1000)},asetpts=PTS-STARTPTS"
    )
    _run_ffmpeg([
        "-i",
        str(original),
        "-filter:a",
        filter_audio,
        *_codec_args(output),
        str(output),
    ])


def _load_narration_ranges(guiguzi_path: Path) -> list[tuple[int, int]]:
    try:
        result = json.loads(guiguzi_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read Guiguzi result: {guiguzi_path}") from exc
    if result.get("status") != "done":
        raise RuntimeError("film Guiguzi result must be done")
    segments = result.get("segments")
    if not isinstance(segments, list):
        raise RuntimeError("film Guiguzi result must contain segments")
    return narration_intervals([
        segment for segment in segments if isinstance(segment, dict)
    ])


def render_validation_previews(
    *,
    guiguzi_path: str | Path,
    original_path: str | Path,
    vocals_path: str | Path,
    bgm_path: str | Path,
    output_dir: str | Path,
    start_ms: int,
    duration_ms: int,
    duck_db: float = DEFAULT_DUCK_DB,
    fade_ms: int = DEFAULT_FADE_MS,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """生成原始、duck、mute 三份同区间试听及 manifest。"""
    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    if duck_db > 0:
        raise ValueError("duck_db must not be positive")
    end_ms = start_ms + duration_ms
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    ranges_ms = _load_narration_ranges(Path(guiguzi_path))

    original_output = output_root / "original.mp3"
    duck_output = output_root / "duck-minus-30db.mp3"
    mute_output = output_root / "mute.mp3"
    on_progress("render reference preview")
    _render_reference(
        original_path=original_path,
        output_path=original_output,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    on_progress("render duck preview")
    render_quick_bed(
        vocals_path=vocals_path,
        bgm_path=bgm_path,
        output_path=duck_output,
        narration_ranges_ms=ranges_ms,
        start_ms=start_ms,
        end_ms=end_ms,
        narration_gain=math.pow(10, duck_db / 20),
        fade_ms=fade_ms,
    )
    on_progress("render mute preview")
    render_quick_bed(
        vocals_path=vocals_path,
        bgm_path=bgm_path,
        output_path=mute_output,
        narration_ranges_ms=ranges_ms,
        start_ms=start_ms,
        end_ms=end_ms,
        narration_gain=0.0,
        fade_ms=fade_ms,
    )

    manifest_path = output_root / "manifest.json"
    manifest = {
        "mode": "film_audio_quick_mode_validation",
        "preview": {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": duration_ms,
        },
        "narration_ranges_ms": [
            [range_start_ms, range_end_ms]
            for range_start_ms, range_end_ms in ranges_ms
            if range_end_ms > start_ms and range_start_ms < end_ms
        ],
        "settings": {
            "duck_db": duck_db,
            "fade_ms": fade_ms,
            "fade_boundary": "inside_narration",
        },
        "outputs": {
            "original": str(original_output.resolve()),
            "duck": str(duck_output.resolve()),
            "mute": str(mute_output.resolve()),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    on_progress("film audio previews ready")
    return {**manifest, "manifest": str(manifest_path.resolve())}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render film audio quick-mode validation previews.",
    )
    parser.add_argument("--guiguzi", required=True)
    parser.add_argument("--original", required=True)
    parser.add_argument("--vocals", required=True)
    parser.add_argument("--bgm", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start", type=float, default=20.0)
    parser.add_argument("--duration", type=float, default=40.0)
    parser.add_argument("--duck-db", type=float, default=DEFAULT_DUCK_DB)
    parser.add_argument("--fade-ms", type=int, default=DEFAULT_FADE_MS)
    args = parser.parse_args()

    result = render_validation_previews(
        guiguzi_path=args.guiguzi,
        original_path=args.original,
        vocals_path=args.vocals,
        bgm_path=args.bgm,
        output_dir=args.output_dir,
        start_ms=round(args.start * 1000),
        duration_ms=round(args.duration * 1000),
        duck_db=args.duck_db,
        fade_ms=args.fade_ms,
        on_progress=lambda message: print(f"[film-audio] {message}"),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
