"""Pipeline runner：把固定步骤的 DAG 跑成"每节点一个任务 + 状态机 + SSE"的形态。

设计要点
--------
- 每个 job 对应一个 `video-jobs/{job_id}/pipeline_state.json`，作为唯一真相源。
- 节点状态 idle → queued → running → done | failed；重跑某节点会把它及所有下游
  节点 reset 回 idle（产物保留磁盘上由用户决定是否清理）。
- 真实执行模式：spawn 一个 asyncio task，调 commands/<cmd>.run；进度通过 on_progress
  追加到 events.jsonl。任务完成后 update node status + outputs。
- Mock 模式（NOF_PIPELINE_MOCK=1，默认 ON）：节点用 asyncio.sleep 模拟执行，
  直接产出假 outputs。用于前端独立联调。
- 状态变更广播给内存 SSE pub/sub，订阅者从 asyncio.Queue 读事件。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ncds_opus_factory.pipelines import PIPELINE_REGISTRY, PipelineDef, get_pipeline

logger = logging.getLogger(__name__)

# Mock 模式默认开，让前端可以独立联调。后续接入真实命令时关掉。
PIPELINE_MOCK = os.environ.get("NOF_PIPELINE_MOCK", "1") == "1"


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
        return JobState(
            job_id=data["job_id"],
            pipeline_id=data["pipeline_id"],
            title=data.get("title", ""),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            inputs=data.get("inputs", {}),
            nodes=nodes,
            node_positions=data.get("node_positions", {}),
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
            title=title or f"未命名作品 {job_id[:6]}",
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
        # 用户改了 episode → 等于 rw 节点重新有效；同时 invalidate wst/tts/render
        state = self._load(job_id)
        if "rw" in state.nodes:
            state.nodes["rw"].status = "done"
            state.nodes["rw"].outputs = {"episode_path": str(ep)}
            state.nodes["rw"].finished_at = time.time()
        for n in get_pipeline(state.pipeline_id).downstream_of("rw"):
            if state.nodes[n].status != "idle":
                self._reset_node(state.nodes[n])
        self._save(state)
        self.bus.publish(state.job_id, {"type": "job_updated", "job_id": state.job_id})

    def update_node_position(self, job_id: str, node: str, x: float, y: float) -> None:
        state = self._load(job_id)
        state.node_positions[node] = {"x": x, "y": y}
        self._save(state)
        # 位置变更不广播事件，前端自己掌握；下次 GET 时拿到

    # ---------- 重跑 / 调度 ----------

    def _reset_node(self, n: NodeState) -> None:
        n.status = "idle"
        n.started_at = None
        n.finished_at = None
        n.progress = ""
        n.outputs = {}
        n.error = None
        n.task_id = None

    async def run_node(self, job_id: str, node_name: str) -> None:
        """触发某节点执行。会把节点及其下游全部 reset 后再排队。"""
        state = self._load(job_id)
        pipeline = get_pipeline(state.pipeline_id)
        node = pipeline.node(node_name)
        if node.kind in ("input", "output"):
            raise ValueError(f"node {node_name} is UI-only, not runnable")

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
            if PIPELINE_MOCK:
                await self._execute_mock(job_id, node_name)
            else:
                await self._execute_real(job_id, node_name)
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

    async def _execute_mock(self, job_id: str, node_name: str) -> None:
        """Mock 模式：sleep 模拟，落假 outputs。前端联调用。"""
        state = self._load(job_id)
        n = state.nodes[node_name]
        n.status = "running"
        n.started_at = time.time()
        n.progress = "mock 开始..."
        self._save(state)
        self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

        # 模拟 3-5s 处理时间，中间推几条 progress
        steps = random.randint(3, 5)
        for i in range(steps):
            await asyncio.sleep(random.uniform(0.5, 1.2))
            state = self._load(job_id)
            n = state.nodes[node_name]
            n.progress = f"mock 进度 {i+1}/{steps}"
            self._save(state)
            self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

        # 产假 outputs
        outputs = _mock_outputs(node_name, job_id, self.video_jobs_dir)
        state = self._load(job_id)
        n = state.nodes[node_name]
        n.status = "done"
        n.finished_at = time.time()
        n.progress = "mock 完成"
        n.outputs = outputs
        self._save(state)
        self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

    async def _execute_real(self, job_id: str, node_name: str) -> None:
        """真实执行：调 commands/<cmd>.run。TODO：参数装配每个 node 略有不同。"""
        # 留到联调阶段补。此处暂用 mock 兜底，避免接入半完成态。
        await self._execute_mock(job_id, node_name)


# ---------------------------------------------------------------------------
# Mock outputs（前端联调用，结构尽量贴近真实）
# ---------------------------------------------------------------------------

def _mock_outputs(node_name: str, job_id: str, video_jobs_dir: Path) -> dict[str, Any]:
    """按节点 name 产生形态合理的假 outputs，并把可读文件落盘。"""
    job_dir = video_jobs_dir / job_id

    if node_name == "asr":
        out_dir = job_dir / "01_asr"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "transcript.md").write_text(
            "# 听写稿（mock）\n\n这是一段假转写稿，用于前端联调。\n", encoding="utf-8"
        )
        (out_dir / "key_points.json").write_text(
            json.dumps({"points": ["要点 A", "要点 B", "要点 C"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "transcript_path": str(out_dir / "transcript.md"),
            "key_points_path": str(out_dir / "key_points.json"),
            "feishu_doc_url": "https://example.feishu.cn/docs/mock",
        }

    if node_name == "rw":
        out_dir = job_dir / "02_rw"
        out_dir.mkdir(parents=True, exist_ok=True)
        episode = _mock_episode()
        (out_dir / "episode.json").write_text(
            json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "episode_path": str(out_dir / "episode.json"),
            "beats_count": len(episode["beats"]),
            "scenes_count": len(episode["scenes"]),
        }

    if node_name == "wst":
        out_dir = job_dir / "03_wst"
        out_dir.mkdir(parents=True, exist_ok=True)
        return {
            "pictures_dir": str(out_dir),
            "pictures_count": 0,  # mock 不真生图
            "note": "mock：联调阶段不真调 gpt-image",
        }

    if node_name == "tts":
        out_dir = job_dir / "04_tts"
        out_dir.mkdir(parents=True, exist_ok=True)
        return {
            "audio_dir": str(out_dir),
            "audio_count": 0,  # mock 不真调 TTS
            "note": "mock：联调阶段不真调 dashscope-cosyvoice",
        }

    if node_name == "render":
        out_dir = job_dir / "05_render"
        out_dir.mkdir(parents=True, exist_ok=True)
        return {
            "output_path": str(out_dir / "011.mp4"),
            "note": "mock：联调阶段不真渲染",
        }

    return {}


def _mock_episode() -> dict[str, Any]:
    return {
        "__schema__": "ncds-paper-card-talk/v1",
        "meta": {
            "slug": "mock-episode",
            "title": "示例作品（mock）",
            "brandTitle": "示例作品",
            "disclaimer": "本视频内容仅为示例。",
            "titleOptions": ["示例作品", "Mock Demo"],
        },
        "fonts": [],
        "visual": {
            "palette": "paper",
            "bandStyle": "paper",
            "kenBurns": True,
            "showSubtitleEn": True,
            "capZhSize": 60,
            "capEnSize": 40,
        },
        "playback": {"rate": 0.95},
        "audio": {
            "tts": {
                "engine": "dashscope-cosyvoice",
                "model": "cosyvoice-v3-flash",
                "voice": "longtian_v3",
                "sampleRate": 22050,
                "format": "mp3",
                "rate": 1.1,
            }
        },
        "image": {
            "engine": "gpt-image-2",
            "size": "1536x1024",
            "quality": "auto",
        },
        "beats": [
            {"zh": "这是第一句字幕（mock）", "en": "This is the first caption (mock)", "scene": "intro"},
            {"zh": "这是第二句字幕。", "en": "This is the second caption.", "scene": "intro"},
            {"zh": "这是第三句字幕。", "en": "This is the third caption.", "scene": "body"},
        ],
        "scenes": {
            "intro": {
                "prompt": "扁平插画。米黄纸质底色，中央一支立着的钢笔，旁边翻开的笔记本，留白干净。",
                "label": "",
                "motion": {"enter": "fade", "duration": 700},
                "overlays": [],
            },
            "body": {
                "prompt": "扁平插画。一条延伸的小路，远处地平线一抹暖光。",
                "label": "",
                "motion": {"enter": "zoom-in", "duration": 700},
                "overlays": [],
            },
        },
    }
