"""下载能力：DouK sidecar 优先 + yt-dlp 匿名 + TikHub 兜底 + 封面。

TikHub 是按调用量计费的商业 API；拿无水印播放地址(``fetch_video_url``)每次都算钱。
优先走自部署 DouK sidecar(``NOF_DOUK_ENDPOINT``)，调用方只依赖 HTTP，便于独立机器部署；
sidecar 不可用时再用 yt-dlp 匿名下载。

所以 ``fetch_and_download``：**DouK HTTP 优先，yt-dlp 第二，抖音/TikTok 最后回退 TikHub**。
DouK/yt-dlp 没装 / 平台改版 / 网络抖动都安全降级，不致命。YouTube 当前没有项目内 TikHub 兜底，
必须由 DouK 或 yt-dlp 成功下载。

``fetch_video_url`` / ``download_video`` / ``download_cover`` 真身仍在 ``common/tikhub_client``，
这里 re-export，让 capabilities 成为「采集底层能力」的统一 import 面。
"""

from __future__ import annotations

import importlib.util
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from ncds_opus_core.common import cancel
from ncds_opus_factory.common import tikhub_client
from ncds_opus_factory.common.tikhub_client import (
    download_cover,
    download_video,
    fetch_video_url,
)

from ._base import ProgressFn, noop, read_repo_env_value

__all__ = [
    "fetch_video_url",
    "download_video",
    "download_cover",
    "fetch_and_download",
]

# yt-dlp 单条下载超时(秒)：抖音单视频通常几十秒内，给足余量防卡死。
_YTDLP_TIMEOUT = 600

# DouK sidecar 负责真实抓取，ncds 只等待它生成 artifact 并拉回本地。
_DOUK_TIMEOUT = 900


def _repo_env(key: str) -> str:
    return os.environ.get(key) or read_repo_env_value(key) or ""


def _douk_endpoint() -> str:
    return _repo_env("NOF_DOUK_ENDPOINT").strip().rstrip("/")


def _douk_timeout() -> float:
    raw = _repo_env("NOF_DOUK_TIMEOUT").strip()
    if not raw:
        return _DOUK_TIMEOUT
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DOUK_TIMEOUT


def _douk_headers() -> dict[str, str]:
    token = _repo_env("NOF_DOUK_TOKEN").strip() or _repo_env("DOUK_SIDECAR_TOKEN").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _douk_platform_value(platform: str, suffix: str) -> str:
    key = f"NOF_DOUK_{platform.upper()}_{suffix}"
    return _repo_env(key).strip() or _repo_env(f"NOF_DOUK_{suffix}").strip()


def _douk_file_url(endpoint: str, file_url: str) -> str:
    if file_url.startswith(("http://", "https://")):
        return file_url
    return urljoin(endpoint.rstrip("/") + "/", file_url.lstrip("/"))


def _douk_http_download(
    aweme_id: str, out: Path, on_progress: ProgressFn = noop,
    check: cancel.CheckFn = lambda: False,
    platform: str = "douyin", source_url: str | None = None,
) -> str | None:
    """通过独立 DouK sidecar 下载单作品。

    只有配置 ``NOF_DOUK_ENDPOINT`` 时启用。失败返回 None，让调用方继续走 yt-dlp/TikHub。
    """
    endpoint = _douk_endpoint()
    if not endpoint or platform not in {"douyin", "tiktok"}:
        return None

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    timeout = _douk_timeout()
    payload = {
        "platform": platform,
        "video_id": aweme_id,
        "source_url": source_url or "",
        "url": source_url or _ytdlp_url(aweme_id, platform=platform, source_url=source_url),
    }
    for key, value in {
        "proxy": _douk_platform_value(platform, "PROXY"),
        "cookie": _douk_platform_value(platform, "COOKIE"),
        "device_id": _douk_platform_value(platform, "DEVICE_ID") if platform == "tiktok" else "",
    }.items():
        if value:
            payload[key] = value

    tmp = out.with_name(f"{out.stem}.douk.part")
    tmp.unlink(missing_ok=True)
    headers = _douk_headers()
    cancel.checkpoint(check)
    on_progress(f"[{platform}:{aweme_id}] 调用 DouK sidecar 下载")
    try:
        response = requests.post(
            f"{endpoint}/v1/download",
            json=payload,
            headers=headers,
            timeout=(10, timeout),
        )
        if response.status_code >= 400:
            on_progress(
                f"[{platform}:{aweme_id}] DouK sidecar 返回 {response.status_code}，回退 yt-dlp"
            )
            return None
        data = response.json()
        if not data.get("ok") or not data.get("file_url"):
            on_progress(f"[{platform}:{aweme_id}] DouK sidecar 未返回 artifact，回退 yt-dlp")
            return None

        artifact_url = _douk_file_url(endpoint, str(data["file_url"]))
        expected_sha = str(data.get("sha256") or "")
        digest = hashlib.sha256()
        cancel.checkpoint(check)
        with requests.get(
            artifact_url,
            headers=headers,
            stream=True,
            timeout=(10, timeout),
        ) as artifact:
            if artifact.status_code >= 400:
                on_progress(
                    f"[{platform}:{aweme_id}] DouK artifact 返回 {artifact.status_code}，回退 yt-dlp"
                )
                return None
            with tmp.open("wb") as f:
                for chunk in artifact.iter_content(chunk_size=1024 * 1024):
                    cancel.checkpoint(check)
                    if not chunk:
                        continue
                    digest.update(chunk)
                    f.write(chunk)
        if not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            on_progress(f"[{platform}:{aweme_id}] DouK artifact 为空，回退 yt-dlp")
            return None
        if expected_sha and digest.hexdigest() != expected_sha:
            tmp.unlink(missing_ok=True)
            on_progress(f"[{platform}:{aweme_id}] DouK artifact 校验失败，回退 yt-dlp")
            return None
        tmp.replace(out)
        on_progress(f"[{platform}:{aweme_id}] DouK sidecar 下载完成")
        return str(out)
    except cancel.TaskCancelled:
        tmp.unlink(missing_ok=True)
        raise
    except (requests.RequestException, ValueError) as e:
        tmp.unlink(missing_ok=True)
        on_progress(f"[{platform}:{aweme_id}] DouK sidecar 下载异常，回退 yt-dlp: {type(e).__name__}: {e}")
        return None


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


def _ytdlp_url(video_id: str, platform: str = "douyin", source_url: str | None = None) -> str:
    if source_url and source_url.startswith(("http://", "https://")):
        return source_url
    if platform == "youtube":
        return f"https://www.youtube.com/watch?v={video_id}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@_/video/{video_id}"
    return f"https://www.douyin.com/video/{video_id}"


def _ytdlp_download(
    aweme_id: str, out: Path, on_progress: ProgressFn = noop,
    check: cancel.CheckFn = lambda: False,
    platform: str = "douyin", source_url: str | None = None,
) -> str | None:
    """yt-dlp 匿名下载单作品(无 TikHub、无登录态)。

    抖音默认喂 ``douyin.com/video/{aweme_id}``；TikTok/YouTube 优先使用原始分享链接。
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
    url = _ytdlp_url(aweme_id, platform=platform, source_url=source_url)
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
    on_progress(f"[{platform}:{aweme_id}] yt-dlp 匿名下载完成(未走 TikHub)")
    return str(out)


def fetch_and_download(
    aweme_id: str, out: Path, on_progress: ProgressFn = noop,
    check: cancel.CheckFn = lambda: False, token: str | None = None,
    platform: str = "douyin", source_url: str | None = None,
) -> str:
    """下载作品视频：DouK sidecar 优先；yt-dlp 次之；抖音/TikTok 最后回退 TikHub。

    沈括 ``branch_download`` 走这里。所有下载链路都拿不到视频才抛 RuntimeError(由调用方降级处理)。
    """
    out = Path(out)
    cancel.checkpoint(check)
    try:
        path = _douk_http_download(
            aweme_id, out, on_progress, check, platform=platform, source_url=source_url,
        )
        if path:
            return path
    except cancel.TaskCancelled:
        raise
    except Exception as e:  # noqa: BLE001 — sidecar 是外部服务，失败不影响后续兜底链路
        on_progress(f"[{platform}:{aweme_id}] DouK sidecar 异常，回退 yt-dlp: {type(e).__name__}: {e}")

    try:
        path = _ytdlp_download(
            aweme_id, out, on_progress, check, platform=platform, source_url=source_url,
        )
        if path:
            return path
    except cancel.TaskCancelled:
        raise
    except Exception as e:  # noqa: BLE001 — yt-dlp 任何异常都不致命，回退 TikHub
        if platform != "douyin":
            raise RuntimeError(f"yt-dlp 下载 {platform} 作品失败: {e}") from e
        on_progress(f"[{aweme_id}] yt-dlp 下载异常，回退 TikHub: {type(e).__name__}: {e}")

    if platform not in {"douyin", "tiktok"}:
        raise RuntimeError(f"DouK/yt-dlp 未能下载 {platform} 作品，当前 TikHub 客户端未配置该平台兜底")

    # TikHub 兜底：经模块属性调用(非直接绑定函数对象)，便于测试 monkeypatch tikhub_client.*
    on_progress(f"[{aweme_id}] DouK/yt-dlp 未出片，走 TikHub 兜底")
    url = (
        tikhub_client.fetch_tiktok_video_url(aweme_id, source_url=source_url, token=token)
        if platform == "tiktok"
        else tikhub_client.fetch_video_url(aweme_id, token)
    )
    if not url:
        raise RuntimeError(f"TikHub 未返回 {platform} 播放地址: {aweme_id}")
    return tikhub_client.download_video(url, out)
