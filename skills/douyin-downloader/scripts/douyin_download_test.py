#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).with_name("douyin_download.py")


def load_module():
    spec = importlib.util.spec_from_file_location("douyin_download_under_test", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, chunks=None, error=None):
        self._chunks = chunks or []
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=0):
        if self._error:
            raise self._error
        for chunk in self._chunks:
            yield chunk


class DouyinDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_download_video_retries_after_incomplete_read_and_cleans_part_file(self):
        responses = [
            FakeResponse(error=ConnectionError("broken stream")),
            FakeResponse(chunks=[b"hello", b"world"]),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "video.mp4"

            with (
                patch.object(self.module.requests, "get", side_effect=responses) as get_mock,
                patch.object(self.module.time, "sleep") as sleep_mock,
            ):
                saved_path = self.module.download_video("https://example.com/video.mp4", output_path, max_retries=2)

            self.assertEqual(saved_path, str(output_path))
            self.assertEqual(output_path.read_bytes(), b"helloworld")
            self.assertFalse(output_path.with_suffix(".mp4.part").exists())
            self.assertEqual(get_mock.call_count, 2)
            sleep_mock.assert_called_once()

    def test_download_video_raises_last_error_after_all_retries(self):
        responses = [
            FakeResponse(error=ConnectionError("broken stream")),
            FakeResponse(error=ConnectionError("still broken")),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "video.mp4"

            with (
                patch.object(self.module.requests, "get", side_effect=responses),
                patch.object(self.module.time, "sleep"),
            ):
                with self.assertRaises(ConnectionError) as ctx:
                    self.module.download_video("https://example.com/video.mp4", output_path, max_retries=2)

            self.assertIn("still broken", str(ctx.exception))
            self.assertFalse(output_path.exists())
            self.assertFalse(output_path.with_suffix(".mp4.part").exists())


if __name__ == "__main__":
    unittest.main()
