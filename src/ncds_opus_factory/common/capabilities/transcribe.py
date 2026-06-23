"""ASR 能力：听悟原始听写 + 内部清洗后处理。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ._base import ProgressFn, noop, read_dashscope_key
from . import tingwu


def transcribe(video_path: Path, on_progress: ProgressFn = noop) -> tuple[dict | None, str]:
    """调用唯一听悟 adapter，返回 (raw_response, raw_text)。"""
    try:
        on_progress("听悟 ASR 听写中...")
        result = tingwu.transcribe_file(video_path)
    except Exception as exc:  # noqa: BLE001 - 单条采集失败由上层降级/记录
        on_progress(f"听悟 ASR 失败: {type(exc).__name__}")
        return None, ""
    result_dict = {
        "backend": result.backend,
        "model": result.model,
        "dataId": result.data_id,
        "rawResponse": result.raw_response,
    }
    return json.loads(json.dumps(result_dict, ensure_ascii=False)), result.text


def clean_transcript(raw: str, on_progress: ProgressFn = noop) -> str | None:
    """听写稿清洗:首选 qwen(同音错别字/语义断句的长尾规则穷举不了),
    失败/无 key 时回退本地规则兜底。在转写支线内执行,与 Demucs 等支线并行。"""
    text = (raw or "").strip()
    if len(text) < 20:
        return None
    cleaned = _clean_with_qwen(text, on_progress)
    if cleaned:
        return cleaned
    return _clean_local(text, on_progress)


def _clean_with_qwen(raw: str, on_progress: ProgressFn = noop) -> str | None:
    """qwen 清洗校对:纠同音错别字、去口头语、按语义断句加标点;不得增删事实。"""
    key = read_dashscope_key()
    if not key:
        return None
    import requests

    prompt = (
        "下面是一份语音听写稿(ASR 原文)。请清洗校对:纠正同音/近音错别字,"
        "去除口头语和无意义语气词,按语义断句、补全标点、适当分段。"
        "不得增删事实,不得改写表达风格,不得编造原文没有的内容。"
        "直接输出清洗后的正文,不要任何解释或前后缀。\n\n" + raw[:6000]
    )
    try:
        on_progress("听写稿清洗中(qwen)…")
        resp = requests.post(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "qwen-plus", "temperature": 0.2,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=(15, 120),
        )
        resp.raise_for_status()
        cleaned = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        if not cleaned:
            return None
        on_progress(f"清洗完成(qwen): {len(raw)} -> {len(cleaned)} 字")
        return cleaned
    except Exception as e:  # noqa: BLE001 — qwen 失败交给本地兜底
        on_progress(f"qwen 清洗失败,本地规则兜底: {type(e).__name__}")
        return None


def _clean_local(raw: str, on_progress: ProgressFn = noop) -> str | None:
    """本地规则兜底:去孤立语气词、规整空白与重复标点(无网络/无 key 时至少可读)。"""
    text = re.sub(r"(?:(?<=^)|(?<=[,，。!！?？、\s]))(呃+|嗯+|哎+|诶+)(?=[,，。!！?？、\s]|$)", "", raw)
    text = re.sub(r"[ \t]+", "", text)
    text = re.sub(r"，{2,}", "，", text)
    text = re.sub(r"。{2,}", "。", text)
    cleaned = text.strip()
    if not cleaned or cleaned == raw.strip():
        return None
    on_progress(f"本地清洗(兜底): {len(raw)} -> {len(cleaned)} 字")
    return cleaned
