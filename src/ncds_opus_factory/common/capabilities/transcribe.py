"""转写能力：调 skills/tingwu-asr(听悟/Paraformer) + 听写稿清洗(qwen 优先,本地兜底)。

底层原语，供沈括等上层组合。monkeypatch tingwu 的 key 用主仓库 .env（而非 openclaw 默认）。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from ._base import REPO_ROOT, ProgressFn, noop

MAIN_ENV = REPO_ROOT / ".env"  # 仓库根 .env(已 gitignore,模板见 .env.example)
TINGWU_DIR = REPO_ROOT / "skills" / "tingwu-asr" / "scripts"


def read_dashscope_key() -> str | None:
    """优先 env DASHSCOPE_API_KEY,其次主仓库 .env;都没有则返回 None(让 tingwu 走 openclaw 默认)。"""
    if os.getenv("DASHSCOPE_API_KEY"):
        return os.environ["DASHSCOPE_API_KEY"]
    if MAIN_ENV.exists():
        for line in MAIN_ENV.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DASHSCOPE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def transcribe(video_path: Path, on_progress: ProgressFn = noop) -> tuple[dict | None, str]:
    """调 tingwu_transcribe.transcribe_file,返回 (结果 dict, 纯文本)。"""
    if str(TINGWU_DIR) not in sys.path:
        sys.path.insert(0, str(TINGWU_DIR))
    import tingwu_transcribe as tw  # type: ignore

    key = read_dashscope_key()
    if key:
        tw.get_api_key = lambda: key  # 用主仓库 .env 的 key,而非 openclaw 默认
    result = tw.transcribe_file(str(video_path))
    if result is None:
        return None, ""
    text = tw.extract_text(result)
    result_dict = json.loads(json.dumps(result, default=lambda o: getattr(o, "__dict__", str(o))))
    return result_dict, text


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
