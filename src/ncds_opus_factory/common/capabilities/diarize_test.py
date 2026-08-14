from __future__ import annotations

from pathlib import Path

import pytest

from ncds_opus_factory.common.capabilities import diarize


def test_normalize_sentences_maps_funasr_payload() -> None:
    payload = [
        {"text": "你好啊。", "start": 0, "end": 1200, "spk": 0},
        {"text": "哈喽。", "start": "1300", "end": 2500.6, "spk": 1},
        "garbage",
        {"text": "  ", "start": 2600, "end": 2700, "spk": 0},
        {"text": "再见。", "start": -5, "end": 3000},
    ]
    sentences = diarize.normalize_sentences(payload)
    assert [s.text for s in sentences] == ["你好啊。", "哈喽。", "再见。"]
    assert [s.speaker for s in sentences] == [0, 1, 0]
    assert sentences[1].start_ms == 1300
    assert sentences[1].end_ms == 2501
    assert sentences[2].start_ms == 0


def test_normalize_sentences_rejects_non_list() -> None:
    assert diarize.normalize_sentences(None) == []
    assert diarize.normalize_sentences({"text": "x"}) == []


def test_speaker_label_maps_cluster_ids() -> None:
    assert diarize.speaker_label(0) == "A"
    assert diarize.speaker_label(1) == "B"
    assert diarize.speaker_label(2) == "C"
    assert diarize.speaker_label(25) == "Z"
    assert diarize.speaker_label(26) == "S26"
    assert diarize.speaker_label(-1) == "S-1"


def test_format_dialogue_merges_consecutive_same_speaker() -> None:
    sentences = [
        diarize.DiarizedSentence(text="你今天怎么又迟到了?", start_ms=0, end_ms=1500, speaker=0),
        diarize.DiarizedSentence(text="堵车堵得离谱。", start_ms=1600, end_ms=2800, speaker=1),
        diarize.DiarizedSentence(text="真的不怪我。", start_ms=2900, end_ms=3600, speaker=1),
        diarize.DiarizedSentence(text="行吧。", start_ms=3700, end_ms=4200, speaker=0),
    ]
    text = diarize.format_dialogue(sentences)
    assert text.splitlines() == [
        "说话人A: 你今天怎么又迟到了?",
        "说话人B: 堵车堵得离谱。真的不怪我。",
        "说话人A: 行吧。",
    ]


def test_format_dialogue_empty() -> None:
    assert diarize.format_dialogue([]) == ""


def test_result_to_dict_shape() -> None:
    result = diarize.DiarizeResult(
        sentences=[diarize.DiarizedSentence(text="你好。", start_ms=0, end_ms=900, speaker=0)],
        speaker_count=1,
    )
    doc = diarize.result_to_dict(result)
    assert doc["backend"] == "funasr"
    assert doc["speakerCount"] == 1
    assert doc["sentences"] == [
        {"text": "你好。", "startMs": 0, "endMs": 900, "speaker": 0, "speakerLabel": "A"},
    ]


def test_diarize_runs_model_and_counts_speakers(monkeypatch, tmp_path: Path) -> None:
    audio = tmp_path / "vocals.mp3"
    audio.write_bytes(b"fake-audio")
    seen: dict[str, object] = {}

    class FakeModel:
        def generate(self, input: str, **kwargs) -> list[dict]:  # noqa: A002 - funasr 接口就叫 input
            seen["input"] = input
            seen["kwargs"] = kwargs
            return [{
                "text": "你好啊。哈喽。",
                "sentence_info": [
                    {"text": "你好啊。", "start": 0, "end": 1200, "spk": 0},
                    {"text": "哈喽。", "start": 1300, "end": 2500, "spk": 1},
                ],
            }]

    monkeypatch.setattr(diarize, "_get_model", lambda on_progress: FakeModel())
    monkeypatch.setattr(diarize, "_prepare_wav", lambda src, dst_dir, on_progress: src)

    result = diarize.diarize(audio)
    assert seen["input"] == str(audio)
    assert result.speaker_count == 2
    assert [s.speaker for s in result.sentences] == [0, 1]
    assert result.backend == "funasr"


def test_diarize_empty_sentences_returns_empty_result(monkeypatch, tmp_path: Path) -> None:
    audio = tmp_path / "vocals.mp3"
    audio.write_bytes(b"fake-audio")

    class FakeModel:
        def generate(self, input: str, **kwargs) -> list[dict]:  # noqa: A002
            return [{"text": "", "sentence_info": []}]

    monkeypatch.setattr(diarize, "_get_model", lambda on_progress: FakeModel())
    monkeypatch.setattr(diarize, "_prepare_wav", lambda src, dst_dir, on_progress: src)

    result = diarize.diarize(audio)
    assert result.sentences == []
    assert result.speaker_count == 0


def test_diarize_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        diarize.diarize(tmp_path / "missing.mp3")


def test_diarize_unavailable_without_funasr(monkeypatch, tmp_path: Path) -> None:
    audio = tmp_path / "vocals.mp3"
    audio.write_bytes(b"fake-audio")

    def boom() -> None:
        raise diarize.DiarizeUnavailableError("funasr not installed")

    monkeypatch.setattr(diarize, "_MODEL_CACHE", {})
    monkeypatch.setattr(diarize, "_import_automodel", boom)
    assert diarize.is_available() is False
    with pytest.raises(diarize.DiarizeUnavailableError):
        diarize.diarize(audio)


def test_normalize_sentences_joins_token_list_text() -> None:
    payload = [{"text": ["你", "好", "啊"], "start": 0, "end": 900, "spk": 1}]
    sentences = diarize.normalize_sentences(payload)
    assert sentences[0].text == "你好啊"
    assert sentences[0].speaker == 1


def test_sentences_from_dict_roundtrip() -> None:
    result = diarize.DiarizeResult(
        sentences=[
            diarize.DiarizedSentence(text="你好。", start_ms=0, end_ms=900, speaker=0),
            diarize.DiarizedSentence(text="哈喽。", start_ms=1000, end_ms=1900, speaker=1),
        ],
        speaker_count=2,
    )
    restored = diarize.sentences_from_dict(diarize.result_to_dict(result))
    assert restored == result.sentences


def test_get_model_builds_once_under_concurrency(monkeypatch) -> None:
    import threading as _threading
    import time as _time

    built: list[int] = []

    class SlowModel:
        def __init__(self, **kwargs) -> None:
            _time.sleep(0.05)
            built.append(1)

    monkeypatch.setattr(diarize, "_MODEL_CACHE", {})
    monkeypatch.setattr(diarize, "_import_automodel", lambda: SlowModel)

    instances: list[object] = []
    lock = _threading.Lock()

    def worker() -> None:
        model = diarize._get_model()
        with lock:
            instances.append(model)

    threads = [_threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 1, "并发下模型只能构建一次"
    assert len({id(m) for m in instances}) == 1


def _fake_model_env(monkeypatch):
    class FakeModel:
        def __init__(self, **kwargs) -> None:
            pass

        def generate(self, input: str, **kwargs) -> list[dict]:  # noqa: A002
            return [{"text": "你好。", "sentence_info": [
                {"text": "你好。", "start": 0, "end": 900, "spk": 0}]}]

    cache: dict[str, object] = {}
    monkeypatch.setattr(diarize, "_MODEL_CACHE", cache)
    monkeypatch.setattr(diarize, "_import_automodel", lambda: FakeModel)
    monkeypatch.setattr(diarize, "_prepare_wav", lambda src, dst_dir, on_progress: src)
    return cache


def test_diarize_unloads_model_by_default(monkeypatch, tmp_path: Path) -> None:
    """低配生产机策略:默认用完即卸,进程不常驻 3GB 模型。"""
    monkeypatch.delenv("NOF_DIARIZE_KEEP_MODEL", raising=False)
    cache = _fake_model_env(monkeypatch)
    audio = tmp_path / "vocals.mp3"
    audio.write_bytes(b"x")
    result = diarize.diarize(audio)
    assert result.speaker_count == 1
    assert "model" not in cache


def test_diarize_keeps_model_when_env_set(monkeypatch, tmp_path: Path) -> None:
    """NOF_DIARIZE_KEEP_MODEL=1(高内存 dev 机)常驻模型,省掉重复加载。"""
    monkeypatch.setenv("NOF_DIARIZE_KEEP_MODEL", "1")
    cache = _fake_model_env(monkeypatch)
    audio = tmp_path / "vocals.mp3"
    audio.write_bytes(b"x")
    diarize.diarize(audio)
    assert "model" in cache
