"""下载能力：yt-dlp 匿名优先 + TikHub 兜底 + 封面。

TikHub 是按调用量计费的商业 API；拿无水印播放地址(``fetch_video_url``)每次都算钱。
而抖音视频可匿名下载(全程无登录态)：yt-dlp 内置抖音 extractor，喂
``douyin.com/video/{aweme_id}`` 就能自解析无水印 CDN 并直接拉 mp4。

所以 ``fetch_and_download``：**yt-dlp 匿名优先(免费)，失败再回退 TikHub**(fetch_video_url +
download_video)。yt-dlp 没装 / 抖音改版 / 网络抖动都安全降级到 TikHub，不致命。

``fetch_video_url`` / ``download_video`` / ``download_cover`` 真身仍在 ``common/tikhub_client``，
这里 re-export，让 capabilities 成为「采集底层能力」的统一 import 面。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ncds_opus_core.common import cancel
from ncds_opus_factory.common import tikhub_client
from ncds_opus_factory.common.tikhub_client import (
    download_cover,
    download_video,
    fetch_video_url,
)

from ._base import ProgressFn, noop

__all__ = [
    "fetch_video_url",
    "download_video",
    "download_cover",
    "fetch_and_download",
]

# yt-dlp 单条下载超时(秒)：抖音单视频通常几十秒内，给足余量防卡死。
_YTDLP_TIMEOUT = 600


def _ytdlp_cmd() -> list[str] | None:
    """yt-dlp 调用命令前缀。优先级：

    1. env NOF_YT_DLP 指定的外部二进制；
    2. PATH 里的 yt-dlp；
    3. 当前解释器的 ``python -m yt_dlp`` —— yt-dlp 是本项目依赖(装在 venv)，但 .venv/bin
       常不在 PATH，``shutil.which`` 找不到；走模块调用最稳，绑定当前 venv、不依赖 PATH。

    都没有返回 None(交给 TikHub 兜底)。
    """
    env_bin = os.environ.get("NOF_YT_DLP")
    if env_bin:
        return [env_bin]
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    return None


def _ytdlp_download(
    aweme_id: str, out: Path, on_progress: ProgressFn = noop,
    check: cancel.CheckFn = lambda: False,
) -> str | None:
    """yt-dlp 匿名下载抖音作品(无 TikHub、无登录态)。

    喂 ``douyin.com/video/{aweme_id}`` 让 yt-dlp 自己跟随短链/解析无水印 CDN。
    成功返回落盘路径；yt-dlp 不可用 / rc!=0 / 没出片 -> 返回 None(交给 TikHub 兜底)。
    取消：子进程按秒轮询 check()，取消则 SIGTERM(线程杀不掉，子进程随便杀)。
    """
    prefix = _ytdlp_cmd()
    if not prefix:
        return None
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 输出到 <stem>.ytdl.<ext>：下完 glob 找实际文件再原子替换到 out，不假设 yt-dlp 落的扩展名。
    tmpl = str(out.parent / f"{out.stem}.ytdl.%(ext)s")
    url = f"https://www.douyin.com/video/{aweme_id}"
    cmd = [*prefix, "-o", tmpl, "--no-warnings", "--no-playlist", url]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while True:
        rc = proc.poll()
        if rc is not None:
            break
        if check():
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise cancel.TaskCancelled("cancelled during yt-dlp download")
        if time.time() - t0 > _YTDLP_TIMEOUT:
            proc.kill()
            return None  # 超时当 yt-dlp 失败，回退 TikHub
        time.sleep(1)

    produced = sorted(out.parent.glob(f"{out.stem}.ytdl.*"))
    if rc != 0 or not produced:
        for extra in produced:  # 失败残片清掉，不污染后续 glob/缓存判定
            extra.unlink(missing_ok=True)
        return None
    produced[0].replace(out)
    for extra in out.parent.glob(f"{out.stem}.ytdl.*"):  # 多余分段(理论上没有)清掉
        extra.unlink(missing_ok=True)
    on_progress(f"[{aweme_id}] yt-dlp 匿名下载完成(未走 TikHub)")
    return str(out)


def fetch_and_download(
    aweme_id: str, out: Path, on_progress: ProgressFn = noop,
    check: cancel.CheckFn = lambda: False, token: str | None = None,
) -> str:
    """下载抖音作品视频：yt-dlp 匿名优先(省 TikHub 付费调用)，失败回退 TikHub。

    沈括 ``branch_download`` 走这里。两条路都拿不到视频才抛 RuntimeError(由调用方降级处理)。
    """
    out = Path(out)
    cancel.checkpoint(check)
    try:
        path = _ytdlp_download(aweme_id, out, on_progress, check)
        if path:
            return path
    except cancel.TaskCancelled:
        raise
    except Exception as e:  # noqa: BLE001 — yt-dlp 任何异常都不致命，回退 TikHub
        on_progress(f"[{aweme_id}] yt-dlp 下载异常，回退 TikHub: {type(e).__name__}: {e}")

    # TikHub 兜底：经模块属性调用(非直接绑定函数对象)，便于测试 monkeypatch tikhub_client.*
    on_progress(f"[{aweme_id}] yt-dlp 未出片，走 TikHub 兜底")
    url = tikhub_client.fetch_video_url(aweme_id, token)
    if not url:
        raise RuntimeError(f"TikHub 未返回播放地址: {aweme_id}")
    return tikhub_client.download_video(url, out)
