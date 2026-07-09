"""PipelineRunner 的 agent 后台任务 mixin。

这些任务与 final_preview 节点执行体正交：ASR done 后的沈括 enrich、进画布后的沈括刷新、
以及虚拟 agent 鬼谷子的分析/出题后台任务。它们仍通过 PipelineRunner 的 store/event
helper 读写状态；本文件只把方法从 god-object 中机械搬出，不改变运行时契约。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from ncds_opus_core.common import cancel as _cancel
from ncds_opus_factory.server.pipeline_asr_tasks import is_transcript_only_collect

logger = logging.getLogger(__name__)


class PipelineAgentTasksMixin:
    """ASR enrich / Shenkuo refresh / Guiguzi background task methods."""

    def _spawn_asr_enrich(self, job_id: str) -> None:
        """asr 快采 done 后，后台补抠图等素材（音轨已在首趟采集，二趟幂等确认）。
        重跑 asr 前先 cancel 旧 enrich task，避免两趟竞态写 outputs.collected。"""
        try:
            state = self._load(job_id)
        except FileNotFoundError:
            return
        if is_transcript_only_collect(state.inputs):
            self._push_progress(job_id, "asr", "沈括纯文案模式：跳过音轨/抠图后台补齐")
            return
        old = self._enrich_tasks.get(job_id)
        if old is not None and not old.done():
            old.cancel()
        self._enrich_tasks[job_id] = asyncio.create_task(self._enrich_asr_collected(job_id))

    async def _enrich_asr_collected(self, job_id: str) -> None:
        """后台第二趟采集：对 asr.outputs.collected 每条作品 collect_one(do_audio/do_frames=True)
        补抠图并幂等确认音轨（transcribe/comments 已 cached 跳过），字段级合并回 collected 并增量推送。
        失败不影响主链路（快采产物已能驱动下游 rw）。"""
        from ncds_opus_factory.commands import shenkuo

        try:
            state = self._load(job_id)
        except FileNotFoundError:
            return
        asr = state.nodes.get("asr")
        if asr is None:
            return
        collected = list((asr.outputs or {}).get("collected") or [])
        if not collected:
            return
        collect_dir = self.video_jobs_dir / job_id / "01_collect"

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "asr", text)

        flag_path = self._cancel_flag(job_id, "asr")
        for entry in collected:
            if entry.get("error") or not entry.get("aweme_id"):
                continue
            try:
                full = await self._run_in_thread_cancellable(
                    shenkuo.collect_one, flag_path,
                    entry["aweme_id"], collect_dir,
                    meta={}, on_progress=on_progress,
                    do_audio=True, do_frames=True,
                    platform=entry.get("platform") or "douyin",
                    source_url=entry.get("url") if isinstance(entry.get("url"), str) else None,
                )
            except asyncio.CancelledError:
                raise
            except _cancel.TaskCancelled:
                # 取消信号到达：让 enrich task 正常终止，不影响已有快采产物
                raise
            except Exception as exc:  # noqa: BLE001 — 后台补失败不影响主链路
                on_progress(f"[{entry.get('index')}] 音轨/抠图后台补失败（不影响主链路）：{exc}")
                continue
            # 字段级合并：保留快采的展示字段（desc/stats/text/top_comments/cover），补重活产物。
            entry["audio"] = full.get("audio")
            entry["frames"] = full.get("frames")
            entry["cutouts"] = full.get("cutouts")
            entry["status"] = {**entry.get("status", {}), **full.get("status", {})}
            self._push_outputs_patch(job_id, "asr", "collected", collected)
            on_progress(f"[{entry.get('index')}] 音轨/抠图已补齐")

    def refresh_shenkuo(self, job_id: str) -> bool:
        """进画布触发：后台刷新沈括已采作品的播放数据 + 评论（其余产物不动）。

        no-op 的几种情况（返回 False）：job 没 asr / asr 还没采出 collected（首采由
        asr 节点负责）/ asr 正在采（running/queued，在采的数据已最新）/ 同 job 已有刷新在跑。
        真起了后台刷新 task 才返回 True。逐条作品的「1 小时内只采一次」节流 + 防并发由
        _refresh_shenkuo_collected 里的 Redis 锁把关。
        """
        try:
            state = self._load(job_id)
        except FileNotFoundError as e:
            raise KeyError(job_id) from e
        if is_transcript_only_collect(state.inputs):
            return False
        asr = state.nodes.get("asr")
        if asr is None or asr.status in ("running", "queued"):
            return False
        collected = list((asr.outputs or {}).get("collected") or [])
        if not collected:
            return False
        old = self._refresh_tasks.get(job_id)
        if old is not None and not old.done():
            return False  # 同 job 已在刷新，去重
        self._refresh_tasks[job_id] = asyncio.create_task(self._refresh_shenkuo_collected(job_id))
        return True

    async def _refresh_shenkuo_collected(self, job_id: str) -> None:
        """后台刷新：逐条对 asr.outputs.collected 重取播放数据 + 评论，字段级合并回 entry 并增量推送。

        节流/防并发：每条作品采集前先抢 Redis 锁（nof:shenkuo:refresh:{aweme_id}，SET NX EX
        1h）——抢到才调 tikhub 接口，抢不到（1 小时内已刷过 / 别处正在刷）直接跳过省 API 成本。
        单条失败/跳过不影响其它条；全程不碰文案/音轨/抠图等重产物。
        """
        from ncds_opus_factory.commands import shenkuo
        from ncds_opus_factory.server.queue import get_default_queue

        try:
            state = self._load(job_id)
        except FileNotFoundError:
            return
        asr = state.nodes.get("asr")
        if asr is None:
            return
        collected = list((asr.outputs or {}).get("collected") or [])
        if not collected:
            return
        queue = get_default_queue()

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "asr", text)

        for entry in collected:
            aweme_id = entry.get("aweme_id")
            if entry.get("error") or not aweme_id:
                continue
            if not await queue.acquire_shenkuo_refresh(aweme_id):
                on_progress(f"[{entry.get('index')}] 1 小时内已刷新，跳过（省 API）")
                continue
            platform = entry.get("platform") or "douyin"
            try:
                patch = await asyncio.to_thread(
                    shenkuo.refresh_stats_comments, aweme_id, platform,
                    on_progress=on_progress,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 后台刷新失败不影响主链路
                on_progress(f"[{entry.get('index')}] 数据/评论刷新失败（不影响）：{exc}")
                continue
            if not patch:
                continue
            # 字段级合并：只覆盖 stats/digg/comments/top_comments，保留文案/音轨/抠图/封面等。
            entry.update(patch)
            self._push_outputs_patch(job_id, "asr", "collected", collected)
            on_progress(f"[{entry.get('index')}] 数据/评论已刷新")

    def _guiguzi_path(self, job_id: str) -> Path:
        return self.video_jobs_dir / job_id / "guiguzi.json"

    def get_guiguzi(self, job_id: str) -> dict[str, Any] | None:
        """读 per-job 选题结果 guiguzi.json（无则 None）。前端轮询此接口取 running/done。

        检测「僵尸 running」：旧 server 崩溃后 guiguzi.json 残留 running 态但
        _guiguzi_tasks 无对应后台任务，直接删文件让前端回到未开始状态。
        """
        p = self._guiguzi_path(job_id)
        if not p.exists():
            return None
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        task = self._guiguzi_tasks.get(job_id)
        if task is None or task.done():
            # 无存活后台任务 → 若是终态（done/analyzed）正常返回，否则删文件
            if doc.get("status") in ("done", "analyzed"):
                return doc
            p.unlink(missing_ok=True)
            return None
        return doc

    def _write_guiguzi(self, job_id: str, doc: dict[str, Any]) -> None:
        p = self._guiguzi_path(job_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_guiguzi_selection(self, job_id: str, topic: dict[str, Any], analysis: dict[str, Any] | None = None) -> None:
        """把用户最终选中的鬼谷子选题落到 guiguzi.json，供柳永重跑时服务端兜底读取。"""
        if not isinstance(topic, dict) or not str(topic.get("title") or "").strip():
            return
        doc = self.get_guiguzi(job_id) or {}
        doc["chosen_topic"] = topic
        doc["chosen_title"] = str(topic.get("title") or "").strip()
        if isinstance(analysis, dict) and analysis:
            doc["chosen_analysis"] = analysis
        doc["updated_at"] = time.time()
        self._write_guiguzi(job_id, doc)

    @staticmethod
    def _norm_guiguzi_items(items: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {"text": str(it.get("text") or "").strip(), "comment": str(it.get("comment") or "").strip()}
            for it in (items or [])
            if isinstance(it, dict) and str(it.get("comment") or "").strip()
        ]

    def _guiguzi_source_items(self, job_id: str) -> list[dict[str, str]]:
        """无评论「直接拆解」:从沈括采集(asr.collected)取各作品原文(提取文案),
        构造无评论 items [{text, comment:''}],截断到 MAX_ITEMS。无原文则返回空。"""
        from ncds_opus_factory.commands.guiguzi import MAX_ITEMS
        try:
            state = self._load(job_id)
        except FileNotFoundError:
            return []
        asr_node = state.nodes.get("asr")
        asr_out = (asr_node.outputs if asr_node else None) or {}
        collected = list(asr_out.get("collected") or asr_out.get("items") or [])
        out: list[dict[str, str]] = []
        for e in collected:
            if not isinstance(e, dict):
                continue
            text = str(e.get("text") or "").strip()
            if text:
                out.append({"text": text, "comment": ""})
            if len(out) >= MAX_ITEMS:
                break
        return out

    def _guiguzi_progress(self, job_id: str):
        def on_progress(text: str) -> None:
            # 工作线程内调：只碰文件(线程安全),不碰 asyncio 总线。更新 running doc 的 progress。
            cur = self.get_guiguzi(job_id)
            if cur and cur.get("status") == "running":
                cur["progress"] = text
                cur["updated_at"] = time.time()
                self._write_guiguzi(job_id, cur)
        return on_progress

    def analyze_guiguzi(self, job_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """第一步：双模型并行反推爆款原因。立即返回 analyzing doc，后台跑，前端轮询取 analyzed。"""
        try:
            self._load(job_id)
        except FileNotFoundError as e:
            raise KeyError(job_id) from e
        norm = self._norm_guiguzi_items(items)
        no_comments = False
        if not norm:
            # 无评论「直接拆解」:回退到沈括采集的全部作品原文,仅凭原文分析爆款原因。
            norm = self._guiguzi_source_items(job_id)
            no_comments = True
            if not norm:
                raise ValueError("没有可分析的素材:请先在沈括采集,或选 1-5 条高赞评论")
        # 取消旧 task（如果有），始终重置状态
        old = self._guiguzi_tasks.get(job_id)
        if old is not None and not old.done():
            old.cancel()
        # 重新分析 → 重置分析与选题(分析变了,旧题作废)
        doc = {"stage": "analyzing", "status": "running", "items": norm, "analysis": None,
               "chosen_analysis": None, "candidates": None, "topics": None, "out": None,
               "error": None, "progress": "分析爆款原因中…", "updated_at": time.time()}
        self._write_guiguzi(job_id, doc)
        self._guiguzi_tasks[job_id] = asyncio.create_task(
            self._run_guiguzi_analyze_bg(job_id, norm, no_comments))
        return doc

    async def _run_guiguzi_analyze_bg(
        self, job_id: str, items: list[dict[str, Any]], no_comments: bool = False,
    ) -> None:
        from ncds_opus_factory.commands import guiguzi
        from ncds_opus_factory.server.mock_agents import mock_guiguzi_analyze

        try:
            state = self._load(job_id)
        except FileNotFoundError:
            return
        mock = state.mock
        # 选题领域软背景：作品 domain 由前端 doCreate 放进 job inputs（task-2.3），
        # 这里透传给鬼谷子作软背景（仅受众/调性/禁区参考，不预设赛道）。空则回退通用。
        domain = state.inputs.get("domain")
        on_progress = self._guiguzi_progress(job_id)
        try:
            if mock:
                from ncds_opus_factory.server import mock as mock_mod
                analyses = await asyncio.to_thread(
                    lambda: mock_mod.reference_guiguzi_analysis(self.video_jobs_dir, on_progress)
                    or mock_guiguzi_analyze(on_progress)
                )
            else:
                analyses = await asyncio.to_thread(
                    guiguzi.analyze, items, on_progress=on_progress,
                    require_comment=not no_comments, domain=domain)
            doc = {"stage": "analyzed", "status": "analyzed", "items": items,
                   "analysis": analyses, "chosen_analysis": None, "candidates": None,
                   "topics": None, "out": None, "error": None,
                   "progress": "", "updated_at": time.time()}
        except Exception as exc:  # noqa: BLE001 — 失败收成 doc.error,不冒泡
            doc = {"stage": "failed", "status": "failed", "items": items, "analysis": None,
                   "chosen_analysis": None, "candidates": None, "topics": None, "out": None,
                   "error": f"{type(exc).__name__}: {exc}", "progress": "", "updated_at": time.time()}
            logger.warning("[pipeline] guiguzi analyze failed for %s: %s", job_id, exc)
        self._write_guiguzi(job_id, doc)
        self._emit(job_id, {"type": "job_updated", "job_id": job_id})

    def generate_guiguzi(self, job_id: str, items: list[dict[str, Any]],
                         analysis: dict[str, Any] | None, prompt: str | None = None,
                         force: bool = False) -> dict[str, Any]:
        """第二步：以(用户选定/编辑的)analysis 出选题。

        prompt 传入(用户编辑后的提示词模板,含 $source)则用它,且按全量重出(自定义 prompt 不做增量)。
        prompt 为空时按 analysis 拼默认模板:force=False（增量,只为新评论补题）/ True（全部重出）。mock 一律全量。
        """
        from ncds_opus_factory.commands.guiguzi import MODELS as GUIGUZI_MODELS

        try:
            mock = self._load(job_id).mock
        except FileNotFoundError as e:
            raise KeyError(job_id) from e
        norm = self._norm_guiguzi_items(items)
        no_comments = False
        if not norm:
            # 无评论「直接拆解」:回退到沈括采集的全部作品原文,仅凭原文+分析自由出题。
            norm = self._guiguzi_source_items(job_id)
            no_comments = True
            if not norm:
                raise ValueError("没有可出题的素材:请先在沈括采集,或选 1-5 条高赞评论")
        # 取消旧 task（如果有），始终重置
        old = self._guiguzi_tasks.get(job_id)
        if old is not None and not old.done():
            old.cancel()

        has_prompt = bool(prompt and prompt.strip())
        existing = self.get_guiguzi(job_id) or {}
        existing_cands = existing.get("candidates") or {}
        model_ids = list(GUIGUZI_MODELS)
        if isinstance(existing_cands, dict):
            for m in existing_cands:
                if m not in model_ids:
                    model_ids.append(m)
        base: dict[str, list[dict[str, Any]]] = {m: [] for m in model_ids}
        gen = norm
        # 自定义 prompt 总是全量(prompt 已把 N 条评论拼死);否则按 force 决定增量。
        # 无评论「直接拆解」一律全量(增量靠评论 anchor 比对,不适用)。
        if not no_comments and not force and not has_prompt and not mock and existing_cands:
            cur_comments = {it["comment"] for it in norm}
            covered: set[str] = set()
            for m in model_ids:
                kept = [t for t in ((existing_cands.get(m) or {}).get("topics") or [])
                        if t.get("anchor_comment") in cur_comments]
                base[m] = kept
                covered |= {t.get("anchor_comment") for t in kept}
            gen = [it for it in norm if it["comment"] not in covered]

        # generating 期间保留已有的双栏分析(analysis)，记录本次选定 chosen_analysis
        doc = {"stage": "generating", "status": "running", "items": norm,
               "analysis": existing.get("analysis"), "chosen_analysis": analysis,
               "candidates": existing.get("candidates"), "topics": existing.get("topics"),
               "prompt": existing.get("prompt"), "out": existing.get("out"), "error": None,
               "progress": "出题中…", "updated_at": time.time()}
        self._write_guiguzi(job_id, doc)
        self._guiguzi_tasks[job_id] = asyncio.create_task(
            self._run_guiguzi_generate_bg(job_id, norm, gen, base, analysis,
                                          prompt if has_prompt else None, no_comments))
        return doc

    async def _run_guiguzi_generate_bg(
        self, job_id: str, full_items: list[dict[str, Any]], gen_items: list[dict[str, Any]],
        base: dict[str, list[dict[str, Any]]], analysis: dict[str, Any] | None,
        prompt: str | None, no_comments: bool = False,
    ) -> None:
        """后台：对 gen_items 跑 guiguzi.generate_topics（analysis/prompt 指导），与 base 合并，写 guiguzi.json。"""
        from ncds_opus_factory.commands import guiguzi
        from ncds_opus_factory.server.mock_agents import mock_guiguzi_topics

        try:
            state = self._load(job_id)
        except FileNotFoundError:
            return
        mock = state.mock
        # 选题领域软背景，同 analyze：从 job inputs 取 domain 透传（空则回退通用）。
        domain = state.inputs.get("domain")
        on_progress = self._guiguzi_progress(job_id)
        kept_analysis = (self.get_guiguzi(job_id) or {}).get("analysis")
        used_prompt = (self.get_guiguzi(job_id) or {}).get("prompt")
        try:
            out = None
            if gen_items:
                if mock:
                    from ncds_opus_factory.server import mock as mock_mod
                    result = await asyncio.to_thread(
                        lambda: mock_mod.reference_guiguzi_topics(self.video_jobs_dir, on_progress)
                        or mock_guiguzi_topics(on_progress)
                    )
                else:
                    result = await asyncio.to_thread(
                        guiguzi.generate_topics, gen_items, analysis, prompt,
                        on_progress=on_progress, require_comment=not no_comments, domain=domain)
                new_cands = result.get("candidates") or {}
                out = result.get("out")
                used_prompt = result.get("prompt") or used_prompt  # 回填本次模板(含 $source)供前端回显
            else:
                # 纯移除（没有新评论）：无需跑模型，只落保留的旧题。
                new_cands = {}
            model_ids = list(guiguzi.MODELS)
            for source in (base, new_cands):
                for m in source:
                    if m not in model_ids:
                        model_ids.append(m)
            candidates: dict[str, Any] = {}
            for m in model_ids:
                new_m = new_cands.get(m) or {"topics": [], "error": None}
                candidates[m] = {
                    "topics": list(base.get(m) or []) + list(new_m.get("topics") or []),
                    "error": new_m.get("error"),
                }
            flat = [t for m in model_ids for t in candidates[m]["topics"]]
            doc = {"stage": "done", "status": "done", "items": full_items,
                   "analysis": kept_analysis, "chosen_analysis": analysis,
                   "candidates": candidates, "topics": flat, "prompt": used_prompt,
                   "out": out, "error": None, "progress": "", "updated_at": time.time()}
        except Exception as exc:  # noqa: BLE001 — 失败收成 doc.error,前端展示,不冒泡
            doc = {"stage": "failed", "status": "failed", "items": full_items,
                   "analysis": kept_analysis, "chosen_analysis": analysis,
                   "candidates": None, "topics": None, "prompt": used_prompt, "out": None,
                   "error": f"{type(exc).__name__}: {exc}", "progress": "", "updated_at": time.time()}
            logger.warning("[pipeline] guiguzi generate failed for %s: %s", job_id, exc)
        self._write_guiguzi(job_id, doc)
        self._emit(job_id, {"type": "job_updated", "job_id": job_id})
