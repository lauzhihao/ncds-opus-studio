"""说话人分离能力:本地 FunASR(paraformer-zh + fsmn-vad + ct-punc + CAM++ 声纹聚类)。

为什么选 FunASR:免费、离线、中文识别强,CPU 可跑;听悟是付费服务,只有需要
多人对白拆分的 domain(如 comedy)才走本能力,普通转写仍走 transcribe.py。
首次运行由 modelscope 自动下载模型(约 1GB,缓存在 ~/.cache/modelscope),之后完全离线。

输出契约:每句带 (text, start_ms, end_ms, speaker),speaker 是 0-based 聚类编号,
speaker_label 映射成 A/B/C 供人读;编号只在单个音频内一致,跨作品不保证同一人同号。
"""

from __future__ import annotations

import gc
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._base import ProgressFn, noop

# funasr 1.4.x 内置别名:paraformer-zh(识别)/fsmn-vad(切句)/ct-punc(标点)/cam++(声纹)。
# spk_model 依赖 punc_model 才能出 sentence_info(见 funasr auto_model.py)。
ASR_MODEL = "paraformer-zh"
VAD_MODEL = "fsmn-vad"
PUNC_MODEL = "ct-punc"
SPK_MODEL = "cam++"

# funasr 未承诺线程安全:构建与推理都持同一把锁串行化——并发任务重复建模型会
# 双倍吃内存(首跑还会并发下载同一份模型缓存),并发 generate 结果不可信。
#
# 内存策略:整套模型实测常驻约 3.3GB(2026-08 Mac RSS 实测),生产机(154)只有
# 7.8GB 且无 swap,常驻会挤压 Demucs/whisper 触发 OOM。因此默认「用完即卸」:
# 每次 diarize 结束释放模型,下次从磁盘缓存重载(约 20-60s,相对采集全程可忽略)。
# 高内存机器(dev Mac)可设 NOF_DIARIZE_KEEP_MODEL=1 换成常驻提速。
_MODEL_CACHE: dict[str, Any] = {}
_MODEL_LOCK = threading.Lock()


def _keep_model_resident() -> bool:
    return (os.getenv("NOF_DIARIZE_KEEP_MODEL") or "").strip().lower() in {"1", "true", "yes", "on"}


def _release_model() -> None:
    """从进程缓存卸载模型并强制 gc;并发任务仍持引用时,内存在最后一个引用释放后回收。"""
    with _MODEL_LOCK:
        _MODEL_CACHE.pop("model", None)
    gc.collect()


class DiarizeUnavailableError(RuntimeError):
    """funasr 未安装或模型不可用时抛出,调用方自行降级(不拖垮采集)。"""


@dataclass
class DiarizedSentence:
    text: str
    start_ms: int
    end_ms: int
    speaker: int


@dataclass
class DiarizeResult:
    sentences: list[DiarizedSentence]
    speaker_count: int
    text: str = ""
    backend: str = "funasr"
    model: str = f"{ASR_MODEL}+{SPK_MODEL}"
    raw_response: dict[str, Any] = field(default_factory=dict)


def _import_automodel() -> Any:
    try:
        from funasr import AutoModel
    except ImportError as exc:  # pragma: no cover - depends on optional runtime package
        raise DiarizeUnavailableError("funasr not installed") from exc
    return AutoModel


def is_available() -> bool:
    try:
        _import_automodel()
    except DiarizeUnavailableError:
        return False
    return True


def _get_model(on_progress: ProgressFn = noop) -> Any:
    cached = _MODEL_CACHE.get("model")
    if cached is not None:
        return cached
    with _MODEL_LOCK:
        cached = _MODEL_CACHE.get("model")
        if cached is not None:
            return cached
        automodel_cls = _import_automodel()
        on_progress("加载 FunASR 说话人分离模型(首次运行会下载约 1GB)…")
        model = automodel_cls(
            model=ASR_MODEL,
            vad_model=VAD_MODEL,
            punc_model=PUNC_MODEL,
            spk_model=SPK_MODEL,
            disable_update=True,
            log_level="ERROR",
            disable_pbar=True,
        )
        _MODEL_CACHE["model"] = model
        return model


def _prepare_wav(src: Path, dst_dir: Path, on_progress: ProgressFn = noop) -> Path:
    """统一转 16k 单声道 wav:声纹模型按 16k 训练,mp3/mp4 直接喂容易踩解码差异。"""
    ffmpeg = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
    wav = dst_dir / f"{src.stem}.16k.wav"
    subprocess.run(  # noqa: S603
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", str(wav)],
        check=True, timeout=600,
    )
    return wav


def _ms(value: Any) -> int:
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return 0


def _speaker_id(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_sentences(payload: Any) -> list[DiarizedSentence]:
    """funasr sentence_info -> 稳定句子结构;脏条目跳过,缺 spk 归 0。"""
    if not isinstance(payload, list):
        return []
    sentences: list[DiarizedSentence] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        raw_text = item.get("text")
        # funasr 标点降级路径下 text 可能是 token list,直接 str() 会得到 "['你','好']"
        if isinstance(raw_text, (list, tuple)):
            raw_text = "".join(str(part) for part in raw_text)
        text = str(raw_text or "").strip()
        if not text:
            continue
        sentences.append(DiarizedSentence(
            text=text,
            start_ms=_ms(item.get("start")),
            end_ms=_ms(item.get("end")),
            speaker=_speaker_id(item.get("spk")),
        ))
    return sentences


def speaker_label(speaker: int) -> str:
    """0/1/2 -> A/B/C;超出 26 人或异常编号退回 S{n},保证标签唯一可读。"""
    if 0 <= speaker < 26:
        return chr(ord("A") + speaker)
    return f"S{speaker}"


def format_dialogue(sentences: list[DiarizedSentence]) -> str:
    """渲染人读对话体:相邻同说话人句子合并成一个发言轮次。"""
    turns: list[tuple[int, list[str]]] = []
    for sentence in sentences:
        text = sentence.text.strip()
        if not text:
            continue
        if turns and turns[-1][0] == sentence.speaker:
            turns[-1][1].append(text)
        else:
            turns.append((sentence.speaker, [text]))
    return "\n".join(f"说话人{speaker_label(spk)}: {''.join(parts)}" for spk, parts in turns)


def sentences_from_dict(doc: dict[str, Any]) -> list[DiarizedSentence]:
    """result_to_dict 的逆向:从落盘的 asr.speakers.json 还原句子(供缓存重建 dialogue)。"""
    sentences: list[DiarizedSentence] = []
    for item in doc.get("sentences") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        sentences.append(DiarizedSentence(
            text=text,
            start_ms=_ms(item.get("startMs")),
            end_ms=_ms(item.get("endMs")),
            speaker=_speaker_id(item.get("speaker")),
        ))
    return sentences


def result_to_dict(result: DiarizeResult) -> dict[str, Any]:
    return {
        "backend": result.backend,
        "model": result.model,
        "speakerCount": result.speaker_count,
        "sentences": [
            {
                "text": s.text,
                "startMs": s.start_ms,
                "endMs": s.end_ms,
                "speaker": s.speaker,
                "speakerLabel": speaker_label(s.speaker),
            }
            for s in result.sentences
        ],
    }


def diarize(
    audio_path: Path,
    on_progress: ProgressFn = noop,
    *,
    speaker_hint: int | None = None,
) -> DiarizeResult:
    """对单个音频做 ASR + 说话人分离。

    speaker_hint:已知人数时传入(如 2/3),内部作为聚类 oracle_num(preset_spk_num);
    不传则自动估计人数。句子为空返回空结果(交调用方按 empty 处理),不抛异常。
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(str(audio_path))
    try:
        model = _get_model(on_progress)
        with tempfile.TemporaryDirectory(prefix="nof-diarize-") as tmp:
            wav = _prepare_wav(audio_path, Path(tmp), on_progress)
            on_progress("FunASR 听写 + 声纹聚类中…")
            generate_kwargs: dict[str, Any] = {"batch_size_s": 300}
            if speaker_hint is not None and speaker_hint > 0:
                generate_kwargs["preset_spk_num"] = speaker_hint
            with _MODEL_LOCK:
                outputs = model.generate(input=str(wav), **generate_kwargs)
    finally:
        # 默认用完即卸(低配生产机友好);NOF_DIARIZE_KEEP_MODEL=1 常驻提速
        if not _keep_model_resident():
            _release_model()
    first = outputs[0] if isinstance(outputs, list) and outputs else {}
    if not isinstance(first, dict):
        first = {}
    sentences = normalize_sentences(first.get("sentence_info"))
    speakers = {s.speaker for s in sentences}
    return DiarizeResult(
        sentences=sentences,
        speaker_count=len(speakers),
        text=str(first.get("text") or "").strip(),
    )
