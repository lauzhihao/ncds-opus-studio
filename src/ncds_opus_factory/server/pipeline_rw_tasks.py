"""RW node execution helpers for :mod:`pipeline_runner`."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def build_rw_draft(
    *,
    rw_root: Path,
    cand: dict[str, str],
    res: Any,
    model_unavailable_cls: type[BaseException],
    on_progress: Callable[[str], None],
) -> dict[str, Any]:
    """把单模型成功结果转成 draft dict 并写盘；异常/空结果由调用方静默跳过。"""
    mid, label = cand["id"], cand["label"]
    if isinstance(res, model_unavailable_cls):
        return {"model_id": mid, "label": label, "status": "failed",
                "reason": "模型不可用", "draft_relpath": None, "episode_relpath": None}
    if isinstance(res, BaseException):
        return {"model_id": mid, "label": label, "status": "failed",
                "reason": "模型调用失败", "draft_relpath": None, "episode_relpath": None}
    raw_text = (res or "").strip()
    if raw_text.startswith("```"):
        inner = re.match(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$", raw_text)
        if inner:
            raw_text = inner.group(1).strip()
    if not raw_text:
        return {"model_id": mid, "label": label, "status": "failed",
                "reason": "模型输出为空", "draft_relpath": None, "episode_relpath": None}
    model_dir = rw_root / mid
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "draft.md").write_text(raw_text + "\n", encoding="utf-8")
    on_progress(f"模型 {mid} draft 写盘完成（{len(raw_text)} 字）")
    return {"model_id": mid, "label": label, "status": "success", "reason": None,
            "draft_relpath": f"02_rw/{mid}/draft.md", "episode_relpath": None}


@dataclass
class PipelineRwRun:
    """`PipelineRunner._execute_rw` 的一次多模型运行上下文。"""

    runner: Any
    job_id: str
    rw_root: Path
    source_text: str
    system_prompt: str
    user_prompt: str
    model_candidates: list[dict[str, str]]
    model_unavailable_cls: type[BaseException]
    invoke_rw_candidate: Callable[
        [dict[str, str], str, str, Callable[[str], None], Callable[[str, str], None]],
        Awaitable[Any],
    ]
    apply_rw_qc: Callable[[Path, str, Callable[[str], None]], dict[str, Any]]
    model_status: dict[str, dict[str, Any]] = field(init=False)
    drafts_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 模型级状态：pending → running → done | failed | unavailable。
        self.model_status = {
            cand["id"]: {"model_id": cand["id"], "label": cand["label"], "status": "pending"}
            for cand in self.model_candidates
        }

    def on_progress(self, text: str) -> None:
        self.runner._push_progress(self.job_id, "rw", text)

    def push_status(self, model_id: str, st: str) -> None:
        if model_id in self.model_status:
            self.model_status[model_id]["status"] = st
        self.runner._push_model_progress(
            self.job_id, "rw", {k: dict(v) for k, v in self.model_status.items()},
        )

    def ordered_drafts(self) -> list[dict[str, Any]]:
        return [
            self.drafts_by_id[c["id"]]
            for c in self.model_candidates
            if c["id"] in self.drafts_by_id
        ]

    def push_drafts(self) -> None:
        self.runner._push_outputs_patch(self.job_id, "rw", "drafts", self.ordered_drafts())

    def make_draft(self, cand: dict[str, str], res: Any) -> dict[str, Any]:
        return build_rw_draft(
            rw_root=self.rw_root,
            cand=cand,
            res=res,
            model_unavailable_cls=self.model_unavailable_cls,
            on_progress=self.on_progress,
        )

    async def run_one(self, cand: dict[str, str]) -> None:
        try:
            res: Any = await self.invoke_rw_candidate(
                cand, self.user_prompt, self.system_prompt, self.on_progress, self.push_status,
            )
        except BaseException as exc:  # noqa: BLE001 — 单模型失败静默跳过，不污染前端 drafts
            if isinstance(exc, self.model_unavailable_cls):
                return
            return
        draft = self.make_draft(cand, res)
        if draft.get("status") != "success":
            return
        # 柳永质检闸门：ai_taste 打回重写 + rubric 评分（同 liuyong.py）。同步 helper 放 to_thread。
        try:
            draft.update(await asyncio.to_thread(
                self.apply_rw_qc, self.rw_root / cand["id"], cand["id"], self.on_progress,
            ))
        except Exception as exc:  # noqa: BLE001 — 质检失败不拖垮出稿
            self.on_progress(f"  [{cand['id']}] 质检异常（不影响稿件）: {exc}")
        self.drafts_by_id[cand["id"]] = draft
        self.push_drafts()  # 这个模型一好就立即渲染到前端

    async def run(self) -> dict[str, Any]:
        self.on_progress(
            f"{len(self.model_candidates)} 模型并行启动；source={len(self.source_text)} 字"
        )
        # 先推一帧全 pending，让前端立刻看到每个模型一行。
        self.runner._push_model_progress(
            self.job_id, "rw", {k: dict(v) for k, v in self.model_status.items()},
        )

        # 增量产物：每个模型完成立即写盘 + push drafts，前端不必等全部完成才能看/选。
        await asyncio.gather(*[self.run_one(c) for c in self.model_candidates])

        drafts_out = self.ordered_drafts()
        success_count = sum(1 for d in drafts_out if d.get("status") == "success")
        if success_count == 0:
            raise RuntimeError("当前没有模型成功出稿，请稍后重试或检查模型配置")

        self.on_progress(f"完成：{success_count}/{len(self.model_candidates)} 成功")
        return {
            "drafts": drafts_out,
            "selected_model_id": None,
            "candidate_count": len(drafts_out),
            "success_count": success_count,
        }
