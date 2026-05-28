"""Pipeline runner：把固定步骤的 DAG 跑成"每节点一个任务 + 状态机 + SSE"的形态。

设计要点
--------
- 每个 job 对应一个 `video-jobs/{job_id}/pipeline_state.json`，作为唯一真相源。
- 节点状态 idle → queued → running → done | failed；重跑某节点会把它及所有下游
  节点 reset 回 idle（产物保留磁盘上由用户决定是否清理）。
- 真实执行：spawn 一个 asyncio task，调 commands/<cmd>.run；进度通过 on_progress
  推进 state.nodes[x].progress 字段 + 落盘 + SSE publish。
- 状态变更广播给内存 SSE pub/sub，订阅者从 asyncio.Queue 读事件。

接入进度（截至当前）：
- tts、image：已真接入（DashScope CosyVoice / gpt-image-2）
- asr：已真接入（spawn skills/video-pipeline/video_pipeline.py，只产 transcript + polished 清洗稿；爆款精华已下放到 rw 节点）
- rw：已真接入（spawn scripts/content_rewrite_runner.mjs，paper_card_talk profile）
- render：已真接入（commands/render_014.run）
- lines：UI-only，不在此处执行
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.pipelines import PIPELINE_REGISTRY, PipelineDef, get_pipeline

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 状态模型
# ---------------------------------------------------------------------------

@dataclass
class NodeState:
    name: str
    status: str = "idle"  # idle / queued / running / done / failed
    started_at: float | None = None
    finished_at: float | None = None
    progress: str = ""                       # 最新一条进度文本
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    task_id: str | None = None               # 关联 TaskRunner 里的 task_id（真实模式）


@dataclass
class JobState:
    job_id: str
    pipeline_id: str
    title: str
    created_at: float
    updated_at: float
    inputs: dict[str, Any] = field(default_factory=dict)
    nodes: dict[str, NodeState] = field(default_factory=dict)
    # 用户拖动节点后存的位置覆盖默认布局；key 是 node name
    node_positions: dict[str, dict[str, float]] = field(default_factory=dict)
    # 节点级配置（不随 reset 清空）；key 是 node name，value 是任意配置 dict。
    # 目前用于 rw 节点的 {"profile": "toutiao"|"caijing"|"jitang"|"freestyle"}。
    node_configs: dict[str, dict[str, Any]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 事件总线（内存）
# ---------------------------------------------------------------------------

class EventBus:
    """In-memory pub/sub for SSE。每个订阅者一个 asyncio.Queue。

    事件 payload 形态：
        {"type": "node_status", "job_id": "...", "node": "asr", "state": {...}}
        {"type": "job_updated",  "job_id": "...", "state": {...}}
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, job_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        lst = self._subscribers.get(job_id)
        if lst and q in lst:
            lst.remove(q)
            if not lst:
                self._subscribers.pop(job_id, None)

    def publish(self, job_id: str, event: dict[str, Any]) -> None:
        for q in self._subscribers.get(job_id, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # 慢消费者忽略；前端可通过 GET /jobs/{id} 拉取最新全量状态
                pass


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class PipelineRunner:
    """每个进程一个 PipelineRunner 单例（state.py 里建）。"""

    def __init__(self, video_jobs_dir: Path) -> None:
        self.video_jobs_dir = video_jobs_dir
        self.video_jobs_dir.mkdir(parents=True, exist_ok=True)
        self.bus = EventBus()
        self._running_nodes: dict[tuple[str, str], asyncio.Task[Any]] = {}

    # ---------- 持久化 ----------

    def _state_file(self, job_id: str) -> Path:
        return self.video_jobs_dir / job_id / "pipeline_state.json"

    def _save(self, state: JobState) -> None:
        state.updated_at = time.time()
        path = self._state_file(state.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self, job_id: str) -> JobState:
        path = self._state_file(job_id)
        if not path.exists():
            raise KeyError(f"job not found: {job_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        nodes = {
            name: NodeState(**n) for name, n in data.get("nodes", {}).items()
        }
        # 向后兼容：pipeline 新增节点（如 lines）后，旧 job 的 nodes dict 缺 key。
        # 按当前 pipeline schema 自动补 idle 节点，避免 state.nodes[node] KeyError。
        # 找不到 pipeline 时（pipeline_id 已被删）跳过迁移，保留旧 nodes 原样。
        pipeline_id = data["pipeline_id"]
        if pipeline_id in PIPELINE_REGISTRY:
            pipeline = PIPELINE_REGISTRY[pipeline_id]
            for n in pipeline.nodes:
                if n.name in nodes:
                    continue
                if n.kind == "input":
                    # 理论上 input 应该一开始就存在；防御性补成 done + 当前 inputs
                    nodes[n.name] = NodeState(
                        name=n.name,
                        status="done",
                        started_at=data.get("created_at"),
                        finished_at=data.get("created_at"),
                        outputs=dict(data.get("inputs", {})),
                    )
                else:
                    nodes[n.name] = NodeState(name=n.name, status="idle")
        return JobState(
            job_id=data["job_id"],
            pipeline_id=data["pipeline_id"],
            title=data.get("title", ""),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            inputs=data.get("inputs", {}),
            nodes=nodes,
            node_positions=data.get("node_positions", {}),
            node_configs=data.get("node_configs", {}),
        )

    # ---------- Public API ----------

    def list_jobs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.video_jobs_dir.exists():
            return out
        for d in sorted(self.video_jobs_dir.iterdir(), reverse=True):
            sf = d / "pipeline_state.json"
            if not sf.exists():
                continue
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
                # 摘要：不带 nodes 详情
                out.append({
                    "job_id": data["job_id"],
                    "pipeline_id": data["pipeline_id"],
                    "title": data.get("title", ""),
                    "created_at": data["created_at"],
                    "updated_at": data["updated_at"],
                })
            except Exception as exc:
                logger.warning("[pipeline] read %s failed: %s", sf, exc)
        return out

    @staticmethod
    def _default_title(job_id: str, ts: float) -> str:
        # OPUS + 14 位本地时间戳 + 4 位 job_id hash，全大写；命名唯一且便于按时间排序
        return "OPUS" + time.strftime("%Y%m%d%H%M%S", time.localtime(ts)) + job_id[:4].upper()

    def create_job(self, pipeline_id: str, title: str, inputs: dict[str, Any]) -> JobState:
        pipeline = get_pipeline(pipeline_id)
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        # input 节点直接 done 状态，outputs 就是 inputs
        nodes: dict[str, NodeState] = {}
        for n in pipeline.nodes:
            if n.kind == "input":
                nodes[n.name] = NodeState(
                    name=n.name,
                    status="done",
                    started_at=now,
                    finished_at=now,
                    outputs=dict(inputs),
                )
            else:
                nodes[n.name] = NodeState(name=n.name, status="idle")
        state = JobState(
            job_id=job_id,
            pipeline_id=pipeline_id,
            title=title or self._default_title(job_id, now),
            created_at=now,
            updated_at=now,
            inputs=dict(inputs),
            nodes=nodes,
        )
        self._save(state)
        return state

    def get_job(self, job_id: str) -> JobState:
        return self._load(job_id)

    def get_episode(self, job_id: str) -> dict[str, Any] | None:
        """rw 节点产物 episode.json。"""
        ep = self.video_jobs_dir / job_id / "02_rw" / "episode.json"
        if not ep.exists():
            return None
        return json.loads(ep.read_text(encoding="utf-8"))

    def write_episode(self, job_id: str, episode: dict[str, Any]) -> None:
        ep = self.video_jobs_dir / job_id / "02_rw" / "episode.json"
        ep.parent.mkdir(parents=True, exist_ok=True)
        ep.write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")
        # 用户在 preview 阶段改 episode → 仅 invalidate render 及之后；
        # image / tts 本身保留 done（素材不需要重生，除非用户改了 prompt/beats 后主动重跑那两步）
        state = self._load(job_id)
        for n in get_pipeline(state.pipeline_id).downstream_of("preview"):
            if state.nodes[n].status != "idle":
                self._reset_node(state.nodes[n])
        self._save(state)
        self.bus.publish(state.job_id, {"type": "job_updated", "job_id": state.job_id})

    def update_title(self, job_id: str, title: str) -> None:
        state = self._load(job_id)
        state.title = title or self._default_title(job_id, state.created_at)
        state.updated_at = time.time()
        self._save(state)
        self.bus.publish(state.job_id, {"type": "job_updated", "job_id": state.job_id})

    def update_node_position(self, job_id: str, node: str, x: float, y: float) -> None:
        state = self._load(job_id)
        state.node_positions[node] = {"x": x, "y": y}
        self._save(state)
        # 位置变更不广播事件，前端自己掌握；下次 GET 时拿到

    def update_inputs(self, job_id: str, inputs: dict[str, Any]) -> None:
        """更新 job inputs（用户在 input 节点抽屉粘贴抖音 URL 时调用）。

        同步把 inputs 落到 input 节点的 outputs，避免下游 asr 拿不到 url。
        会 invalidate input 之外的所有下游节点（输入变了 → 之前的 asr 结果失效）。
        """
        state = self._load(job_id)
        state.inputs.update(inputs)
        # 同步到 input 节点的 outputs；保持 status=done
        for n in state.nodes.values():
            if n.name == "input":
                n.outputs.update(inputs)
                n.status = "done"
                n.finished_at = time.time()
                break
        # 输入变了 → 整条链 invalidate（除 input 自身）
        for n in state.nodes.values():
            if n.name != "input" and n.status != "idle":
                self._reset_node(n)
        self._save(state)
        self.bus.publish(job_id, {"type": "job_updated", "job_id": job_id})

    # parse_inputs 已废弃：解析在前端完成，后端只通过 update_inputs 持久化。

    # ---------- 重跑 / 调度 ----------

    def _reset_node(self, n: NodeState) -> None:
        n.status = "idle"
        n.started_at = None
        n.finished_at = None
        n.progress = ""
        n.outputs = {}
        n.error = None
        n.task_id = None

    async def run_node(self, job_id: str, node_name: str, params: dict[str, Any] | None = None) -> None:
        """触发某节点执行。会把节点及其下游全部 reset 后再排队。
        params: 可选节点配置（如 rw 的 {profile}），merge 进 node_configs 持久化，
        执行时由 _execute_* 读取。不随 reset 清空。
        """
        state = self._load(job_id)
        pipeline = get_pipeline(state.pipeline_id)
        node = pipeline.node(node_name)
        if node.kind in ("input", "output"):
            raise ValueError(f"node {node_name} is UI-only, not runnable")

        if params:
            cfg = dict(state.node_configs.get(node_name) or {})
            cfg.update(params)
            state.node_configs[node_name] = cfg

        # 检查 deps 已完成
        for dep in node.deps:
            if state.nodes[dep].status != "done":
                raise RuntimeError(
                    f"cannot run {node_name}: dep {dep} status={state.nodes[dep].status}"
                )

        # reset 自身 + 下游
        self._reset_node(state.nodes[node_name])
        for dn in pipeline.downstream_of(node_name):
            if state.nodes[dn].status != "idle":
                self._reset_node(state.nodes[dn])

        state.nodes[node_name].status = "queued"
        self._save(state)
        self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(state.nodes[node_name])})

        # spawn 异步执行
        key = (job_id, node_name)
        if key in self._running_nodes and not self._running_nodes[key].done():
            return  # 已经在跑
        self._running_nodes[key] = asyncio.create_task(self._execute(job_id, node_name))

    async def _execute(self, job_id: str, node_name: str) -> None:
        try:
            await self._execute_real(job_id, node_name)
        except asyncio.CancelledError:
            # 用户主动 cancel：把节点回退到 idle 让 UI 可以再点"确认"
            try:
                state = self._load(job_id)
                n = state.nodes[node_name]
                n.status = "idle"
                n.error = "cancelled"
                n.finished_at = time.time()
                n.progress = ""
                self._reset_node(n)
                self._save(state)
                self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})
            finally:
                raise
        except Exception as exc:
            logger.exception("[pipeline] node %s/%s failed", job_id, node_name)
            state = self._load(job_id)
            n = state.nodes[node_name]
            n.status = "failed"
            n.error = f"{type(exc).__name__}: {exc}"
            n.finished_at = time.time()
            self._save(state)
            self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})
        finally:
            self._running_nodes.pop((job_id, node_name), None)

    async def rewrite_rw_model(self, job_id: str, model_id: str) -> None:
        """重写 rw 某个模型的 draft（保留其他模型不动）。

        触发点：用户在 RW 抽屉切到某模型 tab 后点「重新生成」。流程同 _execute_rw
        但只调单个模型，覆盖目标 model_id 的子目录。
        """
        state = self._load(job_id)
        n = state.nodes.get("rw")
        if n is None:
            raise KeyError("rw node not found")
        if n.status != "done":
            raise ValueError("rw node not done; run rw first")
        drafts = (n.outputs or {}).get("drafts") or []
        if not any(isinstance(d, dict) and d.get("model_id") == model_id for d in drafts):
            raise KeyError(f"unknown model: {model_id}")
        cand = next((c for c in MODEL_CANDIDATES if c["id"] == model_id), None)
        if cand is None:
            raise KeyError(f"model {model_id} not in MODEL_CANDIDATES")

        # 重新拼 sourceText（同 _execute_rw）
        asr_node = state.nodes.get("asr")
        if asr_node is None or asr_node.status != "done":
            raise ValueError("asr node not done; cannot rewrite")
        job_dir = self.video_jobs_dir / job_id
        sections: list[str] = []
        for it in (asr_node.outputs or {}).get("items") or []:
            relpath = it.get("article_relpath") or it.get("transcript_relpath")
            if relpath and (job_dir / relpath).is_file():
                sections.append(
                    f"## 来源 {it.get('index')} - {it.get('title') or ''}\n\n"
                    f"{(job_dir / relpath).read_text(encoding='utf-8').strip()}"
                )
        source_text = "\n\n---\n\n".join(sections).strip()
        if not source_text:
            raise RuntimeError("asr 文章稿全部为空，无法 rw")

        profile = (state.node_configs.get("rw") or {}).get("profile", DEFAULT_RW_PROFILE)
        system_prompt, user_prompt = _build_rw_prompt(profile, source_text)

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "rw", f"[rerun {model_id}] {text}")

        on_progress("单模型重跑启动")
        try:
            raw_text = await _invoke_rw_candidate(cand, user_prompt, system_prompt, on_progress)
        except _ModelUnavailable as exc:
            raise RuntimeError(f"模型 {model_id} 不可用：{exc}") from exc

        # 剥模型偶尔自带的 ```markdown ... ``` 包裹
        cleaned = (raw_text or "").strip()
        if cleaned.startswith("```"):
            inner = re.match(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$", cleaned)
            if inner:
                cleaned = inner.group(1).strip()
        if not cleaned:
            raise RuntimeError(f"模型 {model_id} 输出为空")

        rw_root = job_dir / "02_rw"
        model_dir = rw_root / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "draft.md").write_text(cleaned + "\n", encoding="utf-8")

        # 如果用户当前选中的就是这个模型，把 02_rw/draft.md 也同步更新
        # 并 invalidate 下游（lines 已 done 的话需要重跑：LINES 会重新调 LLM）
        if (n.outputs or {}).get("selected_model_id") == model_id:
            shutil.copyfile(model_dir / "draft.md", rw_root / "draft.md")
            for dn in get_pipeline(state.pipeline_id).downstream_of("rw"):
                if state.nodes[dn].status != "idle":
                    self._reset_node(state.nodes[dn])

        state.updated_at = time.time()
        self._save(state)
        self.bus.publish(
            job_id,
            {"type": "node_status", "job_id": job_id, "node": "rw", "state": asdict(n)},
        )

    def select_rw_model(self, job_id: str, model_id: str) -> None:
        """用户在 rw 抽屉里选中某模型作为定稿入口。
        把 02_rw/{model_id}/draft.md 拷贝到 02_rw/draft.md；
        把 selected_model_id 写入 outputs。下游 LINES 节点会从 02_rw/draft.md
        读定稿，调 LLM 把它结构化成 episode.json。
        """
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
        dst.write_bytes(src.read_bytes())
        n.outputs["selected_model_id"] = model_id
        state.updated_at = time.time()
        self._save(state)
        self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": "rw", "state": asdict(n)})

    async def regen_scene_image_from_preview(self, job_id: str, scene_id: str) -> str:
        """preview 抽屉里点「生成图片」时调用。不要求 image 节点 done，
        独立于 pipeline 流水线，直接出图并写到 03_image/{scene_id}.webp。

        文件名约定：纯 {scene_id}.webp（不带序号前缀）—— 014 模板的 player.js
        line 64 用 picSrcFor(sceneId) = ASSET_ROOT + '/pictures/' + sceneId + '.webp'
        来拼图片 URL；preview.py 路由把 .014-draft-assets/pictures/{sceneId}.webp
        映射到 03_image/{sceneId}.webp。前缀 NN- 会让模板取不到 job 产出。

        实现：复用 _generate_scene_image（gpt-image-2 → Pillow → WebP）。
        若 image 节点已有 outputs.items 且包含该 scene_id，顺手更新 image_relpath。
        """
        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found; run rw first")
        scenes = (ep.get("scenes") or {})
        if scene_id not in scenes:
            raise ValueError(f"unknown scene: {scene_id}")
        sc = scenes[scene_id] or {}
        prompt = str(sc.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"scene {scene_id} has empty prompt; can't generate")

        image_cfg = ep.get("image") or {}
        size = image_cfg.get("size") or "1536x1024"
        quality = image_cfg.get("quality") or "auto"
        no_text_hint = image_cfg.get("noTextHint") or ""
        full_prompt = f"{prompt} {no_text_hint}".strip() if no_text_hint else prompt

        rel = f"03_image/{scene_id}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # 强制重生：先把已有 target 删掉，避免 _generate_scene_image 没有 force 模式
        if target.is_file():
            target.unlink()
        await asyncio.to_thread(
            _generate_scene_image,
            scene_id=scene_id,
            prompt=full_prompt,
            size=size,
            quality=quality,
            target=target,
            job_id=job_id,
        )

        # 同步 image 节点 outputs（如果存在该 scene 的 item）
        state = self._load(job_id)
        image_node = state.nodes.get("image")
        if image_node and image_node.outputs:
            items = list(image_node.outputs.get("items") or [])
            for it in items:
                if it.get("scene_id") == scene_id:
                    it["image_relpath"] = rel
                    break
            image_node.outputs["items"] = items
            image_node.finished_at = time.time()
            state.updated_at = time.time()
            self._save(state)
            self.bus.publish(
                job_id,
                {"type": "node_status", "job_id": job_id, "node": "image", "state": asdict(image_node)},
            )
        return rel

    async def regen_image_scene(self, job_id: str, scene_id: str) -> None:
        """重生 image 节点里指定 scene 的图片。不动其他场景，不动下游节点状态。
        实现 = regen_scene_image_from_preview，要求 image 节点 done 且 scene 在 items 里。
        """
        state = self._load(job_id)
        n = state.nodes.get("image")
        if n is None:
            raise KeyError("image node not found")
        if n.status != "done":
            raise ValueError("image node not done; run image first")
        items = list((n.outputs or {}).get("items") or [])
        if not any(it.get("scene_id") == scene_id for it in items):
            raise ValueError(f"unknown scene: {scene_id}")
        await self.regen_scene_image_from_preview(job_id, scene_id)

    async def regen_tts_beat(self, job_id: str, index: int) -> None:
        """重生 tts 节点里指定 beat 的音频。force 模式覆盖 04_tts/NNNN.mp3。"""
        state = self._load(job_id)
        n = state.nodes.get("tts")
        if n is None:
            raise KeyError("tts node not found")
        if n.status != "done":
            raise ValueError("tts node not done; run tts first")
        items = list((n.outputs or {}).get("items") or [])
        target = next((it for it in items if it.get("index") == index), None)
        if target is None:
            raise ValueError(f"unknown beat index: {index}")

        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found")
        beats = ep.get("beats") or []
        if index < 1 or index > len(beats):
            raise ValueError(f"beat index out of range: {index}")
        zh = str(beats[index - 1].get("zh") or "").strip()
        if not zh:
            raise ValueError(f"beat {index} has empty zh")

        cfg = (ep.get("audio") or {}).get("tts") or {}
        total = len(beats)
        width = max(4, len(str(total)))
        name = f"{index:0{width}d}.mp3"
        out_path = self.video_jobs_dir / job_id / "04_tts" / name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.is_file():
            out_path.unlink()

        from ncds_opus_factory.commands import tts as tts_cmd

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "tts", f"[regen #{index}] {text}")

        await asyncio.to_thread(
            tts_cmd._synth_one,
            zh,
            out_path,
            voice=cfg.get("voice", tts_cmd.DEFAULT_VOICE),
            rate=cfg.get("rate", tts_cmd.DEFAULT_RATE),
            sample_rate=cfg.get("sampleRate", tts_cmd.DEFAULT_SAMPLE_RATE),
            model=cfg.get("model", tts_cmd.DEFAULT_MODEL),
            on_progress=on_progress,
        )

        state = self._load(job_id)
        n = state.nodes.get("tts")
        if n is None:
            return
        n.outputs["items"] = items
        n.finished_at = time.time()
        state.updated_at = time.time()
        self._save(state)
        self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": "tts", "state": asdict(n)})

    async def regen_tts_scene(self, job_id: str, scene_id: str) -> None:
        """015：重生指定 scene 的整段音频（spawn tts_gen.py --only sid --force）。
        UI 按 scene 渲染，重生粒度也是 scene，与「整段合成」语义一致。
        """
        state = self._load(job_id)
        if state.pipeline_id != "paper_card_talk_015":
            raise ValueError("scene 级重生仅 015 pipeline 支持")
        n = state.nodes.get("tts")
        if n is None:
            raise KeyError("tts node not found")
        if n.status != "done":
            raise ValueError("tts node not done; run tts first")

        job_dir = self.video_jobs_dir / job_id
        ep_path = job_dir / "02_rw" / "episode.json"
        if not ep_path.is_file():
            raise ValueError("episode.json not found")
        # 校验 scene 存在于 beats
        ep = json.loads(ep_path.read_text(encoding="utf-8"))
        if not any((b.get("scene") == scene_id) for b in (ep.get("beats") or [])):
            raise ValueError(f"unknown scene: {scene_id}")

        repo_root = Path(__file__).resolve().parents[3]
        tts_gen = (
            repo_root / "src" / "ncds_opus_factory" / "templates"
            / "paper_card_talk_015" / ".015-draft-assets" / "tts_gen.py"
        )
        audio_dir = job_dir / "04_tts"

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "tts", f"[regen {scene_id}] {text}")

        await asyncio.to_thread(
            _run_tts_gen_015,
            script=tts_gen,
            episode_path=ep_path,
            audio_dir=audio_dir,
            on_line=on_progress,
            only=scene_id,
            force=True,
        )

        # 重建 items（episode 的该 scene beats 时间戳已更新）
        ep2 = json.loads(ep_path.read_text(encoding="utf-8"))
        state = self._load(job_id)
        n = state.nodes.get("tts")
        if n is None:
            return
        n.outputs["items"] = _rebuild_tts_items_015(ep2)
        n.finished_at = time.time()
        state.updated_at = time.time()
        self._save(state)
        self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": "tts", "state": asdict(n)})

    async def cancel_node(self, job_id: str, node_name: str) -> bool:
        """取消正在跑的节点 task。返回是否真的取消了。"""
        key = (job_id, node_name)
        task = self._running_nodes.get(key)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def _execute_real(self, job_id: str, node_name: str) -> None:
        """真实执行：按 node_name 分发到对应实现。

        已接入：tts, image
        未接入：asr, rw, render → 显式 raise NotImplementedError；不再 fallback mock。
        """
        state = self._load(job_id)
        n = state.nodes[node_name]
        n.status = "running"
        n.started_at = time.time()
        n.progress = "启动..."
        n.error = None
        n.outputs = {}
        self._save(state)
        self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

        if node_name == "tts":
            outputs = await self._execute_tts(job_id)
        elif node_name == "image":
            outputs = await self._execute_image(job_id)
        elif node_name == "asr":
            outputs = await self._execute_asr(job_id)
        elif node_name == "rw":
            outputs = await self._execute_rw(job_id)
        elif node_name == "lines":
            outputs = await self._execute_lines(job_id)
        elif node_name == "render":
            outputs = await self._execute_render(job_id)
        else:
            raise ValueError(f"unknown runnable node: {node_name}")

        state = self._load(job_id)
        n = state.nodes[node_name]
        n.status = "done"
        n.finished_at = time.time()
        n.progress = "完成"
        n.outputs = outputs
        self._save(state)
        self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

    # ------------------------------------------------------------
    # 真接入：进度推送 helper
    # ------------------------------------------------------------
    def _push_progress(self, job_id: str, node_name: str, text: str) -> None:
        """更新节点 progress 字段 + 落盘 + SSE publish。

        commands/{tts,wst}.run 的 on_progress 回调走这条；可以从 to_thread 里调，
        因为 self._save / bus.publish 都是同步 fire-and-forget。
        """
        try:
            state = self._load(job_id)
            n = state.nodes.get(node_name)
            if n is None:
                return
            n.progress = text
            self._save(state)
            self.bus.publish(
                job_id,
                {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)},
            )
        except Exception as exc:  # 不要让进度推送的失败炸掉真任务
            logger.warning("[pipeline] push_progress failed: %s", exc)

    def _push_outputs_patch(self, job_id: str, node_name: str, key: str, value: Any) -> None:
        """running 期间往 node.outputs[key] 写一份实时进度 + publish。
        前端据此渲染状态行（RW: model_progress / ASR: item_progress）。
        done 后 outputs 会被整体结果覆盖。
        """
        try:
            state = self._load(job_id)
            n = state.nodes.get(node_name)
            if n is None:
                return
            n.outputs = {**(n.outputs or {}), key: value}
            self._save(state)
            self.bus.publish(
                job_id,
                {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)},
            )
        except Exception as exc:
            logger.warning("[pipeline] push_outputs_patch failed: %s", exc)

    def _push_model_progress(self, job_id: str, node_name: str, model_progress: dict[str, Any]) -> None:
        self._push_outputs_patch(job_id, node_name, "model_progress", model_progress)

    # ------------------------------------------------------------
    # 真接入：tts 节点
    # ------------------------------------------------------------
    async def _execute_tts(self, job_id: str) -> dict[str, Any]:
        """按 02_rw/episode.json 合成配音。
        - 015 pipeline：按 scene 整段合成（scene-<sid>.mp3 + 字级时间戳写回 beats），韵律更连贯
        - 014 pipeline：逐句合成 04_tts/NNNN.mp3（旧）
        """
        state = self._load(job_id)
        pipeline_id = state.pipeline_id
        job_dir = self.video_jobs_dir / job_id
        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found; run rw first (or manually seed it)")

        beats_raw = ep.get("beats") or []
        if not beats_raw:
            raise ValueError("episode.beats is empty; nothing to synthesize")

        out_dir = job_dir / "04_tts"
        out_dir.mkdir(parents=True, exist_ok=True)

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "tts", text)

        # —— 015：scene 整段合成 ——
        if pipeline_id == "paper_card_talk_015":
            repo_root = Path(__file__).resolve().parents[3]
            tts_gen = (
                repo_root / "src" / "ncds_opus_factory" / "templates"
                / "paper_card_talk_015" / ".015-draft-assets" / "tts_gen.py"
            )
            if not tts_gen.is_file():
                raise RuntimeError(f"015 tts_gen.py not found: {tts_gen}")
            ep_path = job_dir / "02_rw" / "episode.json"
            on_progress(f"按 scene 整段合成（{len(beats_raw)} beats）…")
            await asyncio.to_thread(
                _run_tts_gen_015,
                script=tts_gen,
                episode_path=ep_path,
                audio_dir=out_dir,
                on_line=on_progress,
            )
            # 读回 tts_gen 写好时间戳的 episode，组装 beat 级 items（audio 指向 scene mp3）
            ep2 = json.loads(ep_path.read_text(encoding="utf-8"))
            items = _rebuild_tts_items_015(ep2)
            scene_files = {it["audio_relpath"] for it in items if it.get("audio_relpath")}
            on_progress(f"完成：{len(scene_files)} 段 scene 音频 · {len(items)} beats")
            return {
                "items": items,
                "audio_dir": str(out_dir),
                "mode": "segmented",
                "scene_count": len(scene_files),
                "audio_count": len(scene_files),
            }

        # —— 014：逐句合成（旧）——
        zh_list = [str(b.get("zh") or "") for b in beats_raw]
        cfg = (ep.get("audio") or {}).get("tts") or {}

        from ncds_opus_factory.commands import tts as tts_cmd

        result = await asyncio.to_thread(
            tts_cmd.run,
            beats=zh_list,
            output_dir=str(out_dir),
            voice=cfg.get("voice", tts_cmd.DEFAULT_VOICE),
            rate=cfg.get("rate", tts_cmd.DEFAULT_RATE),
            sample_rate=cfg.get("sampleRate", tts_cmd.DEFAULT_SAMPLE_RATE),
            model=cfg.get("model", tts_cmd.DEFAULT_MODEL),
            on_progress=on_progress,
        )

        # 生成 items 列表给前端 TtsResultPanel 用（文件名约定与 tts_cmd.run 内部一致）
        total = len(beats_raw)
        width = max(4, len(str(total)))
        items: list[dict[str, Any]] = []
        for i, b in enumerate(beats_raw, start=1):
            name = f"{i:0{width}d}.mp3"
            items.append({
                "index": i,
                "zh": str(b.get("zh") or ""),
                "scene": str(b.get("scene") or ""),
                "audio_relpath": f"04_tts/{name}",
            })

        return {
            "items": items,
            "audio_dir": str(out_dir),
            "audio_count": result["new_count"] + result["skipped"],
            "new_count": result["new_count"],
            "skipped": result["skipped"],
            "model": result["model"],
            "voice": result["voice"],
        }

    # ------------------------------------------------------------
    # 真接入：image 节点
    # ------------------------------------------------------------
    async def _execute_image(self, job_id: str) -> dict[str, Any]:
        """按 02_rw/episode.json scenes[].prompt 批量调 gpt-image-2 出图 → WebP。

        复刻 014 自带 pic_gen.py 的 orchestration：
        - beats 出场顺序去重 → scene_id 列表
        - 跳过 ch* 章节卡（CSS 渲染，不需要图）
        - 每个 scene 用 gpt_image/gpt_image_gen.py 出 PNG，Pillow 转 WebP 落 03_image/{sid}.webp
        - 幂等：已存在跳过
        """
        job_dir = self.video_jobs_dir / job_id
        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found; run rw first (or manually seed it)")

        beats = ep.get("beats") or []
        scenes_def = ep.get("scenes") or {}
        image_cfg = ep.get("image") or {}

        # 出场顺序去重
        seen: set[str] = set()
        scene_order: list[str] = []
        for b in beats:
            sid = b.get("scene")
            if sid and sid not in seen:
                seen.add(sid)
                scene_order.append(sid)

        eligible = [sid for sid in scene_order if not sid.startswith("ch")]
        if not eligible:
            raise ValueError("no image-eligible scenes (all are chapter cards or no scenes)")

        size = image_cfg.get("size") or "1536x1024"
        quality = image_cfg.get("quality") or "auto"
        no_text_hint = image_cfg.get("noTextHint") or ""

        out_dir = job_dir / "03_image"
        out_dir.mkdir(parents=True, exist_ok=True)

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "image", text)

        on_progress(f"image 开始：{len(eligible)} 个场景 · {size} {quality}")

        items: list[dict[str, Any]] = []
        ok = sk = fail = 0
        for i, sid in enumerate(scene_order, start=1):
            sc = scenes_def.get(sid) or {}
            prompt = str(sc.get("prompt") or "").strip()
            if sid.startswith("ch"):
                items.append({"scene_id": sid, "prompt": prompt, "image_relpath": None,
                              "skipped_reason": "chapter card"})
                continue
            if not prompt:
                items.append({"scene_id": sid, "prompt": "", "image_relpath": None,
                              "skipped_reason": "empty prompt"})
                fail += 1
                continue

            target = out_dir / f"{sid}.webp"
            if target.is_file():
                items.append({"scene_id": sid, "prompt": prompt,
                              "image_relpath": f"03_image/{sid}.webp"})
                sk += 1
                on_progress(f"[{i}/{len(scene_order)}] {sid} 已存在，跳过")
                continue

            full_prompt = f"{prompt} {no_text_hint}".strip() if no_text_hint else prompt
            on_progress(f"[{i}/{len(scene_order)}] {sid} 生成中…")
            try:
                await asyncio.to_thread(
                    _generate_scene_image,
                    scene_id=sid,
                    prompt=full_prompt,
                    size=size,
                    quality=quality,
                    target=target,
                    job_id=job_id,
                )
                items.append({"scene_id": sid, "prompt": prompt,
                              "image_relpath": f"03_image/{sid}.webp"})
                ok += 1
            except Exception as exc:
                logger.warning("[pipeline] image scene %s failed: %s", sid, exc)
                on_progress(f"[{i}/{len(scene_order)}] {sid} 失败: {exc}")
                items.append({"scene_id": sid, "prompt": prompt, "image_relpath": None,
                              "error": str(exc)})
                fail += 1

        if ok == 0 and fail > 0:
            raise RuntimeError(f"all {fail} scene image generations failed")

        on_progress(f"image 完成：ok={ok} skipped={sk} failed={fail}")

        return {
            "items": items,
            "pictures_dir": str(out_dir),
            "pictures_count": ok + sk,
            "ok": ok,
            "skipped": sk,
            "failed": fail,
        }

    # ------------------------------------------------------------
    # 真接入：asr 节点
    # ------------------------------------------------------------
    async def _execute_asr(self, job_id: str) -> dict[str, Any]:
        """串行跑 inputs.urls 里每条媒体链接，只跑 video_pipeline.py 转写 + 清洗稿。
        填 items[]（front-end 两 tab：听写稿 / 文章解析）。精华稿（highlight）已
        从 asr 节点剥离 —— "爆款精华"现在由 rw 节点的 4 模型并行改写承担。

        刻意绕过 scripts/video_job_worker.mjs 整套飞书编排 —— studio 画布场景没有
        chatId/accountId 上下文，直接对接 video_pipeline.py 的本地产物。
        """
        state = self._load(job_id)
        urls = list(state.inputs.get("urls") or [])
        if not urls:
            raise ValueError("inputs.urls is empty; paste media links into the INPUT node first")

        job_dir = self.video_jobs_dir / job_id
        asr_root = job_dir / "01_asr"
        asr_root.mkdir(parents=True, exist_ok=True)

        repo_root = Path(__file__).resolve().parents[3]
        pipeline_script = repo_root / "skills" / "video-pipeline" / "scripts" / "video_pipeline.py"
        if not pipeline_script.is_file():
            raise RuntimeError(f"video_pipeline.py not found at {pipeline_script}")

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "asr", text)

        # shares 是 InputPanel 解析出的标题/作者，按 URL 对齐
        shares_by_url: dict[str, dict[str, Any]] = {}
        for s in state.inputs.get("shares") or []:
            if isinstance(s, dict) and isinstance(s.get("url"), str):
                shares_by_url[s["url"]] = s

        # 全局下载缓存：跨 job 复用同一 URL 已下载的 mp4。
        # 目录结构：video-jobs/_downloads/<url_md5>/<platform>_*.mp4
        # 命中时把 cache 里的 mp4 symlink 进 item_dir/raw/，video_pipeline.py 的
        # download_video fast-path 看到 raw/ 已有 mp4 就跳过实际下载；
        # 未命中时正常下载到 raw/，下载完成后把真 mp4 迁移到 cache 再 symlink 回来。
        downloads_cache = self.video_jobs_dir / "_downloads"
        downloads_cache.mkdir(parents=True, exist_ok=True)

        # 作品级进度：每条 URL 一行，pending → running(各阶段 stage) → done | failed
        item_status: dict[str, dict[str, Any]] = {}
        for i, u in enumerate(urls, start=1):
            sh = shares_by_url.get(u) or {}
            item_status[str(i)] = {
                "index": i,
                "title": str(sh.get("title") or sh.get("author") or ""),
                "url": u,
                "status": "pending",
                "stage": "",
                "error": "",
            }

        def push_items() -> None:
            self._push_outputs_patch(job_id, "asr", "item_progress", {k: dict(v) for k, v in item_status.items()})

        push_items()

        items: list[dict[str, Any]] = []
        for idx, url in enumerate(urls, start=1):
            item_status[str(idx)]["status"] = "running"
            item_status[str(idx)]["stage"] = "准备中"
            push_items()
            try:
                item_dir = asr_root / str(idx)
                item_dir.mkdir(parents=True, exist_ok=True)

                url_md5 = hashlib.md5(url.encode("utf-8")).hexdigest()
                url_md5_short = url_md5[:12]
                cache_dir = downloads_cache / url_md5
                cache_dir.mkdir(parents=True, exist_ok=True)

                # 幂等 stamp：用 url 的 short md5 标记 item_dir 当前归属哪条 URL。
                # URL 变了（用户在 INPUT 节点改了链接）→ 清掉旧产物重新链入缓存。
                stamp_path = item_dir / ".url-stamp"
                existing_stamp = (
                    stamp_path.read_text(encoding="utf-8").strip()
                    if stamp_path.is_file() else ""
                )
                if existing_stamp and existing_stamp != url_md5_short:
                    on_progress(f"[{idx}/{len(urls)}] URL 已变更，清掉旧产物")
                    shutil.rmtree(item_dir)
                    item_dir.mkdir(parents=True, exist_ok=True)
                stamp_path.write_text(url_md5_short, encoding="utf-8")

                # 缓存→raw/ symlink：若全局缓存里已有此 URL 对应的 mp4，链进 item_dir/raw/
                raw_dir = item_dir / "raw"
                raw_dir.mkdir(parents=True, exist_ok=True)
                cached_mp4s = sorted(cache_dir.glob("*.mp4"))
                if cached_mp4s:
                    on_progress(
                        f"[{idx}/{len(urls)}] 命中下载缓存，复用 {cached_mp4s[0].name}（跳过下载）"
                    )
                    for mp4 in cached_mp4s:
                        link = raw_dir / mp4.name
                        if not link.exists():
                            link.symlink_to(mp4.resolve())

                on_progress(f"[{idx}/{len(urls)}] 启动 video_pipeline.py")

                def on_line(line: str, i: int = idx, total: int = len(urls)) -> None:
                    lbl = _asr_stage_label(line)
                    if lbl and item_status[str(i)]["stage"] != lbl:
                        item_status[str(i)]["stage"] = lbl
                        push_items()
                    on_progress(f"[{i}/{total}] {line}")

                await asyncio.to_thread(
                    _run_video_pipeline,
                    pipeline_script=pipeline_script,
                    url=url,
                    output_dir=item_dir,
                    on_line=on_line,
                )

                # 跑完后扫一遍 raw/，把刚下载的真 mp4 迁移到全局缓存 + 在原位留 symlink。
                # 这样下次同 URL（含其它 job）跑时，cached_mp4s 那段就能命中。
                for mp4 in raw_dir.glob("*.mp4"):
                    if mp4.is_symlink():
                        continue
                    cache_target = cache_dir / mp4.name
                    if not cache_target.exists():
                        shutil.move(str(mp4), str(cache_target))
                    else:
                        mp4.unlink()
                    mp4.symlink_to(cache_target.resolve())

                result_json = item_dir / "deliverables" / "result.json"
                if not result_json.is_file():
                    raise RuntimeError(f"video_pipeline 未产出 result.json: {result_json}")
                result = json.loads(result_json.read_text(encoding="utf-8"))

                transcript_abs = result.get("rawTranscriptPath") or result.get("transcript")
                if not transcript_abs or not Path(transcript_abs).is_file():
                    raise RuntimeError(f"transcript 缺失或不存在: {transcript_abs}")

                # 文章整理：调本机 opus（claude）把原始 transcript polish 成 markdown 文章。
                # 不复用 video_pipeline.py 内部的 polishing 链路（它依赖 ~/.openclaw/openclaw.json
                # 模型映射 + gemini/codex 双模型并行，本机配置不全）。
                item_status[str(idx)]["stage"] = "整理文章"
                push_items()
                on_progress(f"[{idx}/{len(urls)}] 调 opus 整理成文章")
                article_path = item_dir / "article.md"
                share = shares_by_url.get(url) or {}
                try:
                    await asyncio.to_thread(
                        _polish_transcript_with_opus,
                        transcript_path=Path(transcript_abs),
                        output_path=article_path,
                        title_hint=str(share.get("title") or share.get("author") or ""),
                    )
                    article_abs = str(article_path)
                except Exception as exc:
                    on_progress(f"[{idx}/{len(urls)}] opus polish 失败：{exc}；fallback 到原始 transcript")
                    article_abs = transcript_abs

                items.append({
                    "index": idx,
                    "url": url,
                    "title": str(share.get("title") or ""),
                    "author": str(share.get("author") or ""),
                    "transcript_relpath": str(Path(transcript_abs).resolve().relative_to(job_dir)),
                    "article_relpath": str(Path(article_abs).resolve().relative_to(job_dir)),
                    "error": None,
                })
                item_status[str(idx)]["status"] = "done"
                item_status[str(idx)]["stage"] = "完成"
                push_items()
                on_progress(f"[{idx}/{len(urls)}] 完成")
            except Exception as exc:
                msg = str(exc)
                first_line = msg.splitlines()[0] if msg.splitlines() else "未知错误"
                item_status[str(idx)]["status"] = "failed"
                item_status[str(idx)]["stage"] = "失败"
                item_status[str(idx)]["error"] = msg
                push_items()
                on_progress(f"[{idx}/{len(urls)}] 失败：{first_line}")
                sh = shares_by_url.get(url) or {}
                items.append({
                    "index": idx,
                    "url": url,
                    "title": str(sh.get("title") or ""),
                    "author": str(sh.get("author") or ""),
                    "transcript_relpath": "",
                    "article_relpath": "",
                    "error": msg,
                })
                continue


        succeeded = [it for it in items if not it.get("error")]
        if not succeeded:
            raise RuntimeError(f"全部 {len(urls)} 个作品处理失败，详见各作品状态")

        return {"items": items, "asr_dir": str(asr_root)}

    # ------------------------------------------------------------
    # 真接入：rw 节点
    # ------------------------------------------------------------
    async def _execute_rw(self, job_id: str) -> dict[str, Any]:
        """4 模型并行改写：opus / gpt5 / gemini_local / deepseek 同时跑 paper_card_talk profile。

        每个模型直出 {"beats":[...]} JSON；本机不可用的模型保留在 drafts 列表里但
        status='failed' + reason='模型不可用'，让前端看到 4 槽真实状态。

        产物布局（与前端 RwDraft 契约对齐）：
          02_rw/{model_id}/draft.md            # 模型出的 beats JSON 漂亮打印（仅 success）
          02_rw/{model_id}/episode.json        # 模板骨架 + 替换 beats[]（仅 success）
          02_rw/episode.json                   # 留空，select_rw_model 选模型后拷贝
        """
        state = self._load(job_id)
        asr_node = state.nodes.get("asr")
        if asr_node is None or asr_node.status != "done":
            raise ValueError("asr node not done; run asr first")
        items = list((asr_node.outputs or {}).get("items") or [])
        if not items:
            raise ValueError("asr.outputs.items is empty; nothing to rewrite")

        job_dir = self.video_jobs_dir / job_id
        rw_root = job_dir / "02_rw"
        rw_root.mkdir(parents=True, exist_ok=True)

        # 拼 sourceText：asr 各条 article（opus 整理后的文章）拼起来
        sections: list[str] = []
        for it in items:
            relpath = it.get("article_relpath") or it.get("transcript_relpath")
            if not relpath:
                continue
            p = job_dir / relpath
            if not p.is_file():
                continue
            sections.append(
                f"## 来源 {it.get('index')} - {it.get('title') or ''}\n\n"
                f"{p.read_text(encoding='utf-8').strip()}"
            )
        source_text = "\n\n---\n\n".join(sections).strip()
        if not source_text:
            raise RuntimeError("asr 文章稿全部为空，无法 rw")

        profile = (state.node_configs.get("rw") or {}).get("profile", DEFAULT_RW_PROFILE)
        system_prompt, user_prompt = _build_rw_prompt(profile, source_text)

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "rw", text)

        # 模型级状态：pending → running → done | failed | unavailable
        # 共享内存 dict，每次状态变化全量写进 outputs.model_progress 给前端渲染状态行
        model_status: dict[str, dict[str, Any]] = {
            cand["id"]: {"model_id": cand["id"], "label": cand["label"], "status": "pending"}
            for cand in MODEL_CANDIDATES
        }

        def push_status(model_id: str, st: str) -> None:
            if model_id in model_status:
                model_status[model_id]["status"] = st
            self._push_model_progress(job_id, "rw", {k: dict(v) for k, v in model_status.items()})

        on_progress(f"4 模型并行启动；source={len(source_text)} 字")
        # 先推一帧全 pending，让前端立刻看到 4 行
        self._push_model_progress(job_id, "rw", {k: dict(v) for k, v in model_status.items()})

        # 增量产物：每个模型完成立即写盘 + push drafts，前端不必等全部完成才能看/选。
        # drafts_by_id 共享，按 MODEL_CANDIDATES 顺序组装成 outputs.drafts。
        drafts_by_id: dict[str, dict[str, Any]] = {}

        def ordered_drafts() -> list[dict[str, Any]]:
            return [drafts_by_id[c["id"]] for c in MODEL_CANDIDATES if c["id"] in drafts_by_id]

        def push_drafts() -> None:
            self._push_outputs_patch(job_id, "rw", "drafts", ordered_drafts())

        def make_draft(cand: dict[str, str], res: Any) -> dict[str, Any]:
            """把单模型结果（成功文本 / 异常）转成 draft dict + 成功时写盘。"""
            mid, label = cand["id"], cand["label"]
            if isinstance(res, _ModelUnavailable):
                return {"model_id": mid, "label": label, "status": "failed",
                        "reason": f"模型不可用：{res}", "draft_relpath": None, "episode_relpath": None}
            if isinstance(res, BaseException):
                return {"model_id": mid, "label": label, "status": "failed",
                        "reason": str(res), "draft_relpath": None, "episode_relpath": None}
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

        async def run_one(cand: dict[str, str]) -> None:
            try:
                res: Any = await _invoke_rw_candidate(cand, user_prompt, system_prompt, on_progress, push_status)
            except BaseException as exc:  # noqa: BLE001 — 失败/不可用都收进 draft
                res = exc
            drafts_by_id[cand["id"]] = make_draft(cand, res)
            push_drafts()  # 这个模型一好就立即渲染到前端

        await asyncio.gather(*[run_one(c) for c in MODEL_CANDIDATES])

        drafts_out = ordered_drafts()
        success_count = sum(1 for d in drafts_out if d.get("status") == "success")
        if success_count == 0:
            reasons = "; ".join(f"{d['model_id']}={d.get('reason')}" for d in drafts_out)
            raise RuntimeError(f"4 个模型全部失败：{reasons}")

        on_progress(f"完成：{success_count}/{len(MODEL_CANDIDATES)} 成功")

        return {
            "drafts": drafts_out,
            "selected_model_id": None,
            "candidate_count": len(drafts_out),
            "success_count": success_count,
            "profile": profile,
        }

    # ------------------------------------------------------------
    # lines 节点：校验型 no-op（实际"抽取台词"由前端 LinesPanel 直接编辑
    # 02_rw/episode.json 的 beats[] 完成；后端只确认数据齐了就 done）
    # ------------------------------------------------------------
    async def _execute_lines(self, job_id: str) -> dict[str, Any]:
        """读 02_rw/draft.md 定稿 → 调 opus 结构化成 episode.json（meta + beats + scenes）。
        合并模板骨架（保留 audio/visual/playback/fonts/image 等渲染配置），只覆盖
        meta/beats/scenes 三个字段，写 02_rw/episode.json，供 tts/image/render 消费。
        """
        pipeline_id = self._load(job_id).pipeline_id
        draft_path = self.video_jobs_dir / job_id / "02_rw" / "draft.md"
        if not draft_path.is_file():
            raise ValueError(
                "02_rw/draft.md missing；先在 RW 抽屉里选模型（用此模型 · 下一步）"
            )
        draft = draft_path.read_text(encoding="utf-8").strip()
        if not draft:
            raise ValueError("02_rw/draft.md 为空")

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "lines", text)

        system_prompt, user_prompt = _build_lines_prompt(draft)
        on_progress("调 opus 结构化为 beats + scenes…")
        raw = await asyncio.to_thread(
            _call_opus_for_rw, user_prompt, system_prompt, "claude-opus-4-7"
        )

        # 解析 JSON（容忍 ```json ... ``` 包裹）
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            inner = re.match(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$", cleaned)
            if inner:
                cleaned = inner.group(1).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"opus 输出非法 JSON：{exc}；tail={cleaned[-300:]}") from exc

        beats = parsed.get("beats") if isinstance(parsed, dict) else None
        scenes_in = parsed.get("scenes") if isinstance(parsed, dict) else None
        meta_in = parsed.get("meta") if isinstance(parsed, dict) else None
        if not isinstance(beats, list) or not beats:
            raise RuntimeError("结构化结果缺 beats[] 或为空")
        if not isinstance(scenes_in, dict) or not scenes_in:
            raise RuntimeError("结构化结果缺 scenes{} 或为空")

        # 规整 beats（只留约定字段）
        norm_beats: list[dict[str, Any]] = []
        for b in beats:
            if not isinstance(b, dict):
                continue
            zh = str(b.get("zh") or "").strip()
            if not zh:
                continue
            norm_beats.append({
                "zh": zh,
                "en": str(b.get("en") or ""),
                "scene": str(b.get("scene") or ""),
                "chapter": b.get("chapter") if isinstance(b.get("chapter"), int) else None,
            })
        if not norm_beats:
            raise RuntimeError("beats 全部为空")

        # 规整 scenes：补 player 友好字段（motion 缺失 player 会跳过，但 prompt 必须有）
        norm_scenes: dict[str, dict[str, Any]] = {}
        for sid, sc in scenes_in.items():
            prompt = ""
            if isinstance(sc, dict):
                prompt = str(sc.get("prompt") or "").strip()
            elif isinstance(sc, str):
                prompt = sc.strip()
            norm_scenes[str(sid)] = {"prompt": prompt, "label": "", "overlays": []}
        # beats 引用但 scenes 缺的 scene_id 补空 prompt，避免 image 节点 KeyError
        for b in norm_beats:
            sid = b["scene"]
            if sid and sid not in norm_scenes:
                norm_scenes[sid] = {"prompt": "", "label": "", "overlays": []}

        # 合并模板骨架（按 pipeline 选 014/015 模板）
        episode = _load_template_episode(pipeline_id)
        episode["beats"] = norm_beats
        episode["scenes"] = norm_scenes
        if isinstance(meta_in, dict):
            meta = dict(episode.get("meta") or {})
            if meta_in.get("title"):
                meta["title"] = str(meta_in["title"])
            if meta_in.get("subtitle"):
                meta["subtitle"] = str(meta_in["subtitle"])
            if isinstance(meta_in.get("tags"), list):
                meta["tags"] = [str(t) for t in meta_in["tags"]]
            episode["meta"] = meta

        ep_path = self.video_jobs_dir / job_id / "02_rw" / "episode.json"
        ep_path.write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")
        on_progress(f"完成：{len(norm_beats)} 条 beats · {len(norm_scenes)} 个 scenes")

        return {
            "episode_relpath": "02_rw/episode.json",
            "beats_count": len(norm_beats),
            "scenes_count": len(norm_scenes),
        }

    # ------------------------------------------------------------
    # 真接入：render 节点
    # ------------------------------------------------------------
    async def _execute_render(self, job_id: str) -> dict[str, Any]:
        """出 1920x1080 MP4。按 pipeline 选渲染器：
        - 015：commands/render_015（015 render.mjs，scene 整段合音）
        - 014：commands/render_014（逐句 mp3）
        依赖 02_rw/episode.json + 04_tts/*.mp3 + 03_image/*.webp。
        """
        pipeline_id = self._load(job_id).pipeline_id
        job_dir = self.video_jobs_dir / job_id
        episode_path = job_dir / "02_rw" / "episode.json"
        if not episode_path.is_file():
            raise ValueError("02_rw/episode.json missing; select an rw model first")
        audio_dir = job_dir / "04_tts"
        if not audio_dir.is_dir() or not any(audio_dir.glob("*.mp3")):
            raise ValueError("04_tts/*.mp3 missing; run tts first")
        picture_dir = job_dir / "03_image"
        out_dir = job_dir / "06_render"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "output.mp4"

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "render", text)

        if pipeline_id == "paper_card_talk_015":
            from ncds_opus_factory.commands import render_015 as render_cmd
            on_progress("启动 render_015（scene 整段合音）")
        else:
            from ncds_opus_factory.commands import render_014 as render_cmd
            on_progress("启动 render_014（puppeteer + ffmpeg）")

        result = await asyncio.to_thread(
            render_cmd.run,
            episode_path=str(episode_path),
            audio_dir=str(audio_dir),
            output_path=str(out_path),
            picture_dir=str(picture_dir) if picture_dir.is_dir() else None,
            workdir=str(out_dir / "_render_workdir"),
            cleanup_workdir=True,
            on_progress=on_progress,
        )

        return {
            "video_relpath": f"06_render/{out_path.name}",
            "output_path": result.get("output_path", str(out_path)),
            "video_size_bytes": result.get("video_size_bytes"),
            "workdir": result.get("workdir"),
        }


def _rebuild_tts_items_015(episode: dict[str, Any]) -> list[dict[str, Any]]:
    """从写好时间戳的 episode 组装 beat 级 items（audio 指向所属 scene 整段 mp3）。
    前端 TtsResultPanel 按 scene 分组渲染时用。"""
    items: list[dict[str, Any]] = []
    for i, b in enumerate(episode.get("beats") or [], start=1):
        af = str(b.get("audioFile") or "")
        name = af.split("/")[-1] if af else ""
        items.append({
            "index": i,
            "zh": str(b.get("zh") or ""),
            "scene": str(b.get("scene") or ""),
            "audio_relpath": f"04_tts/{name}" if name else "",
            "audio_start": b.get("audioStart"),
            "audio_end": b.get("audioEnd"),
        })
    return items


def _run_tts_gen_015(
    *,
    script: Path,
    episode_path: Path,
    audio_dir: Path,
    on_line: Callable[[str], None],
    only: str | None = None,
    force: bool = False,
) -> None:
    """同步调 015 tts_gen.py 按 scene 整段合成 + 写回 episode.json 时间戳。
    only: 只跑指定 scene（单 scene 重生）；force: 覆盖已存在产物。
    行级转发 stdout；失败把末尾输出塞进 RuntimeError。
    """
    cmd = [
        sys.executable, str(script),
        "--episode", str(episode_path.resolve()),
        "--audio-dir", str(audio_dir.resolve()),
        "--workers", "6",
    ]
    if only:
        cmd += ["--only", only]
    if force:
        cmd += ["--force"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in iter(proc.stdout.readline, ""):
        s = line.rstrip("\n")
        if s:
            on_line(s)
            tail.append(s)
            if len(tail) > 20:
                tail.pop(0)
    proc.stdout.close()
    code = proc.wait(timeout=3600)
    if code != 0:
        snippet = "\n".join(tail).strip()
        raise RuntimeError(f"tts_gen.py exited {code}\n--- last output ---\n{snippet}")


def _asr_stage_label(line: str) -> str | None:
    """从 video_pipeline.py 的 stdout 行识别当前阶段，给作品级状态行做实时 stage 文案。
    polish（opus 整理）那步不在 video_pipeline 里，由 _execute_asr 单独设置。
    """
    s = line or ""
    if not s:
        return None
    if re.search(r"✅\s*转写|转写完成|whisper|转写", s, re.IGNORECASE):
        return "语音转写"
    if re.search(r"提取音频|extract.*audio|ffmpeg.*audio", s, re.IGNORECASE):
        return "提取音频"
    if re.search(r"下载|download|TikHub|yt-dlp|复用.*缓存", s, re.IGNORECASE):
        return "下载视频"
    return None


def _run_video_pipeline(
    *,
    pipeline_script: Path,
    url: str,
    output_dir: Path,
    on_line: Callable[[str], None],
) -> None:
    """同步调 video_pipeline.py，行级转发 stdout 给 on_line。在 to_thread 里跑。
    失败时把最后几行输出塞进 RuntimeError，避免前端只看到一个干瘪的 exit code。
    """
    proc = subprocess.Popen(
        [sys.executable, str(pipeline_script), "-o", str(output_dir), url],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in iter(proc.stdout.readline, ""):
        s = line.rstrip("\n")
        if s:
            on_line(s)
            tail.append(s)
            if len(tail) > 20:
                tail.pop(0)
    proc.stdout.close()
    code = proc.wait(timeout=3600)
    if code != 0:
        snippet = "\n".join(tail).strip()
        raise RuntimeError(f"video_pipeline.py exited {code}\n--- last output ---\n{snippet}")


def _polish_transcript_with_opus(
    *,
    transcript_path: Path,
    output_path: Path,
    title_hint: str = "",
) -> None:
    """调本机 opus launcher（Claude Opus 4.7）把语音转写原稿整理成 markdown 文章。

    透传到 claude CLI，参数参考远程 video_rewrite_runner.runClaudeCli：
      claude -p <prompt> --output-format json --model claude-opus-4-7
             --permission-mode bypassPermissions --tools '' --no-session-persistence

    Claude CLI 的 JSON 输出末行形如 {"type":"result","is_error":false,"result":"<markdown>"}。
    我们逐行扫描，取最后一条 type=result 的 payload。
    """
    text = transcript_path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"transcript empty: {transcript_path}")

    hint = f"（参考标题：{title_hint}）" if title_hint else ""
    prompt = (
        "下面是一段语音转写得到的中文原稿，请把它整理成易读的中文文章" + hint + "。\n"
        "整理要求：\n"
        "1. 修正错别字、口误、明显的同音字错误；\n"
        "2. 补全 / 修正标点符号；\n"
        "3. 合理分段，每段表达一个相对完整的意思；\n"
        "4. 若内容较长，可在合适位置加 2-4 个二级标题（## 标题）；\n"
        "5. 保留原意，不要增删事实，不要添加你自己的总结或评论；\n"
        "6. 输出 Markdown 格式，不要加代码块包裹，不要加任何前言或后记。\n\n"
        "【原稿】\n" + text
    )

    launcher = "opus"  # 本机 sclaude 启动器壳
    # --no-resume:  强制开新会话；否则 launcher 会 resume cwd 下最近的 claude session，
    #               把无关的旧上下文带进来污染输出（实测会让 claude 输出元话题回答）
    # --no-session-persistence: claude CLI 自身的开关，防止本次会话留痕影响后续
    args = [
        launcher, "launch", "--no-resume", "--",
        "-p", prompt,
        "--output-format", "json",
        "--model", "claude-opus-4-7",
        "--permission-mode", "bypassPermissions",
        "--tools", "",
        "--no-session-persistence",
    ]
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=600,
        stdin=subprocess.DEVNULL,  # 防止父进程残留 stdin 流入 claude CLI
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"opus launcher exited {proc.returncode}: {tail}")

    # 逐行扫描，找最后一条 type=result
    final_text = ""
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "result":
            continue
        if payload.get("is_error"):
            raise RuntimeError(f"claude returned error: {payload.get('result')}")
        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            final_text = result.strip()
    if not final_text:
        raise RuntimeError(f"opus returned empty result; stdout tail={proc.stdout[-300:]}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# RW 4 模型并行调度（替代旧 content_rewrite_runner.mjs 路径）
# ---------------------------------------------------------------------------

# 4 模型 candidate 表。runner 决定调用方式：
#   - opus    : `opus launch -- ...` 透传到 claude CLI（Claude Opus 4.7）
#   - scodex  : `scodex launch -- ...` 透传到 codex CLI（GPT-5.5）
#   - gemini  : `~/.gemini/g.sh` —— 本机未安装则直接标"模型不可用"
#   - deepseek: HTTP POST 到 deepseek API —— 需 DEEPSEEK_API_KEY，未设则标"模型不可用"
MODEL_CANDIDATES: list[dict[str, str]] = [
    {"id": "opus",         "label": "Claude Opus 4.7",   "runner": "opus",         "model": "claude-opus-4-7"},
    {"id": "gpt5",         "label": "GPT-5.5",           "runner": "scodex",       "model": "gpt-5.5"},
    {"id": "gemini_local", "label": "GEMINI-3.5 FLASH",  "runner": "gemini_local", "model": ""},
    {"id": "deepseek",     "label": "DeepSeek V4 Pro",   "runner": "deepseek",     "model": "deepseek-v4-pro"},
]


class _ModelUnavailable(Exception):
    """专用 sentinel：模型在本机不可用（缺二进制 / 缺 API key 等）。
    与"调用失败" 区别开 —— 不可用的不应该上报为运行时错误，而是稳定的状态。
    """


def _check_model_available(cand: dict[str, str]) -> tuple[bool, str]:
    """返回 (是否可用, 不可用原因)。可用时 reason='' 。"""
    runner = cand["runner"]
    if runner == "opus":
        return (shutil.which("opus") is not None, "本机未安装 opus 启动器")
    if runner == "scodex":
        return (shutil.which("scodex") is not None, "本机未安装 scodex 启动器")
    if runner == "gemini_local":
        p = Path.home() / ".gemini" / "g.sh"
        return (p.is_file(), "~/.gemini/g.sh 未安装")
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
    """对单个 candidate：可用就调，返回 raw text；不可用就 raise _ModelUnavailable。
    被 asyncio.gather(return_exceptions=True) 包住，让一个失败不影响其他模型。
    on_status(model_id, status) 推送模型级状态：running / done / failed / unavailable。
    """
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
            # codex 不支持独立 system prompt 通道 —— 按远程 buildCodexCliPrompt 的
            # 「目标类型 / 系统角色 / 任务要求 / 硬性输出约束」四段结构拼成一个 user prompt。
            # RW 阶段出 markdown 文章，所以 expect_json=False，否则 codex 会把文章
            # 包进 {"content": "..."} JSON 返回。
            combined = _build_codex_user_prompt(
                system_prompt=system_prompt,
                task_prompt=user_prompt,
                target_profile="paper_card_talk",
                expect_json=False,
            )
            text = await asyncio.to_thread(_call_scodex_for_rw, combined, cand["model"])
        elif runner == "deepseek":
            text = await asyncio.to_thread(_call_deepseek_for_rw, user_prompt, system_prompt, cand["model"])
        else:
            # 其他 runner（gemini_local 等）真要接时在这里加分支。
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
    """走本机 opus 启动器 → claude CLI。沿用 _polish_transcript_with_opus 的
    --no-resume / --no-session-persistence / stdin=DEVNULL 套路防止会话污染。
    """
    args = [
        "opus", "launch", "--no-resume", "--",
        "-p", user_prompt,
        "--output-format", "json",
        "--model", model_id,
        "--permission-mode", "bypassPermissions",
        "--tools", "",
        "--no-session-persistence",
    ]
    if system_prompt:
        args.extend(["--system-prompt", system_prompt])
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=900,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"opus launcher exited {proc.returncode}: {tail}")

    final = ""
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "result":
            continue
        if payload.get("is_error"):
            raise RuntimeError(f"claude error: {payload.get('result')}")
        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            final = result.strip()
    if not final:
        raise RuntimeError(f"opus empty result; stdout tail={proc.stdout[-300:]}")
    return final


def _call_scodex_for_rw(prompt: str, model_id: str) -> str:
    """走本机 scodex 启动器 → codex CLI。--json 输出 NDJSON 流，取最后一条
    type=item.completed 的 item.text 作为最终回答（参考远程 extractCodexJsonText）。
    """
    # 注意：不要传 `-a never`。scodex launcher 自己会注入
    # `--dangerously-bypass-approvals-and-sandbox`，再加 `-a` 会触发
    # "the argument ... cannot be used with --ask-for-approval" 冲突。
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
        timeout=900,
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
    """对齐远程 video_rewrite_runner.buildCodexCliPrompt 的四段结构。

    codex CLI 没有独立的 system 通道（--json + exec 模式），把"系统角色"+
    "硬性输出约束"显式拼进 user prompt，比单纯放 [系统提示] 标签更稳。
    """
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
    """走 DeepSeek HTTP API（OpenAI 兼容协议）。参数沿用远程 runDeepSeekChat：
    thinking.enabled=true + reasoning_effort=high，吃 reasoner 模型。

    模型字段示例：'deepseek-v4-pro'。若 API 返回 4xx 说明型号名失效，
    在 .env 或 MODEL_CANDIDATES 里调整。
    """
    import httpx

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY missing")

    messages: list[dict[str, str]] = []
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": user_prompt})

    body = {
        "model": model_id,
        "messages": messages,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    try:
        resp = httpx.post(
            "https://api.deepseek.com/chat/completions",
            json=body,
            headers=headers,
            timeout=900.0,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"deepseek HTTP error: {exc}") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"deepseek http {resp.status_code}: {resp.text[:500]}")
    try:
        payload = resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"deepseek non-json response: {resp.text[:500]}") from exc

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"deepseek empty choices; payload tail={resp.text[-300:]}")
    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        # 极端情况看 reasoning_content
        reasoning = (message.get("reasoning_content") or "").strip()
        if not reasoning:
            raise RuntimeError(f"deepseek returned empty content; message keys={list(message.keys())}")
        return reasoning
    return content


# RW 体裁 profile（对应飞书 /rw -p 参数）。每个 profile 给一段「体裁定调」task 正文，
# 复刻 scripts/rewrite_profiles.mjs 各 profile 的 draft prompt 精华，但统一要求输出
# markdown 文章（RW 抽屉通读 + LINES 再切 beats）。freestyle 不限定体裁。
RW_PROFILE_META: dict[str, str] = {
    "toutiao": "头条图文",
    "caijing": "抖音财经",
    "jitang": "心灵鸡汤",
    "freestyle": "自由发挥",
}

_RW_PROFILE_BODY: dict[str, list[str]] = {
    "toutiao": [
        "你是【今日头条】爆款图文写手。请把下面的源文档改写成一篇可直接发布的头条图文稿。",
        "",
        "【体裁要求】",
        "- 开头黄金 3 秒抛钩子：用反差 / 反常识结论 / 数据冲击抓住注意力；",
        "- 正文分 3-5 个大板块层层推进，板块小标题用 `## `；",
        "- 善用对比、反转、追问句式；信息密度高、可读性强；",
        "- 结尾收束 + 自然引导评论互动；",
        "- 正文 1800-2000 字。",
    ],
    "caijing": [
        "你是抖音财经口播稿的资深写手。请把下面的源文档改写成一篇抖音财经口播稿。",
        "",
        "【体裁要求】",
        "- 开头钩子直接抛出核心反差或反常识结论，黄金 3 秒抓注意力；",
        "- 专业但通俗，把复杂财经逻辑讲得人人能懂；",
        "- 节奏感强，善用对比、反转、追问；适当复用金句结构；",
        "- 结尾落到低门槛行动号召，自然引导互动；",
        "- 正文 1800-2000 字。",
    ],
    "jitang": [
        "你是擅长写抖音情绪向口播稿的中文写手。请把下面的源文档改写成一篇情绪共鸣口播稿。",
        "",
        "【体裁要求】",
        "- 以第二人称「你」为主语，拉近距离；",
        "- 用具体生活画面替换抽象形容词，让情绪可感；",
        "- 不要用财经术语包装，不要用「加油 / 你可以的 / 慢慢来」之类的空泛鸡汤套话；",
        "- 节奏起伏，结尾给一句有力量的收束；",
        "- 正文 1800-2200 字。",
    ],
    "freestyle": [
        "你是资深中文内容写手。请根据下面源文档的内容与气质自由发挥，写一篇高质量的中文文章。",
        "",
        "【体裁要求】",
        "- 体裁、风格、结构不限，由你判断什么最适合这份素材；",
        "- 保证可读性：合理分段，必要时加 `## ` 小标题；",
        "- 有清晰的开头钩子和结尾收束。",
    ],
}

DEFAULT_RW_PROFILE = "freestyle"


def _build_rw_prompt(
    profile: str,
    source_text: str,
    user_requirements: str = "",
) -> tuple[str, str]:
    """按体裁 profile 构造 RW 的 (system_prompt, user_prompt)。

    本阶段产物 = 候选稿 markdown 文章（**不是** beats[] JSON）。RW 关心体裁与叙事质量，
    LINES 阶段才把定稿切成 beats[]。未知 profile 回退到 freestyle。
    """
    profile = profile if profile in _RW_PROFILE_BODY else DEFAULT_RW_PROFILE
    label = RW_PROFILE_META.get(profile, profile)
    system_prompt = (
        f"你是中文内容改写的资深写手，本次目标体裁是「{label}」。"
        "请输出 markdown 正文，保留原意、不编造事实，不要输出 JSON 或代码块包裹。"
    )
    parts: list[str] = list(_RW_PROFILE_BODY[profile])
    parts += [
        "",
        "【通用约束】",
        "- 必须使用简体中文；",
        "- 直接输出 markdown 正文，不要 JSON、不要 ``` 代码块包裹、不要额外的元描述；",
        "- 不得编造源文档未出现的人物、平台、数据；只能改写、压缩、重组源文档信息。",
        "",
        "== 源文档 ==",
        source_text,
        "== 源文档结束 ==",
    ]
    if user_requirements.strip():
        parts += [
            "",
            "【用户附加要求（最高优先级，可覆盖以上默认要求）】：",
            user_requirements.strip(),
        ]
    return system_prompt, "\n".join(parts)


def _build_lines_prompt(draft_md: str) -> tuple[str, str]:
    """LINES 阶段：把 RW 定稿 markdown 文章结构化成 paper-card-talk 的
    {meta, beats, scenes} JSON。beats 是逐句字幕，scenes 给每个画面配出图 prompt。
    """
    system_prompt = (
        "你是 paper-card-talk 短视频脚本结构化助手。把给定文章拆成短视频的逐句字幕"
        "（beats）和分镜（scenes）。只输出一个合法 JSON 对象，禁止代码块或任何额外文本。"
    )
    user_prompt = "\n".join([
        "把下面这篇文章结构化成 paper-card-talk 短视频脚本 JSON。",
        "",
        "【输出格式】只输出一个 JSON 对象，结构严格如下，不要代码块包裹、不要解释：",
        "{",
        '  "meta": { "title": "短标题（≤20字）", "subtitle": "", "tags": [] },',
        '  "beats": [',
        '    { "zh": "单句中文字幕", "en": "英文翻译（可空串）", "scene": "scene_id", "chapter": 整数或null }',
        "  ],",
        '  "scenes": {',
        '    "scene_id": { "prompt": "该画面的图像生成提示词" }',
        "  }",
        "}",
        "",
        "【beats 要求】",
        "- 把文章正文切成单句字幕，每句 10-30 字，朗朗上口、可朗读；",
        "- 全篇 30-80 条；不要把整段塞进一条；",
        "- scene 命名：开场用 intro，结尾用 outro，正文按章节用 chap1_xxx / chap2_xxx（xxx 是语义后缀，如 chap1_hook）；",
        "- 同一个 scene 下可以有多条连续 beat（共享一张图）；",
        "- 每个章节的首条 beat 标 chapter 编号（1..N），其余 beat 的 chapter 写 null；",
        "- 只能改写、压缩、重组文章信息，不得编造文章未出现的人物 / 数据 / 平台。",
        "",
        "【scenes 要求】",
        "- 必须覆盖 beats 里出现的每一个 scene_id；",
        "- 每个 scene 的 prompt 用中文描述画面：扁平插画风格、米黄纸质底、克制配色、画面留白方便贴字幕；",
        "- prompt 里不要出现任何文字 / 数字（图上不放字）。",
        "",
        "== 文章 ==",
        draft_md,
        "== 文章结束 ==",
    ])
    return system_prompt, user_prompt


def _load_template_episode(pipeline_id: str = "paper_card_talk_014") -> dict[str, Any]:
    """读对应模板自带 episode.json 作为 LINES 结构化输出的骨架（保留 audio/visual/
    playback/fonts/image 等渲染配置，只覆盖 meta/beats/scenes）。"""
    if pipeline_id == "paper_card_talk_015":
        tpl = (
            Path(__file__).resolve().parents[1]
            / "templates" / "paper_card_talk_015"
            / ".015-draft-assets" / "episode.json"
        )
    else:
        tpl = (
            Path(__file__).resolve().parents[1]
            / "templates" / "paper_card_talk_014"
            / ".014-draft-assets" / "episode.json"
        )
    return json.loads(tpl.read_text(encoding="utf-8"))


def _generate_scene_image(
    *,
    scene_id: str,
    prompt: str,
    size: str,
    quality: str,
    target: Path,
    job_id: str,
) -> None:
    """单 scene 出图：subprocess 调 gpt_image_gen.py → Pillow PNG→WebP → 落 target。

    复刻 ~/projects/ncds-materials/.014-draft-assets/pic_gen.py 的 generate_one()
    逻辑。在 to_thread 里被调，整个函数纯同步、纯 IO。
    """
    # gpt_image_gen.py 路径：repo_root/gpt_image/gpt_image_gen.py
    # pipeline_runner.py 在 src/ncds_opus_factory/server/，向上 3 层到 repo_root
    gen_script = Path(__file__).resolve().parents[3] / "gpt_image" / "gpt_image_gen.py"
    if not gen_script.is_file():
        raise RuntimeError(f"gpt_image_gen.py not found at {gen_script}")

    gen_out_dir = Path("/tmp") / "gpt-image" / f"job-{job_id}-{scene_id}"
    shutil.rmtree(gen_out_dir, ignore_errors=True)
    gen_out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(gen_script),
        "--out-dir", str(gen_out_dir),
        "--size", size,
        "--quality", quality,
        "--overwrite",
        "--prompt", prompt,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        tail = (res.stderr or res.stdout or "").strip()[-500:]
        raise RuntimeError(f"gpt-image gen failed: {tail}")

    local_png = gen_out_dir / "image_01.png"
    if not local_png.is_file():
        raise RuntimeError(f"expected {local_png} not found after gen")

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow not installed; pip install Pillow") from exc

    img = Image.open(local_png).convert("RGB")
    tmp = target.with_suffix(target.suffix + ".part")
    img.save(tmp, format="WEBP", quality=85, method=6)
    tmp.rename(target)
    shutil.rmtree(gen_out_dir, ignore_errors=True)


def _read_episode(job_dir: Path) -> dict[str, Any] | None:
    """读 job_dir/02_rw/episode.json，找不到或解析失败返回 None。"""
    ep_path = job_dir / "02_rw" / "episode.json"
    if not ep_path.exists():
        return None
    try:
        return json.loads(ep_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_template_fonts() -> list[dict[str, Any]]:
    """从模板自带 episode.json 拉 fonts 数组。

    字体清单是模板资源声明（决定浏览器 @font-face 注入 + Inspector 字体下拉选项），
    不是 rw 模型该决定的内容。所以 rw 产出 episode.json 时直接 inherit 模板的字体清单。
    单一真理源 = templates/paper_card_talk_014/.014-draft-assets/episode.json#fonts。
    """
    tpl_ep = (
        Path(__file__).resolve().parents[1]
        / "templates" / "paper_card_talk_014"
        / ".014-draft-assets" / "episode.json"
    )
    try:
        ep = json.loads(tpl_ep.read_text(encoding="utf-8"))
        fonts = ep.get("fonts")
        return fonts if isinstance(fonts, list) else []
    except (OSError, json.JSONDecodeError):
        return []


