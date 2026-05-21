#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("tingwu_v2_transcribe.py")


def load_module():
    spec = importlib.util.spec_from_file_location("tingwu_v2_under_test", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TingwuV2TranscribeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_build_create_offline_task_uses_expected_contract(self):
        payload = self.module.build_create_offline_task("app-demo", "file:///tmp/demo.wav")

        self.assertEqual(
            payload,
            {
                "task": "createTask",
                "type": "offline",
                "appId": "app-demo",
                "fileUrl": "file:///tmp/demo.wav",
                "phraseId": "",
            },
        )

    def test_extract_text_from_task_collects_nested_results(self):
        payload = {
            "output": {
                "results": [
                    {"transcript": "hello"},
                    {"result": {"sentences": [{"text": "world"}]}},
                ]
            }
        }

        text = self.module.extract_text_from_task(payload)

        self.assertEqual(text, "hello\nworld")

    def test_extract_text_from_task_returns_empty_string_for_unknown_shape(self):
        payload = {"output": {"results": [{"foo": "bar"}]}}

        text = self.module.extract_text_from_task(payload)

        self.assertEqual(text, "")


if __name__ == "__main__":
    unittest.main()
