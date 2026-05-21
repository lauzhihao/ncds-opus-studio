"""lark-cli 子进程封装。

本项目不直接调用任何飞书 API。所有需要从飞书读 / 写的动作（下载图片附件、查询群、发消息等）
都通过 spawn `lark-cli` 完成。本模块只放最常用的 helper。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

DEFAULT_TIMEOUT = int(os.getenv("NOF_LARK_CLI_TIMEOUT", "120"))


def detect_lark_cli() -> list[str]:
    """优先用本地 lark-cli，否则 fallback npx。"""
    if shutil.which("lark-cli"):
        return ["lark-cli"]
    return ["npx", "-y", "@larksuite/cli@1.0.17"]


def run_lark_cli(args: list[str], cwd: Path | None = None, timeout: int = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    cmd = [*detect_lark_cli(), *args]
    env = os.environ.copy()
    env.setdefault("LARK_CLI_NO_PROXY", "1")
    env.setdefault("NO_PROXY", "localhost,127.0.0.1,.local,.feishu.cn,.larksuite.com,.larksuite.cn")
    env.setdefault("no_proxy", env["NO_PROXY"])
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def download_message_image(message_id: str, image_key: str, target_dir: Path, as_identity: str = "bot") -> Path:
    """通过 lark-cli 下载消息里的图片资源，返回落地文件路径。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    result = run_lark_cli(
        [
            "im", "+messages-resources-download",
            "--as", as_identity,
            "--message-id", message_id,
            "--file-key", image_key,
            "--type", "image",
            "--output", "reference",
        ],
        cwd=target_dir,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "下载飞书图片失败").strip())
    files = [p for p in sorted(target_dir.iterdir()) if p.is_file()]
    if not files:
        raise RuntimeError("飞书图片已下载，但本地未找到文件。")
    return files[0]
