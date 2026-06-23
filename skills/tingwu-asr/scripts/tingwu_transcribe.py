#!/usr/bin/env python3
"""Legacy CLI name kept as a wrapper around the project TingWu adapter."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ncds_opus_factory.common.capabilities import tingwu  # noqa: E402


def get_api_key() -> str:
    try:
        return tingwu.resolve_api_key()
    except tingwu.TingwuUnavailableError:
        raise ValueError("缺少 DASHSCOPE_API_KEY：请设环境变量，或写进仓库根 .env（模板见 .env.example）")


def transcribe_file(file_path: str | Path) -> tingwu.TingwuTranscript:
    return tingwu.transcribe_file(file_path)


def extract_text(result) -> str:
    if isinstance(result, tingwu.TingwuTranscript):
        return result.text
    if isinstance(result, dict):
        return tingwu.extract_text_from_task(result)
    return getattr(result, "text", "") or ""


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 tingwu_transcribe.py <视频/音频文件路径>")
        sys.exit(1)

    file_path = Path(sys.argv[1]).expanduser()
    if not file_path.exists():
        print(f"[FAIL] 文件不存在: {file_path}")
        sys.exit(1)

    try:
        result = transcribe_file(file_path)
    except Exception as exc:
        print(f"[FAIL] 转写失败: {exc}")
        sys.exit(1)

    output_file = os.path.splitext(str(file_path))[0] + ".txt"
    plain_text = extract_text(result)
    if plain_text:
        Path(output_file).write_text(plain_text, encoding="utf-8")
        print(f"[OK] 纯文本已保存至: {output_file}")
        print("\n--- 转写文本预览（前500字）---")
        print(plain_text[:500])
        print("-------------------\n")
    else:
        raw_file = Path(output_file)
        raw_file.write_text(json.dumps(result.raw_response, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 原始结果已保存至: {raw_file}")
