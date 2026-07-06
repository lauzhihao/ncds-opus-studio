"""RW model invocation, prompt, source-text, and QC helpers."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ncds_opus_factory.common.agy_cli import agy_unavailable_reason
from ncds_opus_factory.common.opus_cli import DEFAULT_OPUS_MODEL, call_opus, is_opus_available

DEFAULT_OPUS_MODEL_ID = DEFAULT_OPUS_MODEL
RW_LLM_TIMEOUT_SEC = int(os.getenv("NOF_RW_LLM_TIMEOUT", "900"))

MODEL_CANDIDATES: list[dict[str, str]] = [
    {"id": "opus",         "label": "改写方案 A",  "runner": "opus",         "model": DEFAULT_OPUS_MODEL_ID},
    {"id": "deepseek",     "label": "改写方案 B",  "runner": "deepseek",     "model": "deepseek-v4-pro"},
    {"id": "agy",          "label": "改写方案 C",  "runner": "agy",          "model": "gemini-3.5-flash"},
    {"id": "codex",        "label": "改写方案 D",  "runner": "scodex",       "model": "gpt-5.5-codex"},
]


class _ModelUnavailable(Exception):  # noqa: N818
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
        reason = agy_unavailable_reason()
        return (not reason, reason)
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
    except Exception:
        status("unavailable")
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
    proc = subprocess.run(  # noqa: S603
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


def _as_nonempty_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int | float):
        return str(value)
    return ""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_guiguzi_doc(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "guiguzi.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 鬼谷子上下文缺失不应阻塞柳永
        return {}
    return data if isinstance(data, dict) else {}


def _find_topic_by_title(topics: Any, title: str) -> dict[str, Any]:
    if not title or not isinstance(topics, list):
        return {}
    for item in topics:
        topic = _as_dict(item)
        if _as_nonempty_text(topic.get("title")) == title:
            return topic
    return {}


def _format_guiguzi_topic(topic: dict[str, Any]) -> str:
    fields = [
        ("title", "选题标题"),
        ("angle", "切入角度"),
        ("why", "可能爆的原因"),
        ("anchor_comment", "锚定评论"),
        ("potential", "潜力分"),
        ("source_model", "来源模型"),
    ]
    lines: list[str] = []
    for key, label in fields:
        text = _as_nonempty_text(topic.get(key))
        if text:
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


def _format_guiguzi_analysis(analysis: dict[str, Any]) -> str:
    lines: list[str] = []
    hook_reason = _as_nonempty_text(analysis.get("hook_reason"))
    if hook_reason:
        lines.append(f"爆款原因: {hook_reason}")
    audience = _as_nonempty_text(analysis.get("audience"))
    if audience:
        lines.append(f"目标受众: {audience}")
    hooks = analysis.get("hooks")
    if isinstance(hooks, list):
        hook_lines = [_as_nonempty_text(x) for x in hooks]
        hook_lines = [x for x in hook_lines if x]
        if hook_lines:
            lines.append("可复制钩子:")
            lines.extend(f"- {x}" for x in hook_lines)
    else:
        hooks_text = _as_nonempty_text(hooks)
        if hooks_text:
            lines.append(f"可复制钩子: {hooks_text}")
    direction = _as_nonempty_text(analysis.get("direction"))
    if direction:
        lines.append(f"衍生选题方向: {direction}")
    return "\n".join(lines)


def _rw_guiguzi_context(
    rw_config: dict[str, Any] | None, job_dir: Path, domain: str | None = None,
) -> str:
    """把鬼谷子拆解/选题结果整理成柳永 prompt 输入。

    新任务优先读取 rw node_config 中由前端传入的 chosen_topic/chosen_analysis；
    旧任务或重跑时回退 per-job guiguzi.json 的 chosen_analysis。
    """
    cfg = _as_dict(rw_config)
    guiguzi_cfg = _as_dict(cfg.get("guiguzi"))
    guiguzi_doc = _read_guiguzi_doc(job_dir)

    topic = _as_dict(
        guiguzi_cfg.get("chosen_topic")
        or guiguzi_cfg.get("topic")
        or guiguzi_doc.get("chosen_topic")
        or guiguzi_doc.get("selected_topic")
    )
    if not topic:
        selected_title = _as_nonempty_text(
            guiguzi_cfg.get("chosen_title")
            or guiguzi_cfg.get("selected_title")
            or guiguzi_doc.get("chosen_title")
            or guiguzi_doc.get("selected_title")
        )
        topic = _find_topic_by_title(guiguzi_doc.get("topics"), selected_title)

    analysis = _as_dict(
        guiguzi_cfg.get("chosen_analysis")
        or guiguzi_cfg.get("analysis")
        or guiguzi_doc.get("chosen_analysis")
    )

    topic_text = _format_guiguzi_topic(topic)
    analysis_text = _format_guiguzi_analysis(analysis)
    if not topic_text and not analysis_text:
        return ""

    if (domain or "").strip().lower() == "film":
        parts: list[str] = [
            "我是一名抖音独家精选的影视解说博主。",
            "请基于下方【选定选题】和后面的【源文档/听写稿】，帮我把这段剧情找到 10 个增量。",
            "增量可以是心理描写，也可以是场景、细节、器具、环境的科普。",
            "这些增量不是新的选题数量要求，而是柳永成稿时要围绕选定选题补强的写作抓手。",
            "",
            "【选定选题】",
            topic_text or "(未选择具体选题，只参考爆款拆解)",
        ]
        if analysis_text:
            parts += ["", "【爆款原因拆解】", analysis_text]
        parts += [
            "",
            "【柳永落稿要求】",
            "- 成稿必须围绕【选定选题】展开，不要把 10 个增量写成列表报告；",
            "- 优先把增量自然揉进剧情推进、人物心理、镜头细节和道具/环境解释里；",
            "- 以下是剧情文案：$听写稿。实际听写稿见后文【源文档】。",
            "- 源文档仍是事实边界：不得编造源文档未出现的人物、情节、镜头或后续剧情。",
        ]
        return "\n".join(parts).strip()

    parts: list[str] = []
    if topic_text:
        parts += ["【选定选题】", topic_text]
    if analysis_text:
        if parts:
            parts.append("")
        parts += ["【爆款原因拆解】", analysis_text]
    return "\n".join(parts).strip()


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


def _ai_taste_issue_lines(report: dict[str, Any]) -> list[str]:
    """把 ai_taste report 转成可给 refine 模型使用的问题清单。"""
    if report.get("verdict") != "fail":
        return []
    issues: list[str] = []
    summary = _as_nonempty_text(report.get("summary"))
    if summary:
        issues.append(f"AI 味未通过: {summary}")

    for group_name in ("density", "hard"):
        hits = report.get(group_name)
        if not isinstance(hits, list):
            continue
        for hit in hits:
            if isinstance(hit, dict):
                rule = _as_nonempty_text(hit.get("rule")) or group_name
                count = _as_nonempty_text(hit.get("count"))
                samples_raw = hit.get("samples")
                samples: list[str] = []
                if isinstance(samples_raw, list):
                    samples = [_as_nonempty_text(x) for x in samples_raw]
                    samples = [x for x in samples if x]
                sample_text = "；".join(samples[:3])
                count_text = f" {count} 次" if count else ""
                sample_suffix = f"，例：{sample_text}" if sample_text else ""
                issues.append(f"{rule}{count_text}{sample_suffix}")
            else:
                text_hit = _as_nonempty_text(hit)
                if text_hit:
                    issues.append(text_hit)
    return issues or ["AI 味句式未通过，请消除命中的模板表达和硬禁口癖"]


def _purge_ai_taste_rw(
    text: str,
    report: dict[str, Any],
    on_progress: Callable[[str], None],
    *,
    model_id: str | None = None,
) -> str:
    """消除 ai_taste 命中的 AI 味；opus 不可用时用现有 refine 模型兜底。"""
    from ncds_opus_factory.common import ai_taste

    try:
        return ai_taste.purge_ai_taste(text, report, timeout_seconds=RW_LLM_TIMEOUT_SEC)
    except Exception:  # noqa: BLE001, S110 — 走下方兜底，不把异常细节推到前端
        pass

    try:
        from ncds_opus_factory.common import quality_rubric

        refined = quality_rubric.refine(
            text,
            _ai_taste_issue_lines(report),
            avoid_models={model_id} if model_id else None,
            timeout_seconds=RW_LLM_TIMEOUT_SEC,
        )
        if refined:
            on_progress("  消 AI 味兜底模型已返回")
            return refined
    except Exception:  # noqa: BLE001, S110 — 消 AI 味失败不致命，保留上一版
        pass
    on_progress("  消 AI 味通道不可用，保留上一版")
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
        new_text = _purge_ai_taste_rw(text, report, on_progress, model_id=model_id)
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
    return {"qc": report, "qc_rubric": rub, "needs_fix": report.get("verdict") == "fail"}


def _build_rw_prompt(
    source_text: str,
    user_requirements: str = "",
    domain_guidance: str | None = None,
    guiguzi_context: str | None = None,
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
    if guiguzi_context and guiguzi_context.strip():
        parts += [
            "",
            "【鬼谷子拆解 / 选题输入】",
            guiguzi_context.strip(),
            "",
            "【柳永执行规则】",
            "- 如有【选定选题】，本次稿件必须围绕该选题展开，选题标题/角度优先于源文档原有标题；",
            "- 爆款原因拆解用于决定开头钩子、受众痛点、结构和语气，不要写成分析报告；",
            "- 源文档仍是事实边界：不得编造源文档未出现的人物、平台、数据。",
        ]
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
    except Exception:  # noqa: BLE001, S110 — 口味注入失败不阻塞改写
        pass
    if reqs.strip():
        parts += [
            "",
            "【用户附加要求 / Leader 口味（最高优先级，可覆盖以上默认要求）】：",
            reqs.strip(),
        ]
    return system_prompt, "\n".join(parts)
