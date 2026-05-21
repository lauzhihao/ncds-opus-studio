#!/usr/bin/env python3
from contextlib import ExitStack
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).with_name("asr_service.py")


def load_asr_service_module():
    os.environ.setdefault("OPENCLAW_FFMPEG", "/usr/bin/true")
    os.environ.setdefault("OPENCLAW_WHISPER", "/usr/bin/true")

    spec = importlib.util.spec_from_file_location("asr_service_under_test", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AsrServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_asr_service_module()

    def make_backend_patches(self, audio_path, *, v2_result=None, legacy_result=None, whisper_result=None):
        backend_calls = []
        transcript_path = audio_path.with_suffix(".txt")

        def fake_v2(*args, **kwargs):
            backend_calls.append("tingwu-v2")
            if isinstance(v2_result, Exception):
                raise v2_result
            return v2_result

        def fake_legacy(*args, **kwargs):
            backend_calls.append("tingwu-legacy")
            if isinstance(legacy_result, Exception):
                raise legacy_result
            return legacy_result

        def fake_whisper(*args, **kwargs):
            backend_calls.append("whisper")
            if isinstance(whisper_result, Exception):
                raise whisper_result
            if whisper_result is not None:
                transcript_path.write_text("fallback transcript", encoding="utf-8")
            return whisper_result

        patches = (
            patch.object(self.module, "get_audio_duration", return_value=0),
            patch.object(self.module, "proofread_with_fallback", side_effect=lambda text, _: text),
            patch.object(self.module, "transcribe_via_tingwu_v2_script", side_effect=fake_v2, create=True),
            patch.object(self.module, "transcribe_via_tingwu_legacy", side_effect=fake_legacy, create=True),
            patch.object(self.module, "transcribe_via_whisper", side_effect=fake_whisper, create=True),
        )
        return backend_calls, transcript_path, patches

    def test_transcribe_audio_uses_v2_as_first_choice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "sample.wav"
            audio_path.write_text("audio", encoding="utf-8")
            txt_path = audio_path.with_suffix(".txt")
            txt_path.write_text("v2 transcript", encoding="utf-8")
            backend_calls, _, patches = self.make_backend_patches(audio_path, v2_result=txt_path)

            with ExitStack() as stack:
                for active_patch in patches:
                    stack.enter_context(active_patch)
                result = self.module.transcribe_audio(audio_path)

            self.assertEqual(backend_calls, ["tingwu-v2"])
            self.assertEqual(result.backendUsed, "tingwu-v2")
            self.assertFalse(result.fallbackTriggered)

    def test_transcribe_audio_falls_back_to_legacy_when_v2_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "sample.wav"
            audio_path.write_text("audio", encoding="utf-8")
            txt_path = audio_path.with_suffix(".txt")
            txt_path.write_text("legacy transcript", encoding="utf-8")
            backend_calls, _, patches = self.make_backend_patches(
                audio_path,
                v2_result=RuntimeError("400 access denied"),
                legacy_result=txt_path,
            )

            with ExitStack() as stack:
                for active_patch in patches:
                    stack.enter_context(active_patch)
                result = self.module.transcribe_audio(audio_path)

            self.assertEqual(backend_calls, ["tingwu-v2", "tingwu-legacy"])
            self.assertEqual(result.backendUsed, "tingwu-legacy")
            self.assertTrue(result.fallbackTriggered)
            self.assertEqual(result.fallbackReason, "tingwu-v2 failed")

    def test_transcribe_audio_falls_back_to_whisper_when_cloud_chain_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "sample.wav"
            audio_path.write_text("audio", encoding="utf-8")
            txt_path = audio_path.with_suffix(".txt")
            backend_calls, _, patches = self.make_backend_patches(
                audio_path,
                v2_result=RuntimeError("400 access denied"),
                legacy_result=RuntimeError("500 internal server error"),
                whisper_result=txt_path,
            )

            with ExitStack() as stack:
                for active_patch in patches:
                    stack.enter_context(active_patch)
                result = self.module.transcribe_audio(audio_path)

            self.assertEqual(backend_calls, ["tingwu-v2", "tingwu-legacy", "whisper"])
            self.assertEqual(result.backendUsed, "whisper")
            self.assertTrue(result.fallbackTriggered)

    def test_transcribe_audio_returns_structured_failure_when_all_backends_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "sample.wav"
            audio_path.write_text("audio", encoding="utf-8")
            backend_calls, _, patches = self.make_backend_patches(
                audio_path,
                v2_result=RuntimeError("400 access denied"),
                legacy_result=RuntimeError("429 rate limit exceeded"),
                whisper_result=RuntimeError("whisper crashed"),
            )

            with ExitStack() as stack:
                for active_patch in patches:
                    stack.enter_context(active_patch)
                result = self.module.transcribe_audio(audio_path)

            self.assertEqual(backend_calls, ["tingwu-v2", "tingwu-legacy", "whisper"])
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.backendUsed, "whisper")
            self.assertEqual(result.errorKind, "fallback-failed")

    def test_transcribe_v2_script_writes_transcript_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "sample.wav"
            audio_path.write_text("audio", encoding="utf-8")
            transcript_path = audio_path.with_suffix(".txt")
            captured = {}

            class FakeCompletedProcess:
                def __init__(self):
                    self.returncode = 0
                    self.stdout = json.dumps({"status": "success", "text": "hello from tingwu script"})
                    self.stderr = ""

            def fake_run(cmd, capture_output=True, text=True, timeout=None):
                captured["cmd"] = cmd
                return FakeCompletedProcess()

            with patch.object(self.module.subprocess, "run", side_effect=fake_run):
                result = self.module.transcribe_via_tingwu_v2_script(audio_path)

            self.assertEqual(result, transcript_path)
            self.assertEqual(transcript_path.read_text(encoding="utf-8"), "hello from tingwu script")
            self.assertIn("python", Path(captured["cmd"][0]).name.lower())

    def test_transcribe_v2_script_requires_successful_json_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "sample.wav"
            audio_path.write_text("audio", encoding="utf-8")

            class FakeCompletedProcess:
                def __init__(self):
                    self.returncode = 0
                    self.stdout = json.dumps({"status": "success"})
                    self.stderr = ""

            with patch.object(self.module.subprocess, "run", return_value=FakeCompletedProcess()):
                with self.assertRaises(RuntimeError) as ctx:
                    self.module.transcribe_via_tingwu_v2_script(audio_path)

            self.assertIn("missing text", str(ctx.exception))

    def test_call_model_dispatches_to_local_gemini(self):
        with patch.object(self.module, "call_local_gemini_model", return_value="gemini text") as model_mock:
            result = self.module.call_model("local-gemini", "g.sh", "prompt", "system")

        self.assertEqual(result, "gemini text")
        model_mock.assert_called_once_with("g.sh", "prompt", "system")

    def test_call_model_dispatches_to_openai_codex(self):
        with patch.object(self.module, "call_openai_codex_model", return_value="codex text") as model_mock:
            result = self.module.call_model("openai-codex", "gpt-5.4", "prompt", "system")

        self.assertEqual(result, "codex text")
        model_mock.assert_called_once_with("gpt-5.4", "prompt", "system")

    def test_proofread_with_fallback_tries_local_gemini_then_openai_codex(self):
        calls = []

        def fake_call_model(provider, model_name, prompt, system=None):
            calls.append((provider, model_name, prompt, system))
            if provider == "local-gemini":
                raise RuntimeError("local gemini failed")
            return "codex proofread text"

        with patch.object(self.module, "call_model", side_effect=fake_call_model):
            result = self.module.proofread_with_fallback("原始文本", self.module.SINGLE_PASS_PROOFREAD_MODELS)

        self.assertEqual(result, "codex proofread text")
        self.assertEqual(
            [(provider, model_name) for provider, model_name, *_ in calls],
            [("local-gemini", "g.sh"), ("openai-codex", "gpt-5.4")],
        )

    def test_call_model_rejects_unsupported_provider(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.module.call_model("codeproxy", "gpt-5.4", "prompt", "system")

        self.assertIn("不支持的 provider", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
