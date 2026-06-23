from __future__ import annotations

from pathlib import Path

from ncds_opus_factory.commands import asr


def test_extract_urls_dedupes_media_links() -> None:
    text = "看这个 https://v.douyin.com/demo/ 还有 https://v.douyin.com/demo/."

    assert asr.extract_urls(text) == ["https://v.douyin.com/demo/"]


def test_run_invokes_local_video_pipeline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(asr, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(asr, "VIDEO_PIPELINE", tmp_path / "video_pipeline.py")
    asr.VIDEO_PIPELINE.write_text("pass", encoding="utf-8")
    calls: list[dict] = []

    class FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        result_json = tmp_path / "video-jobs" / "asr_test" / "deliverables" / "result.json"
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text("{}", encoding="utf-8")
        return FakeCompletedProcess()

    monkeypatch.setattr(asr.subprocess, "run", fake_run)

    result = asr.run(
        text="https://v.douyin.com/demo/",
        payload={"jobId": "asr_test", "inputs": ["https://v.douyin.com/demo/"]},
    )

    assert result["job_id"] == "asr_test"
    assert result["result_json"].endswith("video-jobs/asr_test/deliverables/result.json")
    assert calls[0]["command"][1] == str(asr.VIDEO_PIPELINE)
    assert calls[0]["command"][-1] == "https://v.douyin.com/demo/"
