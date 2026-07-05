from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from ncds_opus_factory.common.capabilities import bcut

transcribe = importlib.import_module("ncds_opus_factory.common.capabilities.transcribe")


class FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None, *, headers: dict[str, str] | None = None) -> None:
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = str(self._payload)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeBcutSession:
    def __init__(self) -> None:
        self.uploaded: list[bytes] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        if url == bcut.API_REQ_UPLOAD:
            return FakeResponse(
                {
                    "data": {
                        "in_boss_key": "boss-key",
                        "resource_id": "resource-id",
                        "upload_id": "upload-id",
                        "upload_urls": ["https://upload.example/part-1"],
                        "per_size": 1024,
                    }
                }
            )
        if url == bcut.API_COMMIT_UPLOAD:
            return FakeResponse({"data": {"download_url": "https://download.example/audio.mp3"}})
        if url == bcut.API_CREATE_TASK:
            return FakeResponse({"data": {"task_id": "task-1"}})
        raise AssertionError(f"unexpected POST {url}")

    def put(self, url: str, **kwargs: Any) -> FakeResponse:
        assert url == "https://upload.example/part-1"
        self.uploaded.append(kwargs["data"])
        return FakeResponse(headers={"Etag": "etag-1"})

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        assert url == bcut.API_QUERY_RESULT
        return FakeResponse(
            {
                "data": {
                    "state": 4,
                    "result": '{"utterances":[{"transcript":"你好","start_time":0,"end_time":800},'
                    '{"transcript":"世界","start_time":900,"end_time":1500}]}',
                }
            }
        )


def test_bcut_transcribe_file_uploads_and_extracts_text(tmp_path: Path) -> None:
    audio = tmp_path / "sample.mp3"
    audio.write_bytes(b"mp3-bytes")
    session = FakeBcutSession()

    result = bcut.transcribe_file(
        audio,
        session=session,  # type: ignore[arg-type]
        max_polls=1,
        poll_interval_seconds=0,
    )

    assert result.backend == "bcut"
    assert result.task_id == "task-1"
    assert result.text == "你好\n世界"
    assert session.uploaded == [b"mp3-bytes"]


def test_transcribe_uses_tingwu_fallback_when_bcut_fails(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"not-real-video")
    progress: list[str] = []

    def fail_bcut(*args: Any, **kwargs: Any) -> bcut.BcutTranscript:
        raise bcut.BcutUnavailableError("offline")

    monkeypatch.setattr(transcribe.bcut, "transcribe_file", fail_bcut)
    monkeypatch.setattr(
        transcribe,
        "_transcribe_tingwu",
        lambda _path, _progress: ({"backend": "tingwu", "model": "tingwu-meeting"}, "fallback text"),
    )

    raw, text = transcribe.transcribe(video, progress.append, engine="bcut")

    assert text == "fallback text"
    assert raw is not None
    assert raw["backend"] == "tingwu"
    assert raw["fallbackFrom"] == "bcut"
    assert "offline" in raw["fallbackReason"]
    assert any("Bcut ASR 失败" in item for item in progress)


def test_transcribe_treats_primary_empty_as_fallback_trigger(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"not-real-video")

    def empty_bcut(*args: Any, **kwargs: Any) -> bcut.BcutTranscript:
        raise bcut.BcutUnavailableError("Bcut task completed without transcript text")

    monkeypatch.setattr(transcribe.bcut, "transcribe_file", empty_bcut)
    monkeypatch.setattr(
        transcribe,
        "_transcribe_tingwu",
        lambda _path, _progress: ({"backend": "tingwu", "empty": True}, ""),
    )

    raw, text = transcribe.transcribe(video, lambda _msg: None, engine="bcut")

    assert text == ""
    assert raw is not None
    assert raw["empty"] is True
    assert raw["fallbackFrom"] == "bcut"


def test_transcribe_whisper_empty_runs_one_fallback(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"not-real-video")

    monkeypatch.setattr(
        transcribe,
        "_transcribe_whisper",
        lambda _path, _progress, language=None: ({"backend": "whisper", "empty": True}, ""),
    )
    monkeypatch.setattr(
        transcribe,
        "_transcribe_bcut_with_tingwu_fallback",
        lambda _path, _progress: ({"backend": "bcut"}, "fallback text"),
    )

    raw, text = transcribe.transcribe(video, lambda _msg: None, engine="whisper")

    assert text == "fallback text"
    assert raw is not None
    assert raw["backend"] == "bcut"
    assert raw["fallbackFrom"] == "whisper"
