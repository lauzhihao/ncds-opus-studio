"""_resolve_opus 路径解析测试：覆盖 PATH 命中 / 回退 ~/.sclaude / 都缺三种情况。

回归点：launchd worker 不继承 shell PATH，裸调 "opus" 会 FileNotFoundError。
"""

from __future__ import annotations

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
