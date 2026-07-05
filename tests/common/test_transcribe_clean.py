from __future__ import annotations

import importlib

transcribe = importlib.import_module("ncds_opus_factory.common.capabilities.transcribe")


def test_clean_transcript_rejects_translation(monkeypatch):
    raw = (
        "Gouging out the man's eyes with her bare fingers, the woman who had "
        "once been enjoying a peaceful retirement had become a demon once again."
    )
    progress: list[str] = []

    monkeypatch.setattr(
        transcribe,
        "_clean_with_qwen",
        lambda _raw, _on_progress: "用赤手挖出男人双眼的女子，如今再度化身为恶魔。",
    )

    assert transcribe.clean_transcript(raw, progress.append) is None
    assert any("改变原始语言" in item for item in progress)


def test_clean_local_preserves_english_word_spaces():
    assert transcribe._clean_local("hello   world", lambda _msg: None) == "hello world"
