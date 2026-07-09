"""RW drawer operations shared by PipelineRunner routes."""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from dataclasses import asdict
from typing import Any

from ncds_opus_core.pipelines import get_pipeline

from ncds_opus_factory.server import pipeline_rw_helpers as rw_helpers


class PipelineRwOperationsMixin:
    """RW model selection, rewrite, and rubric refinement operations."""

    def _assert_known_model(
        self,
        model_id: str,
        *,
        missing_message: str | None = None,
    ) -> dict[str, Any]:
        """返回 MODEL_CANDIDATES 中的候选；缺失时按既有语义抛 KeyError。"""
        cand = next((c for c in rw_helpers.MODEL_CANDIDATES if c["id"] == model_id), None)
        if cand is None:
            raise KeyError(missing_message or f"unknown model: {model_id}")
        return cand

    async def _rw_mock_short_circuit(self, state: Any, job_id: str, model_id: str) -> bool:
        """mock 作品的 RW 单模型操作短路。

        rewrite/refine 在演示作品下都不真调 LLM，只模拟耗时并重发 rw 节点状态。
        非 mock 返回 False，让调用方继续真实路径。
        """
        if not state.mock:
            return False
        # mock 同样校验 model_id：否则 bogus 模型也会静默返回 200，与真实路径行为不一致。
        self._assert_known_model(model_id)
        await self._mock_regen_delay()
        n = state.nodes.get("rw")
        if n is not None:
            self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "rw", "state": asdict(n)})
        return True

    async def rewrite_rw_model(self, job_id: str, model_id: str) -> None:
        """重写 rw 某个模型的 draft（保留其他模型不动）。"""
        state = self._load(job_id)
        if await self._rw_mock_short_circuit(state, job_id, model_id):
            return
        n = state.nodes.get("rw")
        if n is None:
            raise KeyError("rw node not found")
        if n.status != "done":
            raise ValueError("rw node not done; run rw first")
        drafts = (n.outputs or {}).get("drafts") or []
        entry = next(
            (d for d in drafts if isinstance(d, dict) and d.get("model_id") == model_id), None
        )
        if entry is None:
            raise KeyError(f"unknown model: {model_id}")
        cand = self._assert_known_model(
            model_id,
            missing_message=f"model {model_id} not in MODEL_CANDIDATES",
        )

        # 重新拼 sourceText（同 _execute_rw）
        asr_node = state.nodes.get("asr")
        if asr_node is None or asr_node.status != "done":
            raise ValueError("asr node not done; cannot rewrite")
        job_dir = self.video_jobs_dir / job_id
        asr_out = asr_node.outputs or {}
        asr_items = list(asr_out.get("collected") or asr_out.get("items") or [])
        source_text = rw_helpers._rw_source_text(asr_items, job_dir)
        if not source_text:
            raise RuntimeError("asr 采集文案全部为空，无法 rw")

        domain_guidance = rw_helpers._rw_domain_guidance(state.inputs.get("domain"))
        rw_config = state.node_configs.get("rw") or {}
        guiguzi_context = rw_helpers._rw_guiguzi_context(
            rw_config, job_dir, domain=state.inputs.get("domain")
        )
        system_prompt, user_prompt = rw_helpers._build_rw_prompt(
            source_text,
            domain_guidance=domain_guidance,
            guiguzi_context=guiguzi_context,
        )

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "rw", f"[rerun {model_id}] {text}")

        on_progress("单模型重跑启动")
        try:
            raw_text = await rw_helpers._invoke_rw_candidate(cand, user_prompt, system_prompt, on_progress)
        except rw_helpers._ModelUnavailable:
            return
        except Exception:  # noqa: BLE001 — 单模型失败静默跳过，不污染前端
            return

        cleaned = (raw_text or "").strip()
        if cleaned.startswith("```"):
            inner = re.match(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$", cleaned)
            if inner:
                cleaned = inner.group(1).strip()
        if not cleaned:
            return

        rw_root = job_dir / "02_rw"
        model_dir = rw_root / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "draft.md").write_text(cleaned + "\n", encoding="utf-8")

        try:
            qc = await asyncio.to_thread(rw_helpers._apply_rw_qc, model_dir, model_id, on_progress)
            entry.update(qc)
        except Exception as exc:  # noqa: BLE001 — 质检失败不拖垮重写稿
            on_progress(f"质检异常（不影响稿件）: {exc}")

        if (n.outputs or {}).get("selected_model_id") == model_id:
            shutil.copyfile(model_dir / "draft.md", rw_root / "draft.md")
            for dn in get_pipeline(state.pipeline_id).downstream_of("rw"):
                if state.nodes[dn].status != "idle":
                    self._reset_node(state.nodes[dn])

        state.updated_at = time.time()
        self._save(state)
        self._emit(
            job_id,
            {"type": "node_status", "job_id": job_id, "node": "rw", "state": asdict(n)},
        )

    async def refine_rw_model(self, job_id: str, model_id: str) -> None:
        """按 rubric 质检建议优化 rw 某模型的当前 draft。"""
        state = self._load(job_id)
        if await self._rw_mock_short_circuit(state, job_id, model_id):
            return
        n = state.nodes.get("rw")
        if n is None:
            raise KeyError("rw node not found")
        if n.status != "done":
            raise ValueError("rw node not done; run rw first")
        drafts = (n.outputs or {}).get("drafts") or []
        entry = next(
            (d for d in drafts if isinstance(d, dict) and d.get("model_id") == model_id), None
        )
        if entry is None:
            raise KeyError(f"unknown model: {model_id}")
        if entry.get("status") == "failed":
            raise ValueError("失败的模型无法优化")
        issues = rw_helpers._ai_taste_issue_lines(entry.get("qc") or {})
        issues.extend(str(x) for x in (entry.get("qc_rubric") or {}).get("issues") or [])
        if not issues:
            raise ValueError("当前稿没有可用的优化建议")

        job_dir = self.video_jobs_dir / job_id
        rw_root = job_dir / "02_rw"
        model_dir = rw_root / model_id
        draft_path = model_dir / "draft.md"
        if not draft_path.is_file():
            raise FileNotFoundError("draft.md 不存在，无法优化")
        text = draft_path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("当前稿为空")

        from ncds_opus_factory.common import quality_rubric

        def on_progress(msg: str) -> None:
            self._push_progress(job_id, "rw", f"[refine {model_id}] {msg}")

        on_progress(f"按 {len(issues)} 条建议优化启动")
        refined = await asyncio.to_thread(quality_rubric.refine, text, issues, prefer_models={model_id})
        if not refined or len(refined.strip()) < 200:
            raise ValueError("优化未返回有效稿（已保留原稿）")
        draft_path.write_text(refined.strip() + "\n", encoding="utf-8")
        on_progress("优化稿写盘完成，重跑质检")

        try:
            qc = await asyncio.to_thread(rw_helpers._apply_rw_qc, model_dir, model_id, on_progress)
            entry.update(qc)
        except Exception as exc:  # noqa: BLE001 — 质检失败不拖垮优化稿
            on_progress(f"质检异常（不影响稿件）: {exc}")

        if (n.outputs or {}).get("selected_model_id") == model_id:
            shutil.copyfile(draft_path, rw_root / "draft.md")
            for dn in get_pipeline(state.pipeline_id).downstream_of("rw"):
                if state.nodes[dn].status != "idle":
                    self._reset_node(state.nodes[dn])

        state.updated_at = time.time()
        self._save(state)
        self._emit(
            job_id,
            {"type": "node_status", "job_id": job_id, "node": "rw", "state": asdict(n)},
        )

    def select_rw_model(self, job_id: str, model_id: str) -> None:
        """用户在 rw 抽屉里选中某模型作为定稿入口。"""
        state = self._load(job_id)
        n = state.nodes.get("rw")
        if n is None:
            raise KeyError("rw node not found")
        if n.status != "done":
            raise ValueError("rw node not done")
        drafts = (n.outputs or {}).get("drafts") or []
        valid_ids = {
            d.get("model_id")
            for d in drafts
            if isinstance(d, dict) and d.get("status") != "failed"
        }
        if model_id not in valid_ids:
            raise ValueError(f"unknown model or failed model: {model_id}")
        out_dir = self.video_jobs_dir / job_id / "02_rw"
        src = out_dir / model_id / "draft.md"
        dst = out_dir / "draft.md"
        if not src.exists():
            raise FileNotFoundError(f"missing source draft: {src}")

        from ncds_opus_factory.common import ai_taste

        report = ai_taste.scan(src.read_text(encoding="utf-8"))
        entry = next(
            (d for d in drafts if isinstance(d, dict) and d.get("model_id") == model_id), None
        )
        if entry is not None:
            entry["qc"] = report
            entry["needs_fix"] = report.get("verdict") == "fail"
        if report.get("verdict") == "fail":
            state.updated_at = time.time()
            self._save(state)
            self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "rw", "state": asdict(n)})
            summary = str(report.get("summary") or "AI 味未通过")
            raise ValueError(f"质检未通过：{summary}。请先优化后再定稿")

        dst.write_bytes(src.read_bytes())
        n.outputs["selected_model_id"] = model_id
        state.updated_at = time.time()
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "rw", "state": asdict(n)})
