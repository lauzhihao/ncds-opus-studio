#!/usr/bin/env python3
from contextlib import ExitStack
import importlib.util
import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).with_name("video_pipeline.py")


def load_video_pipeline_module():
    os.environ.setdefault("OPENCLAW_YT_DLP", "/usr/bin/true")
    os.environ.setdefault("OPENCLAW_FFMPEG", "/usr/bin/true")
    os.environ.setdefault("OPENCLAW_WHISPER", "/usr/bin/true")

    spec = importlib.util.spec_from_file_location("video_pipeline_under_test", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


class FakePool:
    def __init__(self, max_workers):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, fn, args):
        return FakeFuture(fn(args))


class VideoPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_video_pipeline_module()

    def make_check_tools_patches(self, tmpdir, *, cloud_usable, whisper_usable):
        yt_dlp = Path(tmpdir) / "yt-dlp"
        ffmpeg = Path(tmpdir) / "ffmpeg"
        yt_dlp.write_text("", encoding="utf-8")
        ffmpeg.write_text("", encoding="utf-8")

        available_paths = {str(yt_dlp), str(ffmpeg)}

        def fake_isfile(path):
            return path in available_paths

        return (
            patch.object(self.module, "YT_DLP", str(yt_dlp)),
            patch.object(self.module, "FFMPEG", str(ffmpeg)),
            patch.object(self.module.os.path, "isfile", side_effect=fake_isfile),
            patch.object(self.module, "asr_is_cloud_usable", return_value=cloud_usable),
            patch.object(self.module, "asr_is_whisper_usable", return_value=whisper_usable),
        )

    def make_gemini_cli(self, tmpdir):
        cli_path = Path(tmpdir) / "g.sh"
        cli_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        return cli_path

    def make_model_output_config(self, configured_model_ids=None, *, gemini_cli_path="/__disabled__/g.sh"):
        configured_model_ids = configured_model_ids or ["gpt54"]
        id_to_model_ref = {
            "gemini": "local-gemini/g.sh",
            "gpt54": "openai-codex/gpt-5.4",
        }
        models = {id_to_model_ref[model_id]: {"alias": model_id} for model_id in configured_model_ids}
        return {
            "models": {
                "providers": {
                    "local-gemini": {
                        "cliPath": str(gemini_cli_path),
                    },
                },
            },
            "agents": {
                "defaults": {
                    "models": models,
                },
            },
        }

    def fake_successful_model_call(self, provider, model_name, prompt, system=None):
        if "润色" in prompt:
            return f"{model_name} polished " * 40
        return f"{model_name} rewrite " * 40

    def test_format_success_line_for_download_uses_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = Path(tmpdir) / "file.m4a"
            media_path.touch()

            line = self.module.format_success_line("下载", media_path)

            self.assertEqual(line, f"✅ 下载: {media_path.resolve()}")
            self.assertTrue(Path(line.split(": ", 1)[1]).is_absolute())

    def test_format_success_line_for_transcript_uses_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_path = Path(tmpdir) / "file.txt"
            transcript_path.touch()

            line = self.module.format_success_line("转写", transcript_path)

            self.assertEqual(line, f"✅ 转写: {transcript_path.resolve()}")
            self.assertTrue(Path(line.split(": ", 1)[1]).is_absolute())

    def test_build_polished_transcript_output_path_targets_deliverables_directory(self):
        raw_transcript = Path("/tmp/job/raw/podcast_demo.txt")
        deliverables_dir = Path("/tmp/job/deliverables")

        polished_path = self.module.build_polished_transcript_output_path(raw_transcript, deliverables_dir)

        self.assertEqual(polished_path, Path("/tmp/job/deliverables/podcast_demo.polished.txt"))

    def test_polish_system_prompt_requires_simplified_chinese_output(self):
        self.assertIn("简体中文", self.module.POLISH_SYSTEM_PROMPT)

    def test_download_video_passes_output_template_under_output_dir_to_ytdlp(self):
        output_dir = Path("/tmp/job/raw")
        captured = {}

        def fake_build_ytdlp_cmd(url, platform, output_path):
            captured["url"] = url
            captured["platform"] = platform
            captured["output_path"] = output_path
            return ["yt-dlp"]

        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = "download failed"

        with (
            patch.object(self.module, "build_ytdlp_cmd", side_effect=fake_build_ytdlp_cmd),
            patch.object(self.module.subprocess, "run", return_value=FakeResult()),
        ):
            result = self.module.download_video("https://example.com/video", "youtube", output_dir)

        self.assertIsNone(result)
        self.assertEqual(captured["output_path"], str(output_dir / "youtube_%(id)s.%(ext)s"))

    def test_get_tikhub_script_prefers_adjacent_workspace_skill(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_root = Path(tmpdir) / "skills"
            script_dir = skills_root / "video-pipeline" / "scripts"
            script_dir.mkdir(parents=True)
            tikhub_script = skills_root / "douyin-downloader" / "scripts" / "douyin_download.py"
            tikhub_script.parent.mkdir(parents=True)
            tikhub_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

            self.module.get_tikhub_script.cache_clear()
            try:
                with (
                    patch.dict(self.module.os.environ, {}, clear=True),
                    patch.object(self.module, "SCRIPT_DIR", script_dir),
                    patch.object(self.module, "get_openclaw_npm_root", side_effect=RuntimeError("npm unavailable")),
                ):
                    resolved = self.module.get_tikhub_script()
            finally:
                self.module.get_tikhub_script.cache_clear()

        self.assertEqual(resolved, str(tikhub_script))

    def test_download_via_tikhub_logs_stdout_when_stderr_is_empty(self):
        class FakeResult:
            returncode = 1
            stdout = "stdout failure detail"
            stderr = ""

        stdout = StringIO()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            with (
                patch.object(self.module, "resolve_short_url", return_value="https://www.douyin.com/video/7604126095385824527"),
                patch.object(self.module, "get_tikhub_script", return_value="/tmp/douyin_download.py"),
                patch.object(self.module.subprocess, "run", return_value=FakeResult()),
                patch("sys.stdout", stdout),
            ):
                result = self.module.download_via_tikhub("https://v.douyin.com/demo/", "douyin", output_dir)

        self.assertIsNone(result)
        self.assertIn("❌ TikHub 下载失败: stdout failure detail", stdout.getvalue())

    def test_process_url_calls_asr_service_without_knowing_backend_details(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            video_path = output_dir / "downloaded.mp4"
            audio_path = output_dir / "downloaded.wav"
            transcript_path = output_dir / "downloaded.txt"
            video_path.touch()
            audio_path.touch()
            transcript_path.write_text("transcript", encoding="utf-8")
            captured = {}

            result = self.module.AsrTranscriptionResult(
                status="success",
                backendUsed="tingwu-v2",
                rawText="transcript",
                rawTextPath=str(transcript_path),
                errorKind=None,
                errorMessage=None,
                fallbackTriggered=False,
                fallbackReason=None,
            )

            def fake_asr(audio_file, language="Chinese"):
                captured["audio_file"] = audio_file
                captured["language"] = language
                return result

            with (
                patch.object(self.module, "extract_url", return_value="https://example.com/video"),
                patch.object(self.module, "detect_platform", return_value="youtube"),
                patch.object(self.module, "download_video", return_value=video_path),
                patch.object(self.module, "extract_audio", return_value=audio_path),
                patch.object(self.module, "asr_transcribe_audio", side_effect=fake_asr),
                patch.object(
                    self.module,
                    "generate_model_outputs",
                    return_value={
                        "transcript": str(transcript_path.resolve()),
                        "selectedPolishedModelId": None,
                        "selectedRewriteModelId": None,
                        "polishedTranscriptPath": None,
                        "rewritePath": None,
                        "failureReasons": {"polished": {}, "rewrite": {}},
                        "polishedVariants": [],
                        "rewriteVariants": [],
                    },
                ),
            ):
                process_result = self.module.process_url("ignored", output_dir)

            self.assertEqual(captured["audio_file"], audio_path)
            self.assertEqual(captured["language"], "Chinese")
            self.assertEqual(process_result["transcriptPath"], str(transcript_path.resolve()))

    def test_process_url_prints_exact_success_lines_without_leading_spaces(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            video_path = output_dir / "downloaded.mp4"
            audio_path = output_dir / "downloaded.wav"
            transcript_path = output_dir / "downloaded.txt"
            video_path.touch()
            audio_path.touch()
            transcript_path.touch()
            stdout = StringIO()

            with (
                patch.object(self.module, "extract_url", return_value="https://example.com/video"),
                patch.object(self.module, "detect_platform", return_value="youtube"),
                patch.object(self.module, "download_video", return_value=video_path),
                patch.object(self.module, "extract_audio", return_value=audio_path),
                patch.object(self.module, "transcribe", return_value=transcript_path),
                patch.object(
                    self.module,
                    "generate_model_outputs",
                    return_value={
                        "transcript": str(transcript_path.resolve()),
                        "selectedPolishedModelId": None,
                        "selectedRewriteModelId": None,
                        "polishedTranscriptPath": None,
                        "rewritePath": None,
                        "failureReasons": {"polished": {}, "rewrite": {}},
                        "polishedVariants": [],
                        "rewriteVariants": [],
                    },
                ),
                patch("sys.stdout", stdout),
            ):
                self.module.process_url("ignored", output_dir)

            output_lines = stdout.getvalue().splitlines()
            success_lines = [line for line in output_lines if line.startswith("✅ ")]
            self.assertEqual(
                success_lines,
                [
                    f"✅ 下载: {video_path.resolve()}",
                    f"✅ 转写: {transcript_path.resolve()}",
                ],
            )

    def test_module_loads_without_local_asr_binary_at_import_time(self):
        spec = importlib.util.spec_from_file_location("video_pipeline_no_whisper", SCRIPT_PATH)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        with patch("shutil.which", side_effect=lambda name: "/usr/bin/true" if name in {"yt-dlp", "ffmpeg"} else None):
            spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "asr_transcribe_audio"))

    def test_check_tools_rejects_when_cloud_and_local_asr_are_both_unavailable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with ExitStack() as stack:
                for active_patch in self.make_check_tools_patches(tmpdir, cloud_usable=False, whisper_usable=False):
                    stack.enter_context(active_patch)
                self.assertFalse(self.module.check_tools())

    def test_job_root_helpers_create_raw_and_deliverables_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"

            raw_dir = self.module.ensure_raw_dir(job_root)
            deliverables_dir = self.module.ensure_deliverables_dir(job_root)

            self.assertEqual(raw_dir, job_root / "raw")
            self.assertEqual(deliverables_dir, job_root / "deliverables")
            self.assertTrue(raw_dir.is_dir())
            self.assertTrue(deliverables_dir.is_dir())

    def test_default_polished_and_rewrite_paths_live_in_deliverables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"
            raw_dir = job_root / "raw"
            raw_dir.mkdir(parents=True)
            transcript_path = raw_dir / "demo.txt"
            transcript_path.write_text("transcript", encoding="utf-8")

            polished_path = self.module.build_main_polished_output_path(transcript_path)
            rewrite_path = self.module.build_main_rewrite_output_path(transcript_path)

            self.assertEqual(polished_path, job_root / "deliverables" / "demo.polished.txt")
            self.assertEqual(rewrite_path, job_root / "deliverables" / "demo.rewrite.txt")

    def test_check_tools_fails_when_tingwu_and_whisper_are_both_unavailable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with ExitStack() as stack:
                for active_patch in self.make_check_tools_patches(tmpdir, cloud_usable=False, whisper_usable=False):
                    stack.enter_context(active_patch)
                self.assertFalse(self.module.check_tools())

    def test_main_treats_output_as_job_root_and_uses_raw_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"
            process_calls = []

            def fake_process_url(url, output_dir, no_transcribe=False, language="Chinese"):
                process_calls.append((url, output_dir, no_transcribe, language))
                return {"status": "success", "stage": "transcribe"}

            argv = [
                str(SCRIPT_PATH),
                "--output",
                str(job_root),
                "https://example.com/video",
            ]

            with (
                patch.object(self.module, "check_tools", return_value=True),
                patch.object(self.module, "process_url", side_effect=fake_process_url),
                patch.object(self.module.sys, "argv", argv),
            ):
                self.module.main()

            self.assertEqual(len(process_calls), 1)
            self.assertEqual(process_calls[0][1].resolve(), (job_root / "raw").resolve())
            self.assertTrue((job_root / "raw").is_dir())
            self.assertTrue((job_root / "deliverables").is_dir())

    def test_write_result_json_writes_to_job_root_deliverables_when_given_raw_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"
            raw_dir = job_root / "raw"
            raw_dir.mkdir(parents=True)
            transcript_path = raw_dir / "demo.txt"
            transcript_path.write_text("transcript", encoding="utf-8")

            self.module.write_result_json(raw_dir, transcript_path)

            result_json_path = job_root / "deliverables" / "result.json"
            self.assertTrue(result_json_path.exists())
            payload = json.loads(result_json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["rawTranscriptPath"], str(transcript_path.resolve()))
            self.assertEqual(result_json_path.parent, job_root / "deliverables")

    def test_generate_model_outputs_returns_main_output_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"
            raw_dir = job_root / "raw"
            raw_dir.mkdir(parents=True)
            transcript_path = raw_dir / "demo.txt"
            transcript_path.write_text("raw transcript", encoding="utf-8")

            with (
                patch.object(self.module, "load_openclaw_config", return_value=self.make_model_output_config()),
                patch.object(self.module, "call_model", side_effect=self.fake_successful_model_call),
            ):
                outputs = self.module.generate_model_outputs(transcript_path)

            deliverables_dir = job_root / "deliverables"
            self.assertEqual(outputs["polishedTranscriptPath"], deliverables_dir / "demo.polished.txt")
            self.assertEqual(outputs["rewritePath"], deliverables_dir / "demo.rewrite.txt")

    def test_generate_model_outputs_runs_configured_model_through_both_stages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"
            raw_dir = job_root / "raw"
            raw_dir.mkdir(parents=True)
            transcript_path = raw_dir / "demo.txt"
            transcript_path.write_text("raw transcript " * 40, encoding="utf-8")

            def fake_call_model(provider, model_name, prompt, system=None):
                if "润色" in prompt:
                    return "gpt-5.4 polished " * 40
                return "gpt-5.4 rewrite " * 40

            with (
                patch.object(self.module, "load_openclaw_config", return_value=self.make_model_output_config(["gpt54"])),
                patch.object(self.module, "call_model", side_effect=fake_call_model),
            ):
                outputs = self.module.generate_model_outputs(transcript_path)

            polished_variants = {item["modelId"]: item for item in outputs["polishedVariants"]}
            rewrite_variants = {item["modelId"]: item for item in outputs["rewriteVariants"]}
            self.assertEqual(polished_variants["gpt54"]["status"], "success")
            self.assertEqual(rewrite_variants["gpt54"]["status"], "success")
            self.assertEqual(outputs["selectedPolishedModelId"], "gpt54")
            self.assertEqual(outputs["selectedRewriteModelId"], "gpt54")

    def test_generate_model_outputs_marks_missing_model_config_as_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"
            raw_dir = job_root / "raw"
            raw_dir.mkdir(parents=True)
            transcript_path = raw_dir / "demo.txt"
            transcript_path.write_text("raw transcript " * 40, encoding="utf-8")

            def fake_call_model(provider, model_name, prompt, system=None):
                if "润色" in prompt:
                    return "polished text " * 40
                return "rewrite text " * 40

            with (
                patch.object(self.module, "load_openclaw_config", return_value=self.make_model_output_config(["gpt54"])),
                patch.object(self.module, "call_model", side_effect=fake_call_model),
            ):
                outputs = self.module.generate_model_outputs(transcript_path)

            polished_variants = {item["modelId"]: item for item in outputs["polishedVariants"]}
            rewrite_variants = {item["modelId"]: item for item in outputs["rewriteVariants"]}
            self.assertEqual(polished_variants["gemini"]["status"], "skipped")
            self.assertEqual(polished_variants["gemini"]["reason"], "missing_model_config")
            self.assertEqual(rewrite_variants["gemini"]["status"], "skipped")
            self.assertEqual(rewrite_variants["gemini"]["reason"], "missing_model_config")
            self.assertEqual(polished_variants["gpt54"]["status"], "success")
            self.assertEqual(rewrite_variants["gpt54"]["status"], "success")

    def test_generate_model_outputs_skips_same_model_rewrite_when_polish_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"
            raw_dir = job_root / "raw"
            raw_dir.mkdir(parents=True)
            transcript_path = raw_dir / "demo.txt"
            transcript_path.write_text("raw transcript " * 40, encoding="utf-8")

            def fake_call_model(provider, model_name, prompt, system=None):
                if model_name == "g.sh":
                    raise RuntimeError("gemini failed")
                if "润色" in prompt:
                    return f"{model_name} polished " * 40
                return f"{model_name} rewrite " * 40

            gemini_cli_path = self.make_gemini_cli(tmpdir)
            with (
                patch.object(
                    self.module,
                    "load_openclaw_config",
                    return_value=self.make_model_output_config(["gemini"], gemini_cli_path=gemini_cli_path),
                ),
                patch.object(self.module, "call_model", side_effect=fake_call_model),
            ):
                outputs = self.module.generate_model_outputs(transcript_path)

            rewrite_variants = {item["modelId"]: item for item in outputs["rewriteVariants"]}
            self.assertEqual(rewrite_variants["gemini"]["status"], "skipped")
            self.assertEqual(rewrite_variants["gemini"]["reason"], "missing_polished_input")

    def test_select_main_variant_prefers_local_gemini_then_openai_codex(self):
        results = [
            {"modelId": "gemini", "status": "success", "path": "/tmp/gemini.txt"},
            {"modelId": "gpt54", "status": "success", "path": "/tmp/gpt54.txt"},
        ]

        selected = self.module.select_main_variant(results, ["gemini", "gpt54"])

        self.assertEqual(selected["modelId"], "gemini")

    def test_generate_model_outputs_writes_polished_variants_in_deliverables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"
            raw_dir = job_root / "raw"
            raw_dir.mkdir(parents=True)
            transcript_path = raw_dir / "demo.txt"
            transcript_path.write_text("raw transcript", encoding="utf-8")

            with (
                patch.object(self.module, "load_openclaw_config", return_value=self.make_model_output_config()),
                patch.object(self.module, "call_model", side_effect=self.fake_successful_model_call),
            ):
                outputs = self.module.generate_model_outputs(transcript_path)

            deliverables_dir = job_root / "deliverables"
            self.assertGreaterEqual(len(outputs["polishedVariants"]), 1)
            success_paths = [Path(item["path"]) for item in outputs["polishedVariants"] if item["status"] == "success" and item["path"]]
            self.assertGreaterEqual(len(success_paths), 1)
            self.assertTrue(all(path.exists() for path in success_paths))
            self.assertTrue(all(path.parent == deliverables_dir for path in success_paths))

    def test_generate_model_outputs_writes_rewrite_variants_in_deliverables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"
            raw_dir = job_root / "raw"
            raw_dir.mkdir(parents=True)
            transcript_path = raw_dir / "demo.txt"
            transcript_path.write_text("raw transcript", encoding="utf-8")

            with (
                patch.object(self.module, "load_openclaw_config", return_value=self.make_model_output_config()),
                patch.object(self.module, "call_model", side_effect=self.fake_successful_model_call),
            ):
                outputs = self.module.generate_model_outputs(transcript_path)

            deliverables_dir = job_root / "deliverables"
            self.assertGreaterEqual(len(outputs["rewriteVariants"]), 1)
            success_paths = [Path(item["path"]) for item in outputs["rewriteVariants"] if item["status"] == "success" and item["path"]]
            self.assertGreaterEqual(len(success_paths), 1)
            self.assertTrue(all(path.exists() for path in success_paths))
            self.assertTrue(all(path.parent == deliverables_dir for path in success_paths))

    def test_generate_model_outputs_writes_main_output_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"
            raw_dir = job_root / "raw"
            raw_dir.mkdir(parents=True)
            transcript_path = raw_dir / "demo.txt"
            transcript_path.write_text("raw transcript", encoding="utf-8")

            with (
                patch.object(self.module, "load_openclaw_config", return_value=self.make_model_output_config()),
                patch.object(self.module, "call_model", side_effect=self.fake_successful_model_call),
            ):
                self.module.generate_model_outputs(transcript_path)

            deliverables_dir = job_root / "deliverables"
            self.assertTrue((deliverables_dir / "demo.polished.txt").exists())
            self.assertTrue((deliverables_dir / "demo.rewrite.txt").exists())

    def test_process_url_writes_result_json_with_null_missing_main_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job_root = Path(tmpdir) / "video-jobs" / "vj_demo123"
            raw_dir = job_root / "raw"
            raw_dir.mkdir(parents=True)
            video_path = raw_dir / "downloaded.mp4"
            audio_path = raw_dir / "downloaded.wav"
            transcript_path = raw_dir / "downloaded.txt"
            video_path.touch()
            audio_path.touch()
            transcript_path.write_text("transcript", encoding="utf-8")

            with (
                patch.object(self.module, "extract_url", return_value="https://example.com/video"),
                patch.object(self.module, "detect_platform", return_value="youtube"),
                patch.object(self.module, "download_video", return_value=video_path),
                patch.object(self.module, "extract_audio", return_value=audio_path),
                patch.object(self.module, "transcribe", return_value=transcript_path),
                patch.object(
                    self.module,
                    "generate_model_outputs",
                    return_value={
                        "transcript": str(transcript_path.resolve()),
                        "selectedPolishedModelId": None,
                        "selectedRewriteModelId": None,
                        "polishedTranscriptPath": None,
                        "rewritePath": None,
                        "failureReasons": {},
                        "polishedVariants": [],
                        "rewriteVariants": [],
                    },
                    create=True,
                ),
            ):
                self.module.process_url("ignored", raw_dir)

            result_json_path = job_root / "deliverables" / "result.json"
            self.assertTrue(result_json_path.exists())
            payload = json.loads(result_json_path.read_text(encoding="utf-8"))
            self.assertIsNone(payload["polishedTranscriptPath"])
            self.assertIsNone(payload["rewritePath"])

    def test_check_tools_accepts_tingwu_without_whisper_when_api_key_and_dashscope_are_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with ExitStack() as stack:
                for active_patch in self.make_check_tools_patches(tmpdir, cloud_usable=True, whisper_usable=False):
                    stack.enter_context(active_patch)
                stack.enter_context(patch("builtins.print"))
                self.assertTrue(self.module.check_tools())

    def test_check_tools_rejects_tingwu_without_api_key_when_whisper_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with ExitStack() as stack:
                for active_patch in self.make_check_tools_patches(tmpdir, cloud_usable=False, whisper_usable=False):
                    stack.enter_context(active_patch)
                self.assertFalse(self.module.check_tools())

    def test_check_tools_rejects_tingwu_when_dashscope_import_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with ExitStack() as stack:
                for active_patch in self.make_check_tools_patches(tmpdir, cloud_usable=False, whisper_usable=True):
                    stack.enter_context(active_patch)
                self.assertTrue(self.module.check_tools())


if __name__ == "__main__":
    unittest.main()
