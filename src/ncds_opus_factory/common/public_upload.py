"""把本地图片上传到公网（vooice.tech），获得可被 DashScope HappyHorse 拉取的 URL。

DashScope 视频生成的 i2v / r2v 需要参考图是公网可访问的 URL，本模块负责通过 ssh+scp
把图片落到 vooice.tech 的静态目录，再返回 https URL。

不接入飞书 API。如果参考图来自飞书消息，请先用 common.lark_cli.download_message_image
下载到本地，再调本模块上传。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PUBLIC_IMAGE_BASE_URL = os.getenv("NOF_IMAGE_PUBLIC_BASE_URL", "https://vooice.tech/feishu-images").rstrip("/")
PUBLIC_IMAGE_SSH_TARGET = os.getenv("NOF_IMAGE_UPLOAD_SSH_TARGET", "root@gpt.vooice.tech")
PUBLIC_IMAGE_REMOTE_DIR = os.getenv("NOF_IMAGE_UPLOAD_REMOTE_DIR", "/var/www/vooice.tech/feishu-images")
UPLOAD_TIMEOUT = int(os.getenv("NOF_IMAGE_UPLOAD_TIMEOUT", "600"))
DELETE_DELAY_SECONDS = int(os.getenv("NOF_IMAGE_DELETE_DELAY_SECONDS", "180"))


def _detect(name: str) -> list[str]:
    bin_path = shutil.which(name)
    if not bin_path:
        raise RuntimeError(f"未找到 {name}")
    return [bin_path]


def _build_target(image_path: Path, tag: str | None) -> tuple[str, str]:
    suffix = image_path.suffix.lower() or ".bin"
    safe_tag = re.sub(r"[^A-Za-z0-9._-]+", "_", (tag or "").strip()) or "img"
    date_segment = time.strftime("%Y%m%d")
    file_name = f"{safe_tag}_{uuid.uuid4().hex[:12]}{suffix}"
    remote_path = f"{PUBLIC_IMAGE_REMOTE_DIR}/{date_segment}/{file_name}"
    public_url = f"{PUBLIC_IMAGE_BASE_URL}/{urllib.parse.quote(date_segment)}/{urllib.parse.quote(file_name)}"
    return remote_path, public_url


def _ensure_reachable(url: str) -> None:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"图片公网地址不可访问：HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"图片公网地址不可访问：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"图片公网地址不可访问：{exc.reason}") from exc


def upload(image_path: Path, tag: str | None = None) -> tuple[str, str]:
    """返回 (public_url, remote_path)。"""
    if not image_path.exists():
        raise FileNotFoundError(image_path)
    remote_path, public_url = _build_target(image_path, tag)
    remote_dir = str(Path(remote_path).parent)

    ssh = _detect("ssh")
    scp = _detect("scp")

    mkdir_result = subprocess.run(
        [*ssh, PUBLIC_IMAGE_SSH_TARGET, "mkdir", "-p", remote_dir],
        capture_output=True, text=True, timeout=UPLOAD_TIMEOUT,
    )
    if mkdir_result.returncode != 0:
        raise RuntimeError((mkdir_result.stderr or mkdir_result.stdout or "创建图片上传目录失败").strip())

    upload_result = subprocess.run(
        [*scp, str(image_path), f"{PUBLIC_IMAGE_SSH_TARGET}:{remote_path}"],
        capture_output=True, text=True, timeout=UPLOAD_TIMEOUT,
    )
    if upload_result.returncode != 0:
        raise RuntimeError((upload_result.stderr or upload_result.stdout or "上传参考图失败").strip())

    chmod_result = subprocess.run(
        [*ssh, PUBLIC_IMAGE_SSH_TARGET, "chmod", "644", remote_path],
        capture_output=True, text=True, timeout=UPLOAD_TIMEOUT,
    )
    if chmod_result.returncode != 0:
        raise RuntimeError((chmod_result.stderr or chmod_result.stdout or "设置参考图权限失败").strip())

    _ensure_reachable(public_url)
    return public_url, remote_path


def schedule_remote_delete(remote_path: str, delay_seconds: int = DELETE_DELAY_SECONDS) -> None:
    if not remote_path.strip():
        return
    remote_dir = str(Path(remote_path).parent)
    remote_script = (
        f"sleep {max(delay_seconds, 0)}; "
        f"rm -f -- {shlex.quote(remote_path)}; "
        f"rmdir --ignore-fail-on-non-empty {shlex.quote(remote_dir)} >/dev/null 2>&1 || true"
    )
    try:
        subprocess.Popen(
            [*_detect("ssh"), PUBLIC_IMAGE_SSH_TARGET, "sh", "-lc", remote_script],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass
