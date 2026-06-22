"""_resolve_opus 路径解析测试：覆盖 PATH 命中 / 回退 ~/.sclaude / 都缺三种情况。

回归点：launchd worker 不继承 shell PATH，裸调 "opus" 会 FileNotFoundError。
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from ncds_opus_factory.common import opus_cli


def test_resolve_opus_prefers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """PATH 上能查到 opus -> 直接用 which 的结果。"""
    monkeypatch.setattr(opus_cli.shutil, "which", lambda _name: "/usr/local/bin/opus")
    assert opus_cli._resolve_opus() == "/usr/local/bin/opus"


def test_resolve_opus_falls_back_to_sclaude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PATH 查不到(launchd worker 场景) -> 回退到存在的 ~/.sclaude/bin/opus。"""
    fallback = tmp_path / "opus"
    fallback.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(opus_cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(opus_cli, "_SCLAUDE_FALLBACK", fallback)
    assert opus_cli._resolve_opus() == str(fallback)


def test_resolve_opus_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PATH 没有且回退位也不存在 -> 抛清晰 RuntimeError(不再是裸 FileNotFoundError)。"""
    monkeypatch.setattr(opus_cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(opus_cli, "_SCLAUDE_FALLBACK", tmp_path / "nope" / "opus")
    with pytest.raises(RuntimeError, match="opus launcher not found"):
        opus_cli._resolve_opus()


def test_is_opus_available_uses_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opus_cli, "_resolve_opus", lambda: "/x/opus")
    assert opus_cli.is_opus_available() is True

    def missing() -> str:
        raise RuntimeError("missing")

    monkeypatch.setattr(opus_cli, "_resolve_opus", missing)
    assert opus_cli.is_opus_available() is False


def test_call_opus_passes_system_prompt_timeout_env_and_parses_last_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """call_opus 负责统一 launch 参数与 NDJSON result 解析。"""
    captured: dict = {}
    monkeypatch.setattr(opus_cli, "_resolve_opus", lambda: "/usr/bin/opus")

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "noise\n"
                '{"type":"result","is_error":false,"result":" first "}\n'
                '{"type":"result","is_error":false,"result":" final "}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(opus_cli.subprocess, "run", fake_run)
    out = opus_cli.call_opus(
        "user prompt",
        system_prompt="sys prompt",
        model="m1",
        effort="high",
        timeout_seconds=123,
        env={"A": "B"},
    )

    assert out == "final"
    args = captured["args"]
    assert args[:4] == ["/usr/bin/opus", "launch", "--no-resume", "--"]
    assert args[args.index("-p") + 1] == "user prompt"
    assert args[args.index("--model") + 1] == "m1"
    assert args[args.index("--effort") + 1] == "high"
    assert args[args.index("--system-prompt") + 1] == "sys prompt"
    assert captured["kwargs"]["timeout"] == 123
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert captured["kwargs"]["env"] == {"A": "B"}


def test_call_opus_raises_on_claude_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opus_cli, "_resolve_opus", lambda: "/usr/bin/opus")
    monkeypatch.setattr(
        opus_cli.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(
            returncode=0,
            stdout='{"type":"result","is_error":true,"result":"bad"}\n',
            stderr="",
        ),
    )
    with pytest.raises(RuntimeError, match="claude error: bad"):
        opus_cli.call_opus("prompt")


def test_call_opus_raises_on_process_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opus_cli, "_resolve_opus", lambda: "/usr/bin/opus")
    monkeypatch.setattr(
        opus_cli.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=9, stdout="", stderr="boom"),
    )
    with pytest.raises(RuntimeError, match="opus launcher exited 9: boom"):
        opus_cli.call_opus("prompt")
