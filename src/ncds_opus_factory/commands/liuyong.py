"""/liuyong —— 柳永：抖音认知爆款编剧 agent。

输入一个"选题"(一句话想法)，按 douyin_cog 内核(爆款骨架 v2 + 选题公式 + 反模式)
从零创作一条不糙的认知口播脚本。

复用 scripts/content_rewrite_runner.mjs(双模型 + profile 编排)，不碰飞书：
直接喂 sourceText=选题、targetProfile=douyin_cog，本地产出 draft。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.common import ai_taste, quality_rubric, rubric_store
from ncds_opus_factory.common.node_runtime import resolve_node

ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "scripts" / "content_rewrite_runner.mjs"
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("NOF_OGILVY_TIMEOUT", "1800"))

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


def _build_job_id() -> str:
    return f"OGV_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


def _purge_ai_taste(text: str, report: dict, shim: Path, env: dict, timeout_seconds: int) -> str:
    """把质检命中的 AI 味句式甩回模型(走 scodex shim)消除,返回改写后的全文。"""
    lines: list[str] = []
    for h in report.get("density", []):
        lines.append(f'- "{h["rule"]}" 出现 {h["count"]} 次,例:{h.get("samples")}')
    for h in report.get("hard", []):
        lines.append(f'- "{h["rule"]}",例:{h.get("samples")}')
    prompt = (
        "下面这篇抖音口播稿有 AI 味句式问题,请改写消除。\n\n"
        "【必须消除的句式】\n" + "\n".join(lines) + "\n\n"
        "【改写要求】\n"
        "- 把'不是X而是Y/不是X你是Y/X不是A才是B'等否定转折句,改成自然口语的直接陈述\n"
        "- 保持原意、保持骨架和所有具体台词不变,只动这些句式\n"
        "- 不要引入其他 AI 腔(然而/事实上/本质上/归根结底/愿你 等)\n"
        "- 只输出改写后的全文,不要解释、不要标题、不要 markdown\n\n"
        "【原稿】\n" + text
    )
    cmd = [str(shim), "-a", "never", "exec", "--skip-git-repo-check", "--ephemeral",
           "-s", "read-only", "-m", "gpt-5.5", "--json", prompt]
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=timeout_seconds, text=True, capture_output=True)
    out = ""
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = obj.get("item") or {}
        if obj.get("type") == "item.completed" and item.get("type") == "agent_message":
            out = item.get("text", "") or out
    return out.strip()


def run(
    topic: str,
    user_requirements: str = "",
    deliverables_dir: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """围绕 topic 用 douyin_cog 内核生成认知口播脚本。

    返回 {job_id, deliverables_dir, drafts:[{model, path, text}], raw_status}。
    """
    if not RUNNER.exists():
        raise RuntimeError(f"content_rewrite runner 未就绪: {RUNNER}")
    if not topic or not topic.strip():
        raise ValueError("topic(选题)不能为空")

    job_id = _build_job_id()
    ddir = deliverables_dir or str(ROOT / "video-jobs" / job_id / "deliverables")
    # learned rubric 注入(P4,§8.3 第 3 条·唯一注入点):把卧龙复盘归纳的 Leader
    # 口味摘要(≤800 字)附进生成 brief,让柳永一开始就照 Leader 口味写。
    # 不注入自检/质检层——口味混进工艺打分会让三层判据互相污染。
    reqs = user_requirements or ""
    brief = rubric_store.injection_brief()
    if brief:
        reqs = (reqs + "\n\n" if reqs else "") + brief
        on_progress("已注入 Leader 口味备忘(卧龙复盘)")
    payload = {
        "sourceText": topic.strip(),
        "deliverablesDir": ddir,
        "targetProfile": "douyin_cog",
        "userRequirements": reqs,
    }

    # 运行时生成 model 配置：① 登记 openai-codex/gpt-5.5 过 resolveRewriteModels 的 configured 检查；
    # ② 把 codex 的 cliPath 指向 scodex shim(账号感知 + flag 适配)。
    # 写进 deliverables_dir，路径动态正确，不污染 ~/.openclaw 全局配置、不进 git。
    os.makedirs(ddir, exist_ok=True)
    shim = ROOT / "scripts" / "codex_scodex_shim.sh"
    model_config_path = os.path.join(ddir, "model_config.json")
    with open(model_config_path, "w", encoding="utf-8") as fp:
        json.dump(
            {
                "agents": {"defaults": {"models": {"openai-codex/gpt-5.5": {}}}},
                "models": {"providers": {"openai-codex": {"cliPath": str(shim)}}},
            },
            fp,
            ensure_ascii=False,
        )
    payload["modelConfigPath"] = model_config_path

    env = os.environ.copy()
    on_progress(f"柳永启动(job={job_id}) 选题: {topic.strip()[:40]}")
    proc = subprocess.run(
        [resolve_node(), str(RUNNER), json.dumps(payload, ensure_ascii=False)],
        cwd=str(ROOT),
        env=env,
        timeout=timeout_seconds,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"content_rewrite runner 失败(code={proc.returncode}): {proc.stderr[-800:]}")

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"runner 输出非 JSON: {exc}; stdout 末尾: {proc.stdout[-400:]}") from exc

    drafts_out: list[dict[str, Any]] = []
    for draft in result.get("drafts", []) or []:
        if draft.get("status") == "success" and draft.get("path") and os.path.exists(draft["path"]):
            text = Path(draft["path"]).read_text(encoding="utf-8")
            drafts_out.append(
                {"model": draft.get("modelLabel") or draft.get("modelId"), "path": draft["path"], "text": text}
            )

    # === 质检闸门:扫 AI 味 -> 命中就打回重写,直到 pass 或达上限 ===
    for d in drafts_out:
        report = ai_taste.scan(d["text"])
        on_progress(f"质检[{d['model']}]: {report['verdict']} - {report['summary']}")
        rounds = 0
        while report["verdict"] == "fail" and rounds < 2:
            rounds += 1
            on_progress(f"  AI 味超标,打回重写第 {rounds} 轮...")
            new_text = _purge_ai_taste(d["text"], report, shim, env, timeout_seconds)
            if not new_text or len(new_text) < 200:
                on_progress("  重写未返回有效稿,保留上一版")
                break
            d["text"] = new_text
            report = ai_taste.scan(new_text)
            on_progress(f"  第 {rounds} 轮后: {report['verdict']} - {report['summary']}")
        d["qc"] = report
        # 第 2 层:rubric 正向质量分(opus 他评,仅标注不打回,校准期)
        rub = quality_rubric.score(d["text"], timeout_seconds=timeout_seconds)
        d["qc_rubric"] = rub
        if rub.get("available"):
            on_progress(f"质检2[rubric/opus]: {rub['total']}/50 {rub['grade']}")
        else:
            on_progress(f"质检2[rubric]: 跳过({rub.get('skipped')})")
        if d.get("path"):
            Path(d["path"]).write_text(d["text"], encoding="utf-8")
            # qc sidecar:质检结果旁挂,不污染正文 .md
            qc_path = Path(d["path"]).with_suffix(".qc.json")
            qc_path.write_text(
                json.dumps({"qc": report, "qc_rubric": rub}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    on_progress(f"柳永完成: {len(drafts_out)} 稿成功")
    return {
        "job_id": job_id,
        "deliverables_dir": ddir,
        "drafts": drafts_out,
        "raw_status": result.get("status"),
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof liuyong", description="柳永: 抖音认知爆款编剧 agent")
    parser.add_argument("--topic", required=True, help="选题(一句话想法)")
    parser.add_argument("--requirements", default="", help="附加创作要求")
    parser.add_argument("--deliverables-dir", default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        topic=args.topic,
        user_requirements=args.requirements,
        deliverables_dir=args.deliverables_dir,
        timeout_seconds=args.timeout,
        on_progress=on_progress,
    )
    for draft in result["drafts"]:
        print(f"\n===== {draft['model']} =====\n{draft['text']}")
    print(
        json.dumps(
            {"job_id": result["job_id"], "deliverables_dir": result["deliverables_dir"], "draft_count": len(result["drafts"])},
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
