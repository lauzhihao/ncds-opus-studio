"""PipelineRunner -> production engine strangler bridge.

web 当前仍以 `/jobs` / `JobState` 为 UI 契约；本 mixin 只负责把命中的节点
转发到 `InstanceRunner.run_step()`，并把 engine 输出回桥到 facade state。
"""

from __future__ import annotations

from typing import Any

from ncds_opus_core.common import cancel as _cancel


class PipelineEngineBridgeMixin:
    """Engine attach + per-node run_step adapter."""

    def attach_engine(self, engine: Any) -> None:
        """注入生产引擎（InstanceRunner）。由 server/state.py 在两 runner 都建好后调，
        避免 pipeline_runner ↔ state 的 import 环。"""
        self._engine = engine

    async def _execute_via_engine(self, job_id: str, node_name: str) -> dict[str, Any]:
        """绞杀者（E1-b2 #3）：该节点的执行改走生产引擎 ``run_step``——经合并 registry 派发到
        ``final_*`` performer + 引擎状态机。

        JobState 仍是 facade 真相源；引擎实例只作执行载体（throwaway，每 job 复用一个、跑前
        ``reset_step`` 回 idle 支持重跑）。performer 读写同一 ``video-jobs/{job_id}`` 文件，故与未迁
        节点经共享 ``02_rw/episode.json`` 互通。返回引擎步骤的 outputs，由 ``_execute_real`` 落进
        JobState.nodes[node]。仅 ``NOF_ENGINE_NODES`` 命中的（slice-1：无步内增量进度的）节点启用。
        """
        engine = self._engine
        job = self._load(job_id)
        iid = job.engine_iid
        if iid is None or not engine.store.exists(iid):
            iid = engine.create_instance("final_preview", inputs=job.inputs).meta.instance_id
            job.engine_iid = iid          # 持久化句柄，重启后复用同一实例（不留孤儿）
            self._save(job)
        # force：无条件回 idle 重跑。watcher 宽限期后的 asyncio 强制 cancel 只回收 facade 节点、
        # 不碰引擎 step（_execute except asyncio.CancelledError 分支），会留下 orphan running 的引擎步，
        # 之后普通 reset_step 撞「无法重置运行中的步」永久起不来。run_node 的 _running_nodes 守卫已保证
        # 走到这里时旧 task 必 done（旧线程已死），故此处 force 重置 orphan running 是安全的。
        await engine.reset_step(iid, node_name, force=True)
        # 闭合签名：各 final_* performer 不能盲 splat config，driver 按节点装配 step_inputs。
        step_inputs = self._engine_step_inputs(job, node_name)
        # 纯引擎路径无 TaskRunner task_id 时，用实例 iid 作节点追踪句柄。
        # web /jobs 运行时已由 pipeline_node 任务承载，不能再用 iid 覆盖 facade task_id。
        node = job.nodes.get(node_name)
        if getattr(self, "_task_runner", None) is None and node is not None and node.task_id != iid:
            node.task_id = iid
            self._save(job)
        # 把 performer 的 on_progress 回桥到 facade JobState.nodes[node].progress + /jobs SSE，
        # 否则引擎路径下 storyboard/image/render 抽屉 running 态进度文本会冻结。
        st = await engine.run_step(
            iid, node_name, step_inputs,
            on_progress=lambda text: self._push_progress(job_id, node_name, text),
            # 4f：传文件 flag checker 给引擎，让 performer 在 cancel.checkpoint() 处中止
            cancel_check=lambda: _cancel.is_flagged(self._cancel_flag(job_id, node_name)),
        )
        if st.status == "awaiting_review":
            # content_edit 步（lines/storyboard）：facade 无 awaiting 闸（编辑走 episode 端点），自动定稿
            st = await engine.approve_step(iid, node_name, "approved")
        if st.status != "done":
            raise RuntimeError(f"engine step {node_name} -> {st.status}: {st.error or ''}")
        return dict(st.outputs)

    def _engine_step_inputs(self, job: Any, node_name: str) -> dict[str, Any]:
        """为生产引擎 performer 装配该步的 step_inputs。

        performer 是闭合签名（不能盲 splat config，否则 TypeError），故 driver 负责把上游产物/
        全局输入折进 step_inputs（与旧 _execute_rw 的取数口径一致）：
          - asr：urls + shares（inputs 只存了 shares、无顶层 urls 时从 shares 派生 url）
          - rw ：asr_items（asr 节点 outputs.items）；写作由 domain 驱动（引擎从实例 inputs 透传 domain）
          - 其余（lines/storyboard/tts/image/render）：只读 02_rw/episode.json，job_dir 足矣
        """
        si: dict[str, Any] = {"job_dir": str(self.video_jobs_dir / job.job_id)}
        if node_name == "asr":
            shares = list(job.inputs.get("shares") or [])
            urls = [u.strip() for u in (job.inputs.get("urls") or []) if u and u.strip()]
            if not urls:
                urls = [s["url"] for s in shares
                        if isinstance(s, dict) and isinstance(s.get("url"), str) and s["url"].strip()]
            si["urls"] = urls
            si["shares"] = shares
        elif node_name == "rw":
            asr_node = job.nodes.get("asr")
            asr_out = (asr_node.outputs or {}) if asr_node else {}
            si["asr_items"] = list(asr_out.get("collected") or asr_out.get("items") or [])
            # 体裁 profile 已废；写作 domain 由引擎从实例 inputs 透传给 performer，无需在此装配。
        elif node_name == "image":
            si["outputs_patch"] = (
                lambda key, value, jid=job.job_id, n=node_name:
                    self._push_outputs_patch(jid, n, key, value)
            )
        return si
