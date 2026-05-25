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
import hashlib
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

    def rewrite_rw_model(self, job_id: str, model_id: str) -> None:
        """只重写 rw 单个模型的 draft（不动另一个、不动下游）。
        当前仅 mock 实现：覆写 02_rw/{model_id}/draft.md + episode.json。
        """
        state = self._load(job_id)
        n = state.nodes.get("rw")
        if n is None:
            raise KeyError("rw node not found")
        if n.status != "done":
            raise ValueError("rw node not done; run rw first")
        label = dict(MOCK_RW_MODELS).get(model_id)
        if label is None:
            raise ValueError(f"unknown model: {model_id}")
        out_dir = self.video_jobs_dir / job_id / "02_rw"
        _write_rw_model_artifacts(out_dir, model_id, label)
        state.updated_at = time.time()
        self._save(state)
        self.bus.publish(job_id, {"type": "job_updated", "job_id": job_id})

    def select_rw_model(self, job_id: str, model_id: str) -> None:
        """用户在 rw 抽屉里选定一个模型作为下游 image 的入口。
        把 02_rw/{model_id}/episode.json 拷贝到 02_rw/episode.json；
        把 selected_model_id 写入 outputs。
        """
        state = self._load(job_id)
        n = state.nodes.get("rw")
        if n is None:
            raise KeyError("rw node not found")
        if n.status != "done":
            raise ValueError("rw node not done")
        drafts = (n.outputs or {}).get("drafts") or []
        valid_ids = {d.get("model_id") for d in drafts if isinstance(d, dict)}
        if model_id not in valid_ids:
            raise ValueError(f"unknown model: {model_id}")
        out_dir = self.video_jobs_dir / job_id / "02_rw"
        src = out_dir / model_id / "episode.json"
        dst = out_dir / "episode.json"
        if not src.exists():
            raise FileNotFoundError(f"missing source episode: {src}")
        dst.write_bytes(src.read_bytes())
        n.outputs["selected_model_id"] = model_id
        state.updated_at = time.time()
        self._save(state)
        self.bus.publish(job_id, {"type": "node_status", "job_id": job_id, "node": "rw", "state": asdict(n)})

    async def cancel_node(self, job_id: str, node_name: str) -> bool:
        """取消正在跑的节点 task。返回是否真的取消了。"""
        key = (job_id, node_name)
        task = self._running_nodes.get(key)
        if task is None or task.done():
            return False
        task.cancel()
        return True

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
        outputs = _mock_outputs(node_name, job_id, self.video_jobs_dir, state.inputs)
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

def _mock_outputs(
    node_name: str,
    job_id: str,
    video_jobs_dir: Path,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """按节点 name 产生形态合理的假 outputs，并把可读文件落盘。"""
    job_dir = video_jobs_dir / job_id

    if node_name == "asr":
        out_dir = job_dir / "01_asr"
        out_dir.mkdir(parents=True, exist_ok=True)

        # 从 inputs 抽出每条视频；shares 优先（带 title/author），urls 兜底
        shares: list[dict[str, Any]] = []
        for s in inputs.get("shares") or []:
            if isinstance(s, dict) and s.get("url"):
                shares.append(s)
        if not shares:
            urls = inputs.get("urls") or ([inputs.get("url")] if inputs.get("url") else [])
            shares = [{"url": u, "title": "", "author": "", "tags": []} for u in urls if u]
        if not shares:
            shares = [{"url": "https://v.douyin.com/mock-001", "title": "示例视频", "author": "示例作者", "tags": []}]

        items: list[dict[str, Any]] = []
        for idx, s in enumerate(shares, start=1):
            url = s.get("url", "")
            # title / author 由前端解析得到；缺失就给空字符串，UI 自决定隐藏
            title = (s.get("title") or "").strip()
            author = (s.get("author") or "").strip()
            slug = f"{idx:02d}-{hashlib.md5(url.encode('utf-8')).hexdigest()[:6]}"
            item_dir = out_dir / slug
            item_dir.mkdir(parents=True, exist_ok=True)
            (item_dir / "transcript.md").write_text(
                f"# 听写稿（mock）\n\n来源: {url}\n"
                + (f"作者: {author}\n" if author else "")
                + "\n大家好这是一段模拟的口语转写文本它没有标点符号也没有分段\n"
                "就像 ASR 直接吐出来的样子主要用于前端联调展示\n",
                encoding="utf-8",
            )
            (item_dir / "article.md").write_text(
                f"# 文章解析（mock）\n\n来源: {url}\n\n"
                "## 第一段\n\n这是经过清洗、加标点、分段后的文章解析示例。\n\n"
                "## 第二段\n\n演示多段排版与结构化呈现。\n",
                encoding="utf-8",
            )
            (item_dir / "highlight.md").write_text(
                "# 精华提取（mock）\n\n"
                "- 要点 1：核心结论 A\n"
                "- 要点 2：核心结论 B\n"
                "- 要点 3：核心结论 C\n",
                encoding="utf-8",
            )
            items.append({
                "index": idx,
                "url": url,
                "title": title,
                "author": author,
                "transcript_relpath": f"01_asr/{slug}/transcript.md",
                "article_relpath": f"01_asr/{slug}/article.md",
                "highlight_relpath": f"01_asr/{slug}/highlight.md",
            })

        return {
            "items": items,
            "feishu_doc_url": "https://example.feishu.cn/docs/mock",
        }

    if node_name == "rw":
        out_dir = job_dir / "02_rw"
        out_dir.mkdir(parents=True, exist_ok=True)
        # 双模型改写：各写一份 draft.md + episode.json 到子目录
        # selected_model_id 在用户点 tab 内"下一步"时由 select 端点写入
        drafts: list[dict[str, Any]] = []
        for model_id, label in MOCK_RW_MODELS:
            _write_rw_model_artifacts(out_dir, model_id, label)
            drafts.append({
                "model_id": model_id,
                "label": label,
                "draft_relpath": f"02_rw/{model_id}/draft.md",
                "episode_relpath": f"02_rw/{model_id}/episode.json",
            })
        # 02_rw/episode.json 留空；用户 select 后由 select 端点拷贝
        return {
            "drafts": drafts,
            "selected_model_id": None,
        }

    if node_name == "image":
        out_dir = job_dir / "03_image"
        out_dir.mkdir(parents=True, exist_ok=True)
        return {
            "pictures_dir": str(out_dir),
            "pictures_count": 0,
            "note": "mock：联调阶段不真调 wst",
        }

    if node_name == "tts":
        out_dir = job_dir / "04_tts"
        out_dir.mkdir(parents=True, exist_ok=True)
        return {
            "audio_dir": str(out_dir),
            "audio_count": 0,
            "note": "mock：联调阶段不真调 dashscope-cosyvoice",
        }

    if node_name == "preview":
        out_dir = job_dir / "05_preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        return {
            "preview_url": f"/preview/{job_dir.name}/011-reading-confidence.html",
            "approved": True,
            "note": "mock：用户审核通过，下游可跑 render",
        }

    if node_name == "render":
        out_dir = job_dir / "06_render"
        out_dir.mkdir(parents=True, exist_ok=True)
        return {
            "output_path": str(out_dir / "011.mp4"),
            "note": "mock：联调阶段不真渲染",
        }

    return {}


MOCK_RW_MODELS: list[tuple[str, str]] = [
    ("gpt5", "GPT-5.5"),
    ("gemini", "GEMINI-3.5-flash"),
]


def _write_rw_model_artifacts(out_dir: Path, model_id: str, label: str) -> None:
    """写一个模型的 draft.md + episode.json。重写单模型时可单独调。"""
    item_dir = out_dir / model_id
    item_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    (item_dir / "draft.md").write_text(
        f"# {label} 改写稿（mock · #{timestamp}）\n\n"
        "## 标题\n\n示例作品（mock 改写）\n\n"
        "## 正文\n\n"
        f"这是 {label} 模型生成的改写稿示例。\n"
        "用户可以直接在右侧 textarea 修改，离开后自动保存。\n\n"
        "## 字幕节奏\n\n"
        "- 第一句：开场引入\n- 第二句：核心观点\n- 第三句：行动呼吁\n",
        encoding="utf-8",
    )
    episode = _mock_episode()
    episode["meta"]["title"] = f"{label} 改写示例"
    (item_dir / "episode.json").write_text(
        json.dumps(episode, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
