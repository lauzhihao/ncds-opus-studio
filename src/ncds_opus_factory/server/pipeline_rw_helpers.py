"""RW model invocation, prompt, source-text, and QC helpers."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.common.opus_cli import DEFAULT_OPUS_MODEL, call_opus, is_opus_available

DEFAULT_OPUS_MODEL_ID = DEFAULT_OPUS_MODEL
RW_LLM_TIMEOUT_SEC = int(os.getenv("NOF_RW_LLM_TIMEOUT", "900"))

MODEL_CANDIDATES: list[dict[str, str]] = [
    {"id": "opus",         "label": "改写方案 A",  "runner": "opus",         "model": DEFAULT_OPUS_MODEL_ID},
    {"id": "deepseek",     "label": "改写方案 B",  "runner": "deepseek",     "model": "deepseek-v4-pro"},
    {"id": "agy",          "label": "改写方案 C",  "runner": "agy",          "model": "gemini-3.5-flash"},
    {"id": "codex",        "label": "改写方案 D",  "runner": "scodex",       "model": "gpt-5.5-codex"},
]


class _ModelUnavailable(Exception):
    """专用 sentinel：模型在本机不可用（缺二进制 / 缺 API key 等）。"""


def _check_model_available(cand: dict[str, str]) -> tuple[bool, str]:
    """返回 (是否可用, 不可用原因)。可用时 reason='' 。"""
    runner = cand["runner"]
    if runner == "opus":
        return (is_opus_available(), "本机未安装 opus 启动器")
    if runner == "scodex":
        return (shutil.which("scodex") is not None, "本机未安装 scodex 启动器")
    if runner == "gemini_local":
        p = Path.home() / ".gemini" / "g.sh"
        return (p.is_file(), "~/.gemini/g.sh 未安装")
    if runner == "agy":
        return (shutil.which("agy") is not None, "本机未安装 agy 启动器")
    if runner == "deepseek":
        return (bool(os.environ.get("DEEPSEEK_API_KEY")), "DEEPSEEK_API_KEY 未设置")
    return (False, f"unknown runner: {runner}")


async def _invoke_rw_candidate(
    cand: dict[str, str],
    user_prompt: str,
    system_prompt: str,
    on_progress: Callable[[str], None],
    on_status: Callable[[str, str], None] | None = None,
) -> str:
    """对单个 candidate：可用就调，返回 raw text；不可用就 raise _ModelUnavailable。"""
    mid = cand["id"]

    def status(st: str) -> None:
        if on_status is not None:
            on_status(mid, st)

    available, reason = _check_model_available(cand)
    if not available:
        on_progress(f"模型 {mid} 跳过：{reason}")
        status("unavailable")
        raise _ModelUnavailable(reason)
    on_progress(f"模型 {mid} 开始调用")
    status("running")
    runner = cand["runner"]
    try:
        if runner == "opus":
            text = await asyncio.to_thread(_call_opus_for_rw, user_prompt, system_prompt, cand["model"])
        elif runner == "scodex":
            combined = _build_codex_user_prompt(
                system_prompt=system_prompt,
                task_prompt=user_prompt,
                target_profile="paper_card_talk",
                expect_json=False,
            )
            text = await asyncio.to_thread(_call_scodex_for_rw, combined, cand["model"])
        elif runner == "agy":
            text = await asyncio.to_thread(_call_agy_for_rw, user_prompt, system_prompt, cand["model"])
        elif runner == "deepseek":
            text = await asyncio.to_thread(_call_deepseek_for_rw, user_prompt, system_prompt, cand["model"])
        else:
            status("unavailable")
            raise _ModelUnavailable(f"runner {runner} 尚未实装")
        on_progress(f"模型 {mid} 调用完成（{len(text)} 字）")
        status("done")
        return text
    except _ModelUnavailable:
        raise
    except Exception as exc:
        on_progress(f"模型 {mid} 调用失败：{exc}")
        status("failed")
        raise


def _call_opus_for_rw(user_prompt: str, system_prompt: str, model_id: str) -> str:
    """RW opus 调用薄适配层；真实 launch/parse 统一委托 common.opus_cli.call_opus。"""
    return call_opus(
        user_prompt,
        system_prompt=system_prompt,
        model=model_id,
        timeout_seconds=RW_LLM_TIMEOUT_SEC,
    )


def _call_scodex_for_rw(prompt: str, model_id: str) -> str:
    """走本机 scodex 启动器 → codex CLI。"""
    args = [
        "scodex", "launch", "--no-resume", "--",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "-s", "read-only",
        "-m", model_id,
        "--json",
        prompt,
    ]
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=RW_LLM_TIMEOUT_SEC,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"scodex launcher exited {proc.returncode}: {tail}")

    final = ""
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "item.completed":
            continue
        item = payload.get("item") or {}
        text = item.get("text")
        if not isinstance(text, str) and isinstance(item.get("content"), list):
            text = "".join(
                p.get("text", "")
                for p in item["content"]
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            )
        if isinstance(text, str) and text.strip():
            final = text.strip()
    if not final:
        raise RuntimeError(f"scodex empty result; stdout tail={proc.stdout[-300:]}")
    return final


def _build_codex_user_prompt(
    *,
    system_prompt: str,
    task_prompt: str,
    target_profile: str,
    expect_json: bool,
) -> str:
    """对齐远程 video_rewrite_runner.buildCodexCliPrompt 的四段结构。"""
    output_contract = (
        '只输出一个合法 JSON 对象，不要代码块、解释或前后缀。'
        if expect_json
        else '只输出最终候选稿正文，不要解释过程、代码块或额外前后缀。'
    )
    return "\n".join([
        f"目标类型：{target_profile}",
        "",
        "【系统角色】",
        system_prompt,
        "",
        "【任务要求】",
        task_prompt,
        "",
        "【硬性输出约束】",
        output_contract,
    ])


def _call_deepseek_for_rw(user_prompt: str, system_prompt: str, model_id: str) -> str:
    """rw 改写候选的 DeepSeek 调用。"""
    from ncds_opus_factory.common.deepseek_cli import call_deepseek

    return call_deepseek(user_prompt, system_prompt=system_prompt, model=model_id)


def _call_agy_for_rw(user_prompt: str, system_prompt: str, model_id: str) -> str:
    """rw 改写候选的 AGY 调用。"""
    from ncds_opus_factory.common.agy_cli import call_agy

    combined = "\n".join([
        "【系统角色】",
        system_prompt,
        "",
        "【任务要求】",
        user_prompt,
    ]) if system_prompt else user_prompt

    return call_agy(combined, model=model_id)


_GENERIC_RW_BODY: list[str] = [
    "你是资深中文内容写手。请根据下面源文档的内容与气质，写一篇高质量、可直接使用的中文稿件。",
    "",
    "【写作要求】",
    "- 体裁、风格、结构由你判断什么最适合这份素材；",
    "- 开头黄金 3 秒抛钩子（反差 / 反常识 / 悬念），结尾有力收束；",
    "- 口语化、强节奏、信息密度高；不堆术语、不写 AI 味套话（如「首先 / 其次 / 综上所述」）；",
    "- 合理分段（用空行分隔），不要 markdown 标记。",
]


def _rw_domain_guidance(domain: str | None) -> str | None:
    """取作品垂类(domain)的写作方法 prompt（domain_profiles.liuyong）；无/未知 domain → None。"""
    from ncds_opus_factory.server.domain_profiles import get_profile

    dp = get_profile(domain) if domain else None
    return dp.get("liuyong") if dp else None


def _rw_source_text(asr_items: list[dict[str, Any]], job_dir: Path) -> str:
    """把 asr 产物拼成 rw 的源文本。"""
    sections: list[str] = []
    for it in asr_items:
        txt = (it.get("text") or "").strip()
        if not txt:
            relpath = it.get("article_relpath") or it.get("transcript_relpath")
            if relpath and (job_dir / relpath).is_file():
                txt = (job_dir / relpath).read_text(encoding="utf-8").strip()
        if not txt:
            continue
        title = str(it.get("desc") or it.get("title") or "")
        for tag in it.get("hashtags") or []:
            title = title.replace(f"#{tag}", "")
        title = " ".join(title.split())
        sections.append(f"## 来源 {it.get('index')} - {title}\n\n{txt}")
    return "\n\n---\n\n".join(sections).strip()


def _purge_ai_taste_rw(text: str, report: dict[str, Any], on_progress: Callable[[str], None]) -> str:
    """把 ai_taste 命中的 AI 味甩回 opus 消除，返回改写后全文（common 单点实现）。"""
    from ncds_opus_factory.common import ai_taste

    try:
        return ai_taste.purge_ai_taste(text, report, timeout_seconds=RW_LLM_TIMEOUT_SEC)
    except Exception as exc:  # noqa: BLE001 — 消 AI 味失败不致命，保留上一版
        on_progress(f"  消 AI 味调用失败: {exc}")
        return ""


def _apply_rw_qc(model_dir: Path, model_id: str, on_progress: Callable[[str], None]) -> dict[str, Any]:
    """对一份已写盘的 draft.md 跑柳永质检闸门。"""
    from ncds_opus_factory.common import ai_taste, quality_rubric

    draft_path = model_dir / "draft.md"
    if not draft_path.is_file():
        return {}
    text = draft_path.read_text(encoding="utf-8").strip()
    report = ai_taste.scan(text)
    on_progress(f"质检[{model_id}]: {report.get('verdict')} - {report.get('summary', '')}")
    rounds = 0
    while report.get("verdict") == "fail" and rounds < 2:
        rounds += 1
        on_progress(f"  [{model_id}] AI 味超标，打回重写第 {rounds} 轮…")
        new_text = _purge_ai_taste_rw(text, report, on_progress)
        if not new_text or len(new_text) < 200:
            on_progress(f"  [{model_id}] 重写未返回有效稿，保留上一版")
            break
        text = new_text
        report = ai_taste.scan(text)
        on_progress(f"  [{model_id}] 第 {rounds} 轮后: {report.get('verdict')}")
    rub = quality_rubric.score(text, avoid_models={model_id})
    if rub.get("available"):
        judge = rub.get("judge_model", "?")
        on_progress(f"质检2[rubric/{model_id}](judge={judge}): {rub.get('total')}/50 {rub.get('grade')}")
    else:
        on_progress(f"质检2[rubric/{model_id}]: 跳过（{rub.get('skipped')}）")
    draft_path.write_text(text + "\n", encoding="utf-8")
    (model_dir / "draft.qc.json").write_text(
        json.dumps({"qc": report, "qc_rubric": rub}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"qc": report, "qc_rubric": rub}


def _build_rw_prompt(
    source_text: str,
    user_requirements: str = "",
    domain_guidance: str | None = None,
) -> tuple[str, str]:
    """构造 RW 的 (system_prompt, user_prompt)。"""
    system_prompt = (
        "你是中文内容改写的资深写手。请按下方【写作要求】把源文档改写成一篇可直接使用的中文稿件，"
        "保留原意、不编造事实，输出纯文本正文，不要 JSON 或代码块包裹。"
    )
    if domain_guidance and domain_guidance.strip():
        parts: list[str] = ["【写作要求】", domain_guidance.strip()]
    else:
        parts = list(_GENERIC_RW_BODY)
    parts += [
        "",
        "【通用约束】",
        "- 必须使用简体中文；",
        "- 直接输出纯文本正文，不要 JSON、不要 ``` 代码块包裹、不要额外的元描述；",
        "- 不得编造源文档未出现的人物、平台、数据；只能改写、压缩、重组源文档信息。",
        "",
        "== 源文档 ==",
        source_text,
        "== 源文档结束 ==",
    ]
    reqs = user_requirements or ""
    try:
        from ncds_opus_factory.common import rubric_store

        brief = rubric_store.injection_brief()
        if brief:
            reqs = (reqs + "\n\n" if reqs else "") + brief
    except Exception:  # noqa: BLE001 — 口味注入失败不阻塞改写
        pass
    if reqs.strip():
        parts += [
            "",
            "【用户附加要求 / Leader 口味（最高优先级，可覆盖以上默认要求）】：",
            reqs.strip(),
        ]
    return system_prompt, "\n".join(parts)
