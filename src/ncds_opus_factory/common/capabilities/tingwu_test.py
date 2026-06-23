from __future__ import annotations

from pathlib import Path

from ncds_opus_factory.common.capabilities import tingwu


def test_extract_output_field_reads_output_and_data_shapes() -> None:
    assert tingwu.extract_output_field({"output": {"dataId": "out-id"}}, "dataId") == "out-id"
    assert tingwu.extract_output_field({"data": {"url": "https://example.test/file.wav"}}, "url") == "https://example.test/file.wav"


def test_resolve_file_url_uploads_local_file(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_text("audio", encoding="utf-8")
    calls: list[tuple[Path, str]] = []

    def fake_upload(path: Path, api_key: str) -> str:
        calls.append((path, api_key))
        return "https://example.test/sample.wav"

    monkeypatch.setattr(tingwu, "upload_local_file", fake_upload)

    assert tingwu.resolve_file_url(audio_path, "sk-test") == "https://example.test/sample.wav"
    assert calls == [(audio_path, "sk-test")]


def test_upload_local_file_returns_download_url_from_dashscope_data_shape(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_text("audio", encoding="utf-8")

    class FakeFiles:
        @staticmethod
        def upload(*, file_path: str, purpose: str, api_key: str) -> dict:
            assert file_path == str(audio_path.resolve())
            assert purpose == "file-extract"
            assert api_key == "sk-test"
            return {"status_code": 200, "data": {"uploaded_files": [{"file_id": "file-1"}]}}

        @staticmethod
        def get(file_id: str, *, api_key: str) -> dict:
            assert file_id == "file-1"
            assert api_key == "sk-test"
            return {"status_code": 200, "data": {"url": "https://example.test/sample.wav"}}

    monkeypatch.setattr(tingwu, "DashScopeFiles", FakeFiles)

    assert tingwu.upload_local_file(audio_path, "sk-test") == "https://example.test/sample.wav"
