"""TTS provider 抽象的测试:工厂/注册 + 用 FakeProvider 验证 tts.run 走通,不碰真 API。"""
from __future__ import annotations

from pathlib import Path

import pytest

from ncds_opus_factory.common import tts_provider as tp
from ncds_opus_factory.common.tts_provider import SynthSpec, TtsProvider


@tp.register("fake")
class _FakeProvider(TtsProvider):
    """测试用:不联网,直接写一个占位字节,记录每次合成。"""
    default_spec = SynthSpec(voice="fake_voice")
    calls: list[tuple[str, str]] = []

    def synth(self, text, out_path: Path, spec, *, attempts=4, timeout=60, on_progress=tp._noop):
        type(self).calls.append((text, spec.voice))
        out_path.write_bytes(b"FAKEMP3")


def test_cosyvoice_is_default():
    prov = tp.get_provider()  # 无参 -> 默认 cosyvoice
    assert prov.name == "cosyvoice"
    assert prov.default_spec.voice == "longtian_v3"


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError, match="未知 TTS provider"):
        tp.get_provider("no_such_engine")


def test_fake_registered():
    assert "fake" in tp.available_providers()
    assert tp.get_provider("fake").name == "fake"


def test_tts_run_through_fake_provider(tmp_path: Path):
    """commands.tts.run 经 provider 抽象跑通:产文件 + 幂等跳过。"""
    from ncds_opus_factory.commands import tts

    _FakeProvider.calls.clear()
    out = tmp_path / "audio"
    res = tts.run(beats=["第一句", "第二句"], output_dir=str(out),
                  provider="fake", sleep_between=0)
    assert res["total"] == 2 and res["new_count"] == 2
    assert sorted(p.name for p in out.iterdir()) == ["0001.mp3", "0002.mp3"]
    assert len(_FakeProvider.calls) == 2

    # 再跑一次:已存在则跳过(幂等),不再调 provider
    _FakeProvider.calls.clear()
    res2 = tts.run(beats=["第一句", "第二句"], output_dir=str(out),
                   provider="fake", sleep_between=0)
    assert res2["skipped"] == 2 and res2["new_count"] == 0
    assert _FakeProvider.calls == []


def test_synthspec_defaults():
    spec = SynthSpec(voice="x")
    assert spec.rate == 1.1 and spec.sample_rate == 22050 and spec.audio_format == "mp3"
