#!/usr/bin/env python3
"""Douyin video downloader CLI backed by the project TikHub client."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ncds_opus_factory.common import tikhub_client  # noqa: E402

DOWNLOAD_RETRIES = tikhub_client.DOWNLOAD_RETRIES

# Tests patch these module attributes; they point at the single real implementation.
requests = tikhub_client.requests
time = tikhub_client.time


def get_token(token: str | None = None) -> str:
    return tikhub_client.get_token(token)


def extract_modal_id(text: str) -> str | None:
    """Extract aweme_id from modal_id, bare id, share URL, or copied share text."""
    raw = (text or "").strip()
    modal_match = re.search(r"modal_id[=:](\d+)", raw)
    if modal_match:
        return modal_match.group(1)
    if re.fullmatch(r"\d{16,}", raw):
        return raw
    return tikhub_client.resolve_aweme_id(raw)


def get_video_url_by_modal_id(modal_id: str, token: str) -> str | None:
    return tikhub_client.fetch_video_url(modal_id, token)


def get_video_info(user_input: str, token: str | None = None) -> dict[str, str]:
    token = get_token(token)
    modal_id = extract_modal_id(user_input)
    if not modal_id:
        raise ValueError(f"无法从输入中提取modal_id: {user_input}")

    video_url = get_video_url_by_modal_id(modal_id, token)
    if not video_url:
        raise ValueError(f"无法获取视频链接，请检查modal_id是否正确: {modal_id}")

    return {
        "modal_id": modal_id,
        "video_url": video_url,
    }


def download_video(url: str, output_path: str | Path | None = None, max_retries: int = DOWNLOAD_RETRIES) -> str:
    if output_path is None:
        output_path = f"douyin_{hash(url) % 100000}.mp4"
    return tikhub_client.download_video(url, output_path, max_retries=max_retries)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            """
抖音视频下载器
=============

用法:
  python douyin_download.py "抖音链接或modal_id" [--download]
  加 --download 参数可直接下载视频，否则只返回视频下载url

示例:
  python douyin_download.py "https://www.douyin.com/jingxuan?modal_id=7597329042169220398"
  python douyin_download.py "7597329042169220398" --download
"""
        )
        sys.exit(1)

    user_input = sys.argv[1]
    download = "--download" in sys.argv

    try:
        info = get_video_info(user_input)
        print(f"modal_id: {info['modal_id']}")
        print(f"视频地址: {info['video_url']}")

        if download:
            print("\n[DL] 下载中...")
            path = download_video(info["video_url"])
            print(f"[OK] 下载完成: {path}")
    except Exception as exc:
        print(f"\n[FAIL] 错误: {exc}")
        sys.exit(1)
