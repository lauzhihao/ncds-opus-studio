"""音轨能力：ffmpeg 抽原声 + Demucs 分离 人声/伴奏。

底层原语，返回绝对 Path（不知道仓库根，相对化交给调用方）。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from ncds_opus_core.common import cancel

from ._base import ProgressFn, noop


def _run_proc_cancellable(args: list[str], check: cancel.CheckFn,
                          timeout: float = 1800) -> None:
    """轮询式子进程:每秒看一次取消标记,取消则 SIGTERM(线程杀不掉,子进程随便杀)。"""
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while True:
        rc = proc.poll()
        if rc is not None:
            if rc != 0:
                raise RuntimeError(f"subprocess failed rc={rc}: {args[0]}")
            return
        if check():
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise cancel.TaskCancelled("cancelled during subprocess")
        if time.time() - t0 > timeout:
            proc.kill()
            raise RuntimeError(f"subprocess timeout: {args[0]}")
        time.sleep(1)


def separate_audio(video: Path, audio_dir: Path, on_progress: ProgressFn = noop,
                   check: cancel.CheckFn = lambda: False) -> dict[str, Path]:
    """声音素材:抽原声 + Demucs 分离 人声/伴奏(BGM 与次级音效在伴奏轨)。

    幂等(产物在则跳过);Demucs 失败只保留原声,不拖垮采集主链路。
    分离耗时约 0.5x 实时(CPU),走可取消子进程,取消时 1 秒内 SIGTERM。
    返回 {"original"/"vocals"/"bgm": 绝对 Path}（相对化交给调用方）。
    """
    import shutil

    out: dict[str, Path] = {}
    audio_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"

    original = audio_dir / "original.mp3"
    if not original.exists():
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(video),
                        "-vn", "-codec:a", "libmp3lame", "-b:a", "128k", str(original)],
                       check=True, timeout=300)
        on_progress("原声已抽出")
    out["original"] = original

    vocals, bgm = audio_dir / "vocals.mp3", audio_dir / "bgm.mp3"
    if not (vocals.exists() and bgm.exists()):
        sep_dir = audio_dir / "_demucs"
        try:
            on_progress("Demucs 分离人声/伴奏中(约 0.5x 实时)…")
            _run_proc_cancellable(
                [sys.executable, "-m", "demucs.separate", "--two-stems=vocals",
                 "-n", "htdemucs", "--mp3", "-o", str(sep_dir), str(original)],
                check)
            stem = sep_dir / "htdemucs" / original.stem
            (stem / "vocals.mp3").replace(vocals)
            (stem / "no_vocals.mp3").replace(bgm)
            shutil.rmtree(sep_dir, ignore_errors=True)
            on_progress("声音分离完成: 人声 + 伴奏(BGM/音效)")
        except cancel.TaskCancelled:
            shutil.rmtree(sep_dir, ignore_errors=True)   # 半成品清掉,恢复后重跑
            raise
        except Exception as e:  # noqa: BLE001 — 分离失败保留原声
            on_progress(f"声音分离失败(保留原声): {type(e).__name__}")
    if vocals.exists():
        out["vocals"] = vocals
    if bgm.exists():
        out["bgm"] = bgm
    return out
