"""DouK sidecar autostart tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from ncds_opus_factory.server import douk_sidecar


class _FakePopen:
    def __init__(self):
        self.pid = 12345
        self.args = None
        self.kwargs = None

    def poll(self):
        return None


def test_ensure_douk_sidecar_disabled(monkeypatch):
    monkeypatch.setenv("NOF_DOUK_AUTOSTART", "0")

    async def run():
        handle = await douk_sidecar.ensure_douk_sidecar()
        assert handle is None

    asyncio.run(run())


def test_ensure_douk_sidecar_reuses_healthy_endpoint(monkeypatch):
    monkeypatch.setenv("NOF_DOUK_ENDPOINT", "http://127.0.0.1:5556")
    monkeypatch.setattr(douk_sidecar, "_is_healthy", lambda endpoint, timeout=2.0: True)

    async def run():
        handle = await douk_sidecar.ensure_douk_sidecar()
        assert handle is not None
        assert handle.endpoint == "http://127.0.0.1:5556"
        assert not handle.spawned

    asyncio.run(run())


def test_ensure_douk_sidecar_skips_remote_unhealthy_endpoint(monkeypatch):
    monkeypatch.setenv("NOF_DOUK_ENDPOINT", "http://douk.example.internal:5556")
    monkeypatch.setattr(douk_sidecar, "_is_healthy", lambda endpoint, timeout=2.0: False)

    async def run():
        handle = await douk_sidecar.ensure_douk_sidecar()
        assert handle is not None
        assert handle.endpoint == "http://douk.example.internal:5556"
        assert not handle.spawned

    asyncio.run(run())


def test_ensure_douk_sidecar_starts_local_repo(tmp_path: Path, monkeypatch):
    repo = tmp_path / "TikTokDownloader"
    repo.mkdir()
    (repo / "download_server.py").write_text("print('sidecar')\n", encoding="utf-8")
    fake_proc = _FakePopen()
    seen = {}

    def fake_popen(cmd, **kwargs):
        fake_proc.args = cmd
        fake_proc.kwargs = kwargs
        seen["env"] = kwargs["env"]
        return fake_proc

    monkeypatch.delenv("NOF_DOUK_ENDPOINT", raising=False)
    monkeypatch.setenv("NOF_DOUK_REPO", str(repo))
    monkeypatch.setenv("NOF_DOUK_PORT", "5566")
    monkeypatch.setenv("NOF_DOUK_TOKEN", "secret")
    monkeypatch.setenv("NOF_DOUK_TIKTOK_PROXY", "http://127.0.0.1:10808")
    monkeypatch.setenv("NOF_DOUK_LOG", str(tmp_path / "douk.log"))
    monkeypatch.setattr(douk_sidecar, "_is_healthy", lambda endpoint, timeout=2.0: False)
    monkeypatch.setattr(douk_sidecar, "_start_cmd", lambda repo: ["python", "download_server.py"])
    monkeypatch.setattr(douk_sidecar.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(douk_sidecar, "_wait_until_healthy", AsyncMock(return_value=True))

    async def run():
        handle = await douk_sidecar.ensure_douk_sidecar()
        assert handle is not None
        assert handle.endpoint == "http://127.0.0.1:5566"
        assert handle.process is fake_proc
        assert fake_proc.args == ["python", "download_server.py"]
        assert fake_proc.kwargs["cwd"] == repo
        assert seen["env"]["DOUK_SIDECAR_PORT"] == "5566"
        assert seen["env"]["DOUK_SIDECAR_TOKEN"] == "secret"
        assert seen["env"]["DOUK_TIKTOK_PROXY"] == "http://127.0.0.1:10808"
        assert douk_sidecar.os.environ["NOF_DOUK_ENDPOINT"] == "http://127.0.0.1:5566"

    asyncio.run(run())
