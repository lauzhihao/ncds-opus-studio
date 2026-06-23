from __future__ import annotations

import pytest

from ncds_opus_factory.server.pipeline_lines_tasks import structure_lines_json_with_fallback


def test_lines_fallback_tries_next_model_after_error() -> None:
    called: list[str] = []
    progress: list[str] = []

    def fail_agy(_user: str, _system: str, _model: str) -> str:
        called.append("agy")
        raise RuntimeError("agy down")

    def ok_ds(_user: str, _system: str, _model: str) -> str:
        called.append("ds")
        return '{"beats":[{"zh":"第一句","en":"","chapter":1}]}'

    parsed = structure_lines_json_with_fallback(
        "user",
        "system",
        progress.append,
        callers={
            "agy": fail_agy,
            "ds": ok_ds,
        },
    )

    assert called == ["agy", "ds"]
    assert parsed["beats"][0]["zh"] == "第一句"
    assert any("正在切换备用通道" in item for item in progress)


def test_lines_fallback_uses_friendly_error_after_all_fail() -> None:
    def fail(_user: str, _system: str, _model: str) -> str:
        raise RuntimeError("raw launcher stack")

    with pytest.raises(RuntimeError) as excinfo:
        structure_lines_json_with_fallback(
            "user",
            "system",
            lambda _text: None,
            callers={
                "agy": fail,
                "ds": fail,
                "scodex": fail,
                "opus": fail,
            },
        )

    msg = str(excinfo.value)
    assert "视觉方案准备暂时失败" in msg
    assert "raw launcher stack" not in msg
