"""ASR subprocess and polish helpers shared by pipeline facade paths."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from ncds_opus_core.common import cancel as _cancel
from ncds_opus_factory.common.opus_cli import DEFAULT_OPUS_MODEL, call_opus
from ncds_opus_factory.server.pipeline_media_helpers import _terminate_proc_group

DEFAULT_OPUS_MODEL_ID = DEFAULT_OPUS_MODEL
ASR_PROC_TIMEOUT_SEC = int(os.getenv("NOF_ASR_PROC_TIMEOUT", "3600"))
ASR_POLISH_TIMEOUT_SEC = int(os.getenv("NOF_ASR_POLISH_TIMEOUT", "600"))


def _asr_stage_label(line: str) -> str | None:
    """从 video_pipeline.py 的 stdout 行识别当前阶段，给作品级状态行做实时 stage 文案。"""
    s = line or ""
    if not s:
        return None
    if re.search(r"\[OK\]\s*转写|\u2705\s*转写|转写完成|whisper|转写", s, re.IGNORECASE):
        return "语音转写"
    if re.search(r"提取音频|extract.*audio|ffmpeg.*audio", s, re.IGNORECASE):
        return "提取音频"
    if re.search(r"下载|download|TikHub|yt-dlp|复用.*缓存", s, re.IGNORECASE):
        return "下载视频"
    return None


def _run_video_pipeline(
    *,
    pipeline_script: Path,
    url: str,
    output_dir: Path,
    on_line: Callable[[str], None],
) -> None:
    """同步调 video_pipeline.py，行级转发 stdout 给 on_line。在 to_thread 里跑。"""
    proc = subprocess.Popen(
        [sys.executable, str(pipeline_script), "-o", str(output_dir), url],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        start_new_session=True,
    )
    assert proc.stdout is not None
    tail: list[str] = []
    checker = _cancel.current()
    for line in iter(proc.stdout.readline, ""):
        if checker():
            _terminate_proc_group(proc)
            raise _cancel.TaskCancelled("cancelled during video_pipeline subprocess")
        s = line.rstrip("\n")
        if s:
            on_line(s)
            tail.append(s)
            if len(tail) > 20:
                tail.pop(0)
    proc.stdout.close()
    code = proc.wait(timeout=ASR_PROC_TIMEOUT_SEC)
    if code != 0:
        snippet = "\n".join(tail).strip()
        raise RuntimeError(f"video_pipeline.py exited {code}\n--- last output ---\n{snippet}")


def _polish_transcript_with_opus(
    *,
    transcript_path: Path,
    output_path: Path,
    title_hint: str = "",
) -> bool:
    """调本机 opus launcher（Claude Opus 4.8）把语音转写原稿整理成 markdown 文章。"""
    text = transcript_path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"transcript empty: {transcript_path}")

    src_key = hashlib.sha256((text + "\x00" + (title_hint or "")).encode("utf-8")).hexdigest()
    sha_path = output_path.with_name(output_path.name + ".src-sha256")
    if (output_path.is_file()
            and output_path.read_text(encoding="utf-8").strip()
            and sha_path.is_file()
            and sha_path.read_text(encoding="utf-8").strip() == src_key):
        return False

    hint = f"（参考标题：{title_hint}）" if title_hint else ""
    prompt = (
        "下面是一段语音转写得到的中文原稿，请把它整理成易读的中文文章" + hint + "。\n"
        "整理要求：\n"
        "1. 修正错别字、口误、明显的同音字错误；\n"
        "2. 补全 / 修正标点符号；\n"
        "3. 合理分段，每段表达一个相对完整的意思；\n"
        "4. 若内容较长，可在合适位置加 2-4 个二级标题（## 标题）；\n"
        "5. 保留原意，不要增删事实，不要添加你自己的总结或评论；\n"
        "6. 输出 Markdown 格式，不要加代码块包裹，不要加任何前言或后记。\n\n"
        "【原稿】\n" + text
    )

    final_text = call_opus(
        prompt,
        model=DEFAULT_OPUS_MODEL_ID,
        timeout_seconds=ASR_POLISH_TIMEOUT_SEC,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_text + "\n", encoding="utf-8")
    sha_path.write_text(src_key, encoding="utf-8")
    return True
