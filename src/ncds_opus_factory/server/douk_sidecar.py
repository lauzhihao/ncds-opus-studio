"""DouK sidecar process management for nof-worker.

The downloader itself lives in the sibling TikTokDownloader repository.  This
module only makes sure the HTTP sidecar is available before Shenkuo tasks start.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DOUK_PORT = 5556


@dataclass
class DouKSidecarHandle:
    endpoint: str
    process: subprocess.Popen | None = None

    @property
    def spawned(self) -> bool:
        return self.process is not None


def _env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off"}


def _client_host(host: str) -> str:
    return "127.0.0.1" if host in {"", "0.0.0.0", "::"} else host


def _local_endpoint() -> str:
    host = _client_host(os.environ.get("NOF_DOUK_HOST", "127.0.0.1").strip())
    port = int(os.environ.get("NOF_DOUK_PORT", str(DEFAULT_DOUK_PORT)).strip() or DEFAULT_DOUK_PORT)
    return f"http://{host}:{port}"


def _is_local_endpoint(endpoint: str) -> bool:
    try:
        parsed = urlparse(endpoint)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return host in {"", "localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _health_url(endpoint: str) -> str:
    return endpoint.rstrip("/") + "/health"


def _is_healthy(endpoint: str, timeout: float = 2.0) -> bool:
    try:
        resp = requests.get(_health_url(endpoint), timeout=timeout)
        return resp.status_code == 200 and bool((resp.json() or {}).get("ok"))
    except Exception:  # noqa: BLE001
        return False


def _douk_repo() -> Path:
    raw = os.environ.get("NOF_DOUK_REPO", "").strip()
    return Path(raw).expanduser() if raw else REPO_ROOT.parent / "TikTokDownloader"


def _start_cmd(repo: Path) -> list[str]:
    raw = os.environ.get("NOF_DOUK_START_CMD", "").strip()
    if raw:
        return shlex.split(raw)
    venv_python = repo / ".venv" / "bin" / "python"
    if venv_python.exists():
        return [str(venv_python), "download_server.py"]
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "python", "download_server.py"]
    return [sys.executable, "download_server.py"]


def _sidecar_env(endpoint: str) -> dict[str, str]:
    env = os.environ.copy()
    parsed = urlparse(endpoint)
    host = os.environ.get("NOF_DOUK_BIND_HOST", os.environ.get("NOF_DOUK_HOST", "127.0.0.1"))
    port = str(parsed.port or DEFAULT_DOUK_PORT)
    env["DOUK_SIDECAR_HOST"] = host
    env["DOUK_SIDECAR_PORT"] = port
    bridges = {
        "NOF_DOUK_TOKEN": "DOUK_SIDECAR_TOKEN",
        "NOF_DOUK_PROXY": "DOUK_PROXY",
        "NOF_DOUK_TIKTOK_PROXY": "DOUK_TIKTOK_PROXY",
        "NOF_DOUK_DOUYIN_PROXY": "DOUK_DOUYIN_PROXY",
    }
    for src, dst in bridges.items():
        if env.get(src) and not env.get(dst):
            env[dst] = env[src]
    return env


async def _wait_until_healthy(endpoint: str, proc: subprocess.Popen, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        if await asyncio.to_thread(_is_healthy, endpoint, 2.0):
            return True
        await asyncio.sleep(0.5)
    return False


async def ensure_douk_sidecar() -> DouKSidecarHandle | None:
    """Ensure DouK sidecar is reachable for this worker process.

    Returns a handle only when an endpoint is configured or autostarted.  Failure is
    non-fatal: downloads will keep their existing fallback chain.
    """
    if not _env_flag("NOF_DOUK_AUTOSTART", "1"):
        logger.info("[douk-sidecar] autostart disabled")
        return None

    configured = os.environ.get("NOF_DOUK_ENDPOINT", "").strip().rstrip("/")
    autoconfigured = not configured
    endpoint = configured or _local_endpoint()
    if await asyncio.to_thread(_is_healthy, endpoint, 2.0):
        os.environ["NOF_DOUK_ENDPOINT"] = endpoint
        logger.info("[douk-sidecar] ready endpoint=%s", endpoint)
        return DouKSidecarHandle(endpoint=endpoint)

    if configured and not _is_local_endpoint(configured):
        logger.warning("[douk-sidecar] configured endpoint not healthy: %s", configured)
        return DouKSidecarHandle(endpoint=configured)

    repo = _douk_repo()
    entry = repo / "download_server.py"
    if not entry.exists():
        logger.warning("[douk-sidecar] repo entry missing, skip autostart: %s", entry)
        return None

    os.environ["NOF_DOUK_ENDPOINT"] = endpoint
    timeout = float(os.environ.get("NOF_DOUK_START_TIMEOUT", "45") or "45")
    log_path = Path(os.environ.get("NOF_DOUK_LOG", str(REPO_ROOT / "state" / "douk_sidecar.log"))).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("ab")
    cmd = _start_cmd(repo)
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=repo,
            env=_sidecar_env(endpoint),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log_file.close()
        if autoconfigured:
            os.environ.pop("NOF_DOUK_ENDPOINT", None)
        logger.warning("[douk-sidecar] failed to start %s: %s", cmd, exc)
        return None
    log_file.close()

    ok = await _wait_until_healthy(endpoint, proc, timeout)
    if ok:
        logger.info("[douk-sidecar] started pid=%s endpoint=%s log=%s", proc.pid, endpoint, log_path)
        return DouKSidecarHandle(endpoint=endpoint, process=proc)

    rc = proc.poll()
    logger.warning("[douk-sidecar] failed healthcheck endpoint=%s pid=%s rc=%s log=%s", endpoint, proc.pid, rc, log_path)
    await stop_douk_sidecar(DouKSidecarHandle(endpoint=endpoint, process=proc))
    if autoconfigured:
        os.environ.pop("NOF_DOUK_ENDPOINT", None)
    return None


async def stop_douk_sidecar(handle: DouKSidecarHandle | None) -> None:
    if not handle or not handle.process:
        return
    proc = handle.process
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        proc.terminate()
    try:
        await asyncio.to_thread(proc.wait, 10)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        await asyncio.to_thread(proc.wait)
