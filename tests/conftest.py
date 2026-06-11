"""测试全局隔离。

NOF_STATE_DIR 自动指到 tmp：rubric_store/round_store 等 common 层按 env 解析
默认目录,不隔离的话,开发机上 P4 复盘产出的真实 state/wolong/rubric 会让单测
意外激活预筛(真调 scodex)。需要自定 state 的测试照常用 monkeypatch 覆盖。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "_isolated_state" / "tasks"))
    monkeypatch.delenv("NOF_LABELS_DIR", raising=False)
    monkeypatch.delenv("NOF_RETRO_FAKE_LLM", raising=False)
    monkeypatch.delenv("NOF_PRESCREEN_FAKE_JUDGE", raising=False)
