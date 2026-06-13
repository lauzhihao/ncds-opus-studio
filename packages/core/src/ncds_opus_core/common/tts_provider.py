"""TTS provider 抽象 —— 把"用什么音色/语速"(决定)和"怎么调哪家 API"(细节)分开。

设计模式:
- Strategy   : `TtsProvider` 统一接口,运行时可换 provider(CosyVoice 现役,未来可加更高质量引擎)。
- Adapter    : `CosyVoiceProvider` 把阿里云 DashScope 的 HTTP 细节(endpoint/payload/重试/鉴权)
               适配成统一的 `synth(text, out, spec)`,调用方完全看不到 DashScope。
- Factory +  : `get_provider(name)` 从注册表按名取 provider;加新引擎只 `@register`,不改调用方。
  Registry

调用方(commands/tts.py / 伯牙)只认 `SynthSpec` + `provider.synth(...)`,不碰任何 API 细节。
依赖:CosyVoiceProvider 需要 $DASHSCOPE_API_KEY。
"""
from __future__ import annotations

import abc
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


@dataclass(frozen=True)
class SynthSpec:
    """一次合成的参数(与具体 provider 无关的通用部分 + 可选 provider 私有 model)。"""
    voice: str
    rate: float = 1.1
    sample_rate: int = 22050
    audio_format: str = "mp3"
    model: str | None = None  # provider 私有模型名;None = 用 provider 默认


class TtsProvider(abc.ABC):
    """所有 TTS 引擎的统一接口。"""

    name: str = "base"
    default_spec: SynthSpec = SynthSpec(voice="")

    @abc.abstractmethod
    def synth(
        self,
        text: str,
        out_path: Path,
        spec: SynthSpec,
        *,
        attempts: int = 4,
        timeout: int = 60,
        on_progress: ProgressFn = _noop,
    ) -> None:
        """把 text 合成到 out_path。失败应重试,最终失败抛异常。"""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# 注册表 + 工厂
# --------------------------------------------------------------------------- #
_REGISTRY: dict[str, type[TtsProvider]] = {}


def register(name: str) -> Callable[[type[TtsProvider]], type[TtsProvider]]:
    """provider 类装饰器:登记到注册表。"""
    def deco(cls: type[TtsProvider]) -> type[TtsProvider]:
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return deco


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def get_provider(name: str | None = None, **kwargs) -> TtsProvider:
    """取一个 provider 实例。name 省略时取 env NOF_TTS_PROVIDER,再退到 cosyvoice。"""
    name = name or os.getenv("NOF_TTS_PROVIDER", "cosyvoice")
    if name not in _REGISTRY:
        raise ValueError(f"未知 TTS provider: {name};已注册: {available_providers()}")
    return _REGISTRY[name](**kwargs)


# --------------------------------------------------------------------------- #
# CosyVoice / DashScope 适配器(细节藏在这里,别处不再出现 DashScope 字样)
# --------------------------------------------------------------------------- #
@register("cosyvoice")
class CosyVoiceProvider(TtsProvider):
    """阿里云 DashScope SpeechSynthesizer(CosyVoice)。

    逻辑沿用原 commands/tts.py 的 _synth_one(已验证可用),只是收拢到 Adapter 内。
    """

    URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
    DEFAULT_MODEL = "cosyvoice-v3-flash"  # plus / v3.5 系列当前 key 未开通
    default_spec = SynthSpec(voice="longtian_v3", rate=1.1, sample_rate=22050, model=DEFAULT_MODEL)

    def synth(
        self,
        text: str,
        out_path: Path,
        spec: SynthSpec,
        *,
        attempts: int = 4,
        timeout: int = 60,
        on_progress: ProgressFn = _noop,
    ) -> None:
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY env var not set")
        payload = {
            "model": spec.model or self.DEFAULT_MODEL,
            "input": {
                "text": text,
                "voice": spec.voice,
                "format": spec.audio_format,
                "sample_rate": spec.sample_rate,
                "rate": spec.rate,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        last_err: Exception | None = None
        for n in range(1, attempts + 1):
            try:
                req = urllib.request.Request(self.URL, data=body, method="POST", headers=headers)
                resp = json.load(urllib.request.urlopen(req, timeout=timeout))
                url = resp.get("output", {}).get("audio", {}).get("url")
                if not url:
                    raise RuntimeError(f"no audio.url in response: {resp}")
                tmp = out_path.with_suffix(out_path.suffix + ".part")
                urllib.request.urlretrieve(url, tmp)
                tmp.rename(out_path)
                return
            except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as exc:
                try:
                    detail = (
                        exc.read().decode(errors="replace")
                        if isinstance(exc, urllib.error.HTTPError)
                        else str(exc)
                    )
                except Exception:
                    detail = str(exc)
                wait = 1.5 * n
                on_progress(f"retry {n}/{attempts} after {wait:.1f}s ({detail[:120]})")
                time.sleep(wait)
                last_err = exc
        raise last_err if last_err else RuntimeError("synth failed for unknown reason")
