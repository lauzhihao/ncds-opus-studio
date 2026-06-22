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
- render：已真接入（commands/render_015.run）
- lines：UI-only，不在此处执行
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ncds_opus_core.templates import template_dir as _template_dir
from ncds_opus_core.pipelines import PIPELINE_REGISTRY, get_pipeline
from ncds_opus_core.common import cancel as _cancel
from ncds_opus_factory.server import pipeline_media_helpers as media_helpers
from ncds_opus_factory.server import pipeline_rw_helpers as rw_helpers
from ncds_opus_factory.server.pipeline_agent_tasks import PipelineAgentTasksMixin
from ncds_opus_factory.server.pipeline_asr_tasks import PipelineAsrCollectRun
from ncds_opus_factory.server.pipeline_engine_bridge import PipelineEngineBridgeMixin
from ncds_opus_factory.server.pipeline_image_tasks import PipelineImageRun
from ncds_opus_factory.server.pipeline_lines_tasks import PipelineLinesRun
from ncds_opus_factory.server.pipeline_render_tasks import PipelineRenderRun
from ncds_opus_factory.server.pipeline_rw_tasks import PipelineRwRun
from ncds_opus_factory.server.pipeline_storyboard_tasks import PipelineStoryboardRun
from ncds_opus_factory.server.pipeline_tts_tasks import PipelineTtsRun

logger = logging.getLogger(__name__)

DEFAULT_OPUS_MODEL_ID = rw_helpers.DEFAULT_OPUS_MODEL_ID


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
    # mock 作品标志：True 时 _execute 走 _execute_mock（sleep + 015 素材产物），
    # 不打任何真实下游（gpt-image / TTS / LLM）。仅 server.mock 种的 mock015 会置 True。
    mock: bool = False
    # 绞杀者（E1-b2 #3）：该 job 复用的生产引擎实例 iid。持久化以便 server 重启后复用同一实例，
    # 避免每次重启 + 改道运行就新建一个、旧的成磁盘孤儿 + 污染 /instances 视图。
    engine_iid: str | None = None


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

class PipelineRunner(PipelineEngineBridgeMixin, PipelineAgentTasksMixin):
    """每个进程一个 PipelineRunner 单例（state.py 里建）。"""

    def __init__(self, video_jobs_dir: Path) -> None:
        self.video_jobs_dir = video_jobs_dir
        self.video_jobs_dir.mkdir(parents=True, exist_ok=True)
        self.bus = EventBus()
        self._running_nodes: dict[tuple[str, str], asyncio.Task[Any]] = {}
        # asr（沈括采集）快采 done 后，后台补音轨/抠图的 enrich task（按 job_id；重跑 asr cancel 旧的）。
        self._enrich_tasks: dict[str, asyncio.Task[Any]] = {}
        # 进画布触发的「数据/评论」后台刷新 task（按 job_id；同 job 已在刷新则去重）。
        self._refresh_tasks: dict[str, asyncio.Task[Any]] = {}
        # 鬼谷子选题（评论驱动双模型）后台 task（按 job_id；同 job 在跑则复用，不重派）。
        self._guiguzi_tasks: dict[str, asyncio.Task[Any]] = {}
        # 绞杀者（E1-b2 #3）：注入的生产引擎 + 命中节点集。每 job 的引擎实例 iid 持久化在
        # JobState.engine_iid（不放内存，避免重启后重建孤儿实例）。
        self._engine: Any = None
        # S1：events.jsonl 事件落盘计数器。key=job_id，value=已发出的最后 seq。
        # server 重启后首次 _emit 时按文件末行 seq 恢复，保证跨重启单调不回退。
        self._event_seq: dict[str, int] = {}
        # 本分支默认「除 asr 外的可执行节点走生产引擎」。NOF_ENGINE_NODES 可覆盖：
        #   未设                       → rw/lines/storyboard/tts/image/render 走引擎 run_step
        #   "none"/"off"/"legacy"/"" → 全走旧 _execute_*（临时回旧画布调试用）
        #   逗号列表(如 lines,render)   → 只这些节点走引擎，其余走旧
        # 注：asr 执行处仍固定走 legacy,因为它依赖步内 collected 增量与 done 后 enrich。
        _all = {"rw", "lines", "storyboard", "tts", "image", "render"}
        _env = os.getenv("NOF_ENGINE_NODES")
        if _env is None:
            self._engine_nodes: set[str] = set(_all)
        elif _env.strip().lower() in {"", "none", "off", "legacy"}:
            self._engine_nodes = set()
        else:
            self._engine_nodes = {n.strip() for n in _env.split(",") if n.strip()}

    # ---------- S2：跨进程取消文件标记 ----------

    #: watcher 轮询间隔（秒）：0.5s 足够响应，不过度 IO。
    _CANCEL_WATCHER_INTERVAL_SEC: float = 0.5
    #: Option A 宽限期（秒）：flag 命中后等工作线程协作式自停的最长等待时间。
    #: 够 killpg SIGTERM→wait(5)→SIGKILL + 轮询一次子进程退出，超时才 fallback inner.cancel()。
    _CANCEL_GRACE_SEC: float = 15.0

    def _cancel_flag(self, job_id: str, node_name: str) -> Path:
        """返回该节点的取消标记文件路径。

        约定：video-jobs/{job_id}/cancel/{node_name}.flag。
        仅 PipelineRunner 知道目录结构；core cancel 模块只收绝对 Path。
        """
        return self.video_jobs_dir / job_id / "cancel" / f"{node_name}.flag"

    async def _run_in_thread_cancellable(self, fn: Callable, flag_path: Path, /, *args: Any, **kwargs: Any) -> Any:
        """在工作线程里 install 读 is_flagged(flag_path) 的协作式取消 checker 后调 fn。
        镜像 task_runner._invoke：命令在步骤边界 cancel.checkpoint() / 子进程 Popen 轮询
        cancel.current() 命中即中止。checker 读跨进程文件 flag（而非内存），故 worker 拆分后天然可达。"""
        def _wrapped() -> Any:
            # install 的 checker 是文件存在性检查，与内存状态无关——跨进程 worker 侧也能触发
            _cancel.install(lambda: _cancel.is_flagged(flag_path))
            try:
                return fn(*args, **kwargs)
            finally:
                _cancel.uninstall()
        return await asyncio.to_thread(_wrapped)

    # ---------- S1：events.jsonl 落盘 ----------

    def _events_file(self, job_id: str) -> Path:
        """返回该 job 的事件日志路径：video-jobs/{job_id}/events.jsonl。"""
        return self.video_jobs_dir / job_id / "events.jsonl"

    def _emit(self, job_id: str, event: dict[str, Any]) -> None:
        """统一事件发射：
        1. 追加一行到 events.jsonl（seq 单调递增，event 原样 + ts/seq 信封字段）；
        2. 继续走内存 EventBus（保留向后兼容，S1 不删 EventBus）。

        可从同步上下文（to_thread 内部）安全调用，open("a") 是同步 IO。
        """
        # ---- seq 计数：首次调用时从文件末行恢复（跨重启单调）----
        if job_id not in self._event_seq:
            ef = self._events_file(job_id)
            if ef.is_file():
                try:
                    # 读最后一行恢复 seq；残行（torn write）不计入 seq——
                    # 修复缺口2：原代码遇到坏行 last_seq += 1，把进程被杀时写半截的残行
                    # 当成一条有效事件计数，污染 seq。正确做法：坏行直接忽略，不更新 last_seq。
                    last_seq = 0
                    with ef.open("r", encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if line:
                                try:
                                    obj = json.loads(line)
                                    last_seq = int(obj.get("seq") or last_seq)
                                except (json.JSONDecodeError, ValueError):
                                    # 残行（坏 JSON）：忽略，不调整 last_seq
                                    pass
                    self._event_seq[job_id] = last_seq
                except OSError:
                    self._event_seq[job_id] = 0
            else:
                self._event_seq[job_id] = 0

        self._event_seq[job_id] += 1
        seq = self._event_seq[job_id]

        # ---- 落盘记录 = 原 event 原样 + ts/seq 信封字段 ----
        # 关键(契约)：events.jsonl 每行必须等于内存 EventBus 广播的 event 形态
        # (仅多挂 ts/seq + 补 node 字段)，因为 SSE 端把整行原样 yield 给前端。前端
        # useJobStream 读 parsed.node / parsed.state；若把 state 埋进 payload 子对象，
        # 前端 parsed.state 会变 undefined、node_status 增量更新失效(types.ts 也按
        # {type,job_id,node,state} 定义 wire 格式)。
        record: dict[str, Any] = {**event, "ts": time.time(), "seq": seq}
        record.setdefault("node", None)  # job_updated 等无 node 事件填 None，便于消费方按 node 过滤

        # ---- 追加写入 events.jsonl（O_APPEND 语义，单行原子）----
        # 修复缺口2：torn write 防护——若上次进程被 SIGKILL 时写半截（末字节非 \n），
        # 直接 append 会把新记录拼接到残行末尾，导致两行 JSON 合并成一行无法解析。
        # 修法：追加前用二进制模式读末字节；若非 \n，先补一个 \n 再写新记录行。
        ef = self._events_file(job_id)
        ef.parent.mkdir(parents=True, exist_ok=True)
        if ef.is_file() and ef.stat().st_size > 0:
            try:
                with ef.open("rb") as _fb:
                    _fb.seek(-1, 2)  # 从文件末尾倒退 1 字节
                    _last_byte = _fb.read(1)
                if _last_byte != b"\n":
                    # 末行残缺（torn write），先补换行符隔断残行
                    with ef.open("a", encoding="utf-8") as _fix:
                        _fix.write("\n")
            except OSError:
                pass
        with ef.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()

        # ---- 内存广播（向后兼容）----
        self.bus.publish(job_id, event)

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
            mock=data.get("mock", False),
            engine_iid=data.get("engine_iid"),
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
                # 运行状态：任一节点 running/queued 即视为"执行中"（按 pipeline 顺序取首个）
                running_node: str | None = None
                for name, n in (data.get("nodes") or {}).items():
                    if (n or {}).get("status") in ("running", "queued"):
                        running_node = name
                        break
                # 标题自动同步（一次性）：标题仍是创建时默认名、且不在运行中、且画布配置
                # 02_rw/episode.json 有 meta.title → 用内容标题覆盖并落盘一次。
                # 之后标题已非默认 → 不再同步，用户手动改名也被尊重。跳过运行中作品避免写竞争。
                title = data.get("title", "")
                if running_node is None and self._is_default_title(title):
                    ep_path = d / "02_rw" / "episode.json"
                    if ep_path.is_file():
                        try:
                            meta = (json.loads(ep_path.read_text(encoding="utf-8")).get("meta") or {})
                            ep_title = str(meta.get("title") or "").strip()
                        except (OSError, json.JSONDecodeError):
                            ep_title = ""
                        if ep_title and ep_title != title:
                            data["title"] = ep_title
                            title = ep_title
                            try:
                                sf.write_text(
                                    json.dumps(data, ensure_ascii=False, indent=2),
                                    encoding="utf-8",
                                )
                            except OSError as exc:
                                logger.warning("[pipeline] title sync write failed %s: %s", sf, exc)
                # 摘要：不带 nodes 全量详情，但带运行标记 + 各节点 status（供作品列表算
                # agent 级进度灯，前端按 agents.ts 映射成"当前 agent + 红黄绿"）
                node_status = {
                    name: (n or {}).get("status", "idle")
                    for name, n in (data.get("nodes") or {}).items()
                }
                out.append({
                    "job_id": data["job_id"],
                    "pipeline_id": data["pipeline_id"],
                    "title": title,
                    "created_at": data["created_at"],
                    "updated_at": data["updated_at"],
                    "running": running_node is not None,
                    "running_node": running_node,
                    "node_status": node_status,
                })
            except Exception as exc:
                logger.warning("[pipeline] read %s failed: %s", sf, exc)
        return out

    @staticmethod
    def _default_title(job_id: str, ts: float) -> str:
        # OPUS + 14 位本地时间戳 + 4 位 job_id hash，全大写；命名唯一且便于按时间排序
        return "OPUS" + time.strftime("%Y%m%d%H%M%S", time.localtime(ts)) + job_id[:4].upper()

    @staticmethod
    def _is_default_title(title: str) -> bool:
        """标题是否仍是创建时的默认名：前端「作品 …」或后端 OPUS 时间戳，或空。
        只有默认名才会被 episode.meta.title 自动覆盖（一次性），手动改过的名字不动。"""
        t = (title or "").strip()
        return t == "" or t.startswith("作品 ") or t.startswith("OPUS")

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

    async def job_cover_path(self, job_id: str) -> Path | None:
        """作品封面图的本地路径，按优先级取「第一帧」：
        1) 渲染成片 06_render/output.mp4 的首帧（ffmpeg 抽到 06_render/cover.jpg，缓存）
        2) 第一个非章节场景的容器图 03_image/<首场景>.webp
        3) 都没有 → None（前端回退到数字 marker）
        """
        job_dir = self.video_jobs_dir / job_id
        render_dir = job_dir / "06_render"
        cover = render_dir / "cover.jpg"
        mp4 = render_dir / "output.mp4"

        if mp4.is_file():
            # 成片在、且封面比成片新 → 用缓存；否则（重渲染过 / 没抽过）重新抽首帧
            if cover.is_file() and cover.stat().st_mtime >= mp4.stat().st_mtime:
                return cover
            try:
                await asyncio.to_thread(media_helpers._extract_first_frame, mp4, cover)
                if cover.is_file():
                    return cover
            except Exception as exc:
                logger.warning("[pipeline] cover extract failed for %s: %s", job_id, exc)
        elif cover.is_file():
            return cover

        # 回退：第一个非章节场景的容器图
        ep = self.get_episode(job_id)
        if ep:
            for b in ep.get("beats") or []:
                sid = b.get("scene")
                if sid and not str(sid).startswith("ch"):
                    pic = job_dir / "03_image" / f"{sid}.webp"
                    if pic.is_file():
                        return pic
                    break
        return None

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
        self._emit(state.job_id, {"type": "job_updated", "job_id": state.job_id})

    def update_title(self, job_id: str, title: str) -> None:
        state = self._load(job_id)
        state.title = title or self._default_title(job_id, state.created_at)
        state.updated_at = time.time()
        self._save(state)
        self._emit(state.job_id, {"type": "job_updated", "job_id": state.job_id})

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
        self._emit(job_id, {"type": "job_updated", "job_id": job_id})

    # ---------- 重跑 / 调度 ----------

    def _reset_node(self, n: NodeState) -> None:
        n.status = "idle"
        n.started_at = None
        n.finished_at = None
        n.progress = ""
        n.outputs = {}
        n.error = None
        n.task_id = None

    async def run_node(
        self,
        job_id: str,
        node_name: str,
        params: dict[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        """触发某节点执行。会把节点及其下游全部 reset 后再排队。
        params: 可选节点配置（如 rw 的 {profile}），merge 进 node_configs 持久化，
        执行时由 _execute_* 读取。不随 reset 清空。
        force: True = 用户显式「重新执行」，无条件 reset 重跑；False = 普通触发，
        对 asr 走幂等短路（见下）。
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
            self._save(state)

        # asr 幂等短路：article 已存在（done + 有产物）且非显式重跑 → 秒回，不重算、不动下游。
        # 输入变了 update_inputs 已把 asr reset 成 idle，故 done 即「输入未变」，复用安全。
        if (
            not force
            and node_name == "asr"
            and state.nodes[node_name].status == "done"
            and state.nodes[node_name].outputs
        ):
            self._emit(job_id, {
                "type": "node_status", "job_id": job_id, "node": node_name,
                "state": asdict(state.nodes[node_name]),
            })
            return

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
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(state.nodes[node_name])})

        # spawn 异步执行
        key = (job_id, node_name)
        if key in self._running_nodes and not self._running_nodes[key].done():
            return  # 已经在跑
        self._running_nodes[key] = asyncio.create_task(self._execute(job_id, node_name))

    async def _execute(self, job_id: str, node_name: str) -> None:
        try:
            if self._load(job_id).mock:
                await self._execute_mock(job_id, node_name)
            else:
                await self._execute_real_with_flag_watcher(job_id, node_name)
        except asyncio.CancelledError:
            # 用户主动 cancel（内存 task.cancel 或 watcher 检测到 flag 后 cancel inner task）：
            # 把节点回退到 idle 让 UI 可以再点"确认"。
            try:
                state = self._load(job_id)
                n = state.nodes[node_name]
                # _reset_node 先清各字段（outputs/task_id/started_at…），再覆写 error/finished_at
                self._reset_node(n)
                n.error = "cancelled"
                n.finished_at = time.time()
                self._save(state)
                self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})
            finally:
                # 清文件标记：取消完成后清掉，否则下次重跑该节点会立刻又被 watcher 取消。
                _cancel.clear_flag(self._cancel_flag(job_id, node_name))
                raise
        except _cancel.TaskCancelled:
            # Option A 协作式取消：工作线程自己 checkpoint 抛 TaskCancelled（不是 asyncio cancel）。
            # 不重抛（_execute 是 fire-and-forget task，无人 await；协作式取消是正常终态）。
            # flag 由工作线程（_run_in_thread_cancellable 的 finally uninstall 之前）或本分支清。
            try:
                state = self._load(job_id)
                n = state.nodes[node_name]
                # _reset_node 先清各字段，再覆写 error/finished_at
                self._reset_node(n)
                n.error = "cancelled"
                n.finished_at = time.time()
                self._save(state)
                self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})
            finally:
                # 工作线程协作式自停后，flag 由本分支清——flag 存活到此刻是协作式停止的前提。
                _cancel.clear_flag(self._cancel_flag(job_id, node_name))
        except Exception as exc:
            logger.exception("[pipeline] node %s/%s failed", job_id, node_name)
            state = self._load(job_id)
            n = state.nodes[node_name]
            n.status = "failed"
            n.error = f"{type(exc).__name__}: {exc}"
            n.finished_at = time.time()
            self._save(state)
            self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})
        finally:
            self._running_nodes.pop((job_id, node_name), None)

    async def _execute_real_with_flag_watcher(self, job_id: str, node_name: str) -> None:
        """在 _execute_real 外套一个轻量 watcher task，轮询文件标记。

        实现思路：
        - 把 _execute_real 包成 inner asyncio.Task
        - 并行启动 watcher coroutine，每 _CANCEL_WATCHER_INTERVAL_SEC 检查一次 flag
        - 用 asyncio.wait(FIRST_COMPLETED) 等待：inner 结束(正常/异常) 或 watcher 先返回
        - watcher 发现 flag → inner.cancel()，等 inner 结束后重抛 CancelledError
        - 无论如何 watcher task 都会被 cancel 掉（不泄漏）

        为什么不直接 inner.cancel() 再 await：
          asyncio.wait 是最简洁的"竞速"原语，避免在 flag 检测和 inner 异常间的竞态。
        """
        flag_path = self._cancel_flag(job_id, node_name)

        async def _watcher() -> None:
            """持续轮询 flag，发现则直接返回（由外层逻辑 cancel inner）。"""
            while True:
                await asyncio.sleep(self._CANCEL_WATCHER_INTERVAL_SEC)
                if _cancel.is_flagged(flag_path):
                    return  # 告知外层"flag 命中"

        inner: asyncio.Task[None] = asyncio.create_task(self._execute_real(job_id, node_name))
        watcher: asyncio.Task[None] = asyncio.create_task(_watcher())
        try:
            done, pending = await asyncio.wait(
                {inner, watcher},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            # 外层 task（_execute）被直接 cancel（内存路径）：同时 cancel inner 和 watcher
            inner.cancel()
            watcher.cancel()
            # 等待两者都结束，避免泄漏
            await asyncio.gather(inner, watcher, return_exceptions=True)
            raise

        # 注意：绝不在此盲 cancel pending——flag 命中时 pending={inner}，盲 cancel 会抛弃工作线程，
        # 抢在下面宽限期之前把 inner 干掉，使 Option A 协作式停止失效（子进程变孤儿）。
        # 改为按"谁先完成"分别处理。
        if watcher in done and not watcher.cancelled():
            # Option A：flag 命中后先给工作线程宽限期协作式自停（flag 仍存活 → 线程 checkpoint 抛
            # TaskCancelled / 子进程 Popen 轮询 killpg）。asyncio.wait 带 timeout 不会 cancel inner。
            if not inner.done():
                done2, _ = await asyncio.wait({inner}, timeout=self._CANCEL_GRACE_SEC)
                if inner not in done2:
                    # 非协作节点超时未停：强制 cancel（兜底，可能留孤儿线程）
                    inner.cancel()
                    await asyncio.gather(inner, return_exceptions=True)
                    raise asyncio.CancelledError("cancel flag detected (forced after grace)")
            # inner 已协作式结束：透传其异常（通常 TaskCancelled）给 _execute 的 except TaskCancelled
            if inner.cancelled():
                raise asyncio.CancelledError("cancel flag detected")
            exc = inner.exception()
            if exc is not None:
                raise exc
            return  # inner 在 flag 命中前正好正常完成

        # inner 先完成：cancel watcher 防泄漏，再透传 inner 结果（正常无返回值，异常则重抛）
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        exc = inner.exception()
        if exc is not None:
            raise exc

    async def _execute_mock(self, job_id: str, node_name: str) -> None:
        """Mock 执行：状态机与 _execute_real 完全一致（idle→running→done + SSE），
        但 running 态内只 sleep（模拟耗时）再从 015 素材写该节点产物 —— 不打任何
        真实下游（gpt-image / TTS / LLM）。由 state.mock=True 的作品（mock015）走这条；
        前端无感知，照常按接口状态流转。
        """
        from ncds_opus_factory.server import mock as mock_mod  # 延迟 import 破循环依赖

        state = self._load(job_id)
        n = state.nodes[node_name]
        n.status = "running"
        n.started_at = time.time()
        n.progress = "mock 执行中..."
        n.error = None
        n.outputs = {}
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

        await asyncio.sleep(mock_mod.MOCK_NODE_DELAY_SEC)
        job_dir = self.video_jobs_dir / job_id
        outputs = await asyncio.to_thread(mock_mod.run_mock_node, job_dir, node_name)

        state = self._load(job_id)
        n = state.nodes[node_name]
        n.status = "done"
        n.finished_at = time.time()
        n.progress = "完成"
        n.outputs = outputs
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

    async def _mock_regen_delay(self) -> None:
        """mock 下 regen 类操作的统一模拟耗时。"""
        from ncds_opus_factory.server import mock as mock_mod
        await asyncio.sleep(mock_mod.MOCK_NODE_DELAY_SEC)

    def _assert_known_model(
        self,
        model_id: str,
        *,
        missing_message: str | None = None,
    ) -> dict[str, str]:
        """返回 MODEL_CANDIDATES 中的候选；缺失时按既有语义抛 KeyError。"""
        cand = next((c for c in rw_helpers.MODEL_CANDIDATES if c["id"] == model_id), None)
        if cand is None:
            raise KeyError(missing_message or f"unknown model: {model_id}")
        return cand

    async def _rw_mock_short_circuit(self, state: JobState, job_id: str, model_id: str) -> bool:
        """mock 作品的 RW 单模型操作短路。

        rewrite/refine 在 mock015 下都不真调 LLM，只模拟耗时并重发 rw 节点状态。
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
        """重写 rw 某个模型的 draft（保留其他模型不动）。

        触发点：用户在 RW 抽屉切到某模型 tab 后点「重新生成」。流程同 _execute_rw
        但只调单个模型，覆盖目标 model_id 的子目录。
        """
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
        system_prompt, user_prompt = rw_helpers._build_rw_prompt(source_text, domain_guidance=domain_guidance)

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "rw", f"[rerun {model_id}] {text}")

        on_progress("单模型重跑启动")
        try:
            raw_text = await rw_helpers._invoke_rw_candidate(cand, user_prompt, system_prompt, on_progress)
        except rw_helpers._ModelUnavailable as exc:
            on_progress(f"模型 {model_id} 不可用，跳过: {exc}")
            return

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

        # 重写后重跑质检闸门，回写 entry 的 qc/qc_rubric -> 前端雷达图/质量分随新稿刷新
        # （否则会停在上一版旧数据；与 refine_rw_model 保持一致）
        try:
            qc = await asyncio.to_thread(rw_helpers._apply_rw_qc, model_dir, model_id, on_progress)
            entry.update(qc)
        except Exception as exc:  # noqa: BLE001 — 质检失败不拖垮重写稿
            on_progress(f"质检异常（不影响稿件）: {exc}")

        # 如果用户当前选中的就是这个模型，把 02_rw/draft.md 也同步更新
        # 并 invalidate 下游（lines 已 done 的话需要重跑：LINES 会重新调 LLM）
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
        """按 rubric 质检建议优化 rw 某模型的当前 draft（不重新生成、基于现有稿 + issues）。

        触发点：用户在 RW 抽屉某模型 tab 点「按建议优化」。与 rewrite_rw_model 不同：
        不重跑模型，而是把现有 draft.md + qc_rubric.issues 交给 opus 做最小改动优化，
        优化后**重跑质检并回写 drafts entry 的 qc/qc_rubric**（前端雷达图随之刷新）。
        """
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
        issues = list((entry.get("qc_rubric") or {}).get("issues") or [])
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
            raise RuntimeError("优化未返回有效稿（已保留原稿）")
        draft_path.write_text(refined.strip() + "\n", encoding="utf-8")
        on_progress("优化稿写盘完成，重跑质检")

        # 重跑质检闸门（ai_taste 终判 + rubric 评分），回写 entry 的 qc/qc_rubric -> 雷达图刷新
        try:
            qc = await asyncio.to_thread(rw_helpers._apply_rw_qc, model_dir, model_id, on_progress)
            entry.update(qc)
        except Exception as exc:  # noqa: BLE001 — 质检失败不拖垮优化稿
            on_progress(f"质检异常（不影响稿件）: {exc}")

        # 选中的就是这个模型时，同步定稿入口并 invalidate 下游
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
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "rw", "state": asdict(n)})

    async def regen_scene_image_from_preview(self, job_id: str, scene_id: str) -> str:
        """preview 抽屉里点「生成图片」时调用。不要求 image 节点 done，
        独立于 pipeline 流水线，直接出图并写到 03_image/{scene_id}.webp。

        文件名约定：纯 {scene_id}.webp（不带序号前缀）—— 015 模板的 player.js
        用 picSrcFor(sceneId) = ASSET_ROOT + '/pictures/' + sceneId + '.webp'
        来拼图片 URL；preview.py 路由把 .015-draft-assets/pictures/{sceneId}.webp
        映射到 03_image/{sceneId}.webp。前缀 NN- 会让模板取不到 job 产出。

        实现：复用 _generate_scene_image（gpt-image-2 → Pillow → WebP）。
        若 image 节点已有 outputs.items 且包含该 scene_id，顺手更新 image_relpath。
        """
        if self._load(job_id).mock:
            return await self._mock_regen_image(job_id, scene_id)
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
            media_helpers._generate_scene_image,
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
            self._emit(
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

    async def regen_image_sketch(self, job_id: str, scene_id: str, n: int) -> str:
        """重生 image 节点里指定 scene 的第 n 幅简笔画（白底黑剪影，1-based）。
        force 覆盖 03_image/{sid}-sk{n}.webp，更新 outputs.items[].sketches[].image_relpath。
        """
        state = self._load(job_id)
        if state.mock:
            return await self._mock_regen_sketch(job_id, scene_id, n)
        img = state.nodes.get("image")
        if img is None:
            raise KeyError("image node not found")
        if img.status != "done":
            raise ValueError("image node not done; run image first")

        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found")
        sc = (ep.get("scenes") or {}).get(scene_id)
        if not isinstance(sc, dict):
            raise ValueError(f"unknown scene: {scene_id}")
        sketches = sc.get("sketches") or []
        if n < 1 or n > len(sketches):
            raise ValueError(f"sketch index out of range: {n}")
        sp = str((sketches[n - 1] or {}).get("prompt") or "").strip()
        if not sp:
            raise ValueError(f"sketch {scene_id}-sk{n} has empty prompt")

        image_cfg = ep.get("image") or {}
        quality = image_cfg.get("quality") or "auto"
        no_text_hint = image_cfg.get("noTextHint") or ""
        sketch_size = image_cfg.get("sketchSize") or "1024x1024"
        sketch_prefix = str(image_cfg.get("sketchStylePrefix") or "").strip()
        full = " ".join(p for p in (sketch_prefix, sp, no_text_hint) if p)

        rel = f"03_image/{scene_id}-sk{n}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            target.unlink()

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "image", f"[regen {scene_id}-sk{n}] {text}")

        on_progress("简笔画重生中…")
        await asyncio.to_thread(
            media_helpers._generate_scene_image,
            scene_id=f"{scene_id}-sk{n}", prompt=full, size=sketch_size,
            quality=quality, target=target, job_id=job_id,
        )

        # 同步 image 节点 outputs.items[].sketches
        state = self._load(job_id)
        img = state.nodes.get("image")
        if img and img.outputs:
            items = list(img.outputs.get("items") or [])
            for it in items:
                if it.get("scene_id") != scene_id:
                    continue
                sk_items = list(it.get("sketches") or [])
                hit = next((s for s in sk_items if s.get("index") == n), None)
                if hit is not None:
                    hit["image_relpath"] = rel
                    hit.pop("error", None)
                else:
                    sk_items.append({"index": n, "prompt": sp, "image_relpath": rel})
                it["sketches"] = sk_items
                break
            img.outputs["items"] = items
            img.finished_at = time.time()
            state.updated_at = time.time()
            self._save(state)
            self._emit(
                job_id,
                {"type": "node_status", "job_id": job_id, "node": "image", "state": asdict(img)},
            )
        return rel

    async def regen_tts_scene(self, job_id: str, scene_id: str) -> None:
        """015：重生指定 scene 的整段音频（spawn tts_gen.py --only sid --force）。
        UI 按 scene 渲染，重生粒度也是 scene，与「整段合成」语义一致。
        """
        state = self._load(job_id)
        if state.mock:
            await self._mock_regen_tts(job_id, scene_id)
            return
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

        tts_gen = _template_dir("paper_card_talk_015") / ".015-draft-assets" / "tts_gen.py"
        audio_dir = job_dir / "04_tts"

        def on_progress(text: str) -> None:
            self._push_progress(job_id, "tts", f"[regen {scene_id}] {text}")

        await asyncio.to_thread(
            media_helpers._run_tts_gen_015,
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
        n.outputs["items"] = media_helpers._rebuild_tts_items_015(ep2)
        n.finished_at = time.time()
        state.updated_at = time.time()
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "tts", "state": asdict(n)})

    # ------------------------------------------------------------
    # mock 下 regen 短路实现：复用 015 素材，不打真实 gpt-image / TTS
    # ------------------------------------------------------------
    async def _mock_regen_image(self, job_id: str, scene_id: str) -> str:
        """mock：从 015 素材拷该 scene 容器图到 03_image/{scene_id}.webp（复用不重生）。"""
        from ncds_opus_factory.server import mock as mock_mod
        await asyncio.sleep(mock_mod.MOCK_NODE_DELAY_SEC)
        rel = f"03_image/{scene_id}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        src_pic = mock_mod._source_dir() / "pictures" / f"{scene_id}.webp"
        if src_pic.is_file():
            await asyncio.to_thread(shutil.copyfile, src_pic, target)
        state = self._load(job_id)
        img = state.nodes.get("image")
        if img and img.outputs and target.is_file():
            items = list(img.outputs.get("items") or [])
            for it in items:
                if it.get("scene_id") == scene_id:
                    it["image_relpath"] = rel
                    break
            img.outputs["items"] = items
            img.finished_at = time.time()
            self._save(state)
            self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "image", "state": asdict(img)})
        return rel

    async def _mock_regen_sketch(self, job_id: str, scene_id: str, n: int) -> str:
        """mock：源素材一般无简笔画文件，有则拷、没有用容器图占位到 {sid}-sk{n}.webp。"""
        from ncds_opus_factory.server import mock as mock_mod
        await asyncio.sleep(mock_mod.MOCK_NODE_DELAY_SEC)
        rel = f"03_image/{scene_id}-sk{n}.webp"
        target = self.video_jobs_dir / job_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        pics = mock_mod._source_dir() / "pictures"
        cand = pics / f"{scene_id}-sk{n}.webp"
        if not cand.is_file():
            cand = pics / f"{scene_id}.webp"
        if cand.is_file():
            await asyncio.to_thread(shutil.copyfile, cand, target)
        state = self._load(job_id)
        img = state.nodes.get("image")
        if img and img.outputs and target.is_file():
            items = list(img.outputs.get("items") or [])
            for it in items:
                if it.get("scene_id") != scene_id:
                    continue
                sk_items = list(it.get("sketches") or [])
                hit = next((s for s in sk_items if s.get("index") == n), None)
                if hit is not None:
                    hit["image_relpath"] = rel
                    hit.pop("error", None)
                else:
                    sk_items.append({"index": n, "prompt": "", "image_relpath": rel})
                it["sketches"] = sk_items
                break
            img.outputs["items"] = items
            img.finished_at = time.time()
            self._save(state)
            self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "image", "state": asdict(img)})
        return rel

    async def _mock_regen_tts(self, job_id: str, scene_id: str) -> None:
        """mock：scene 音频已由 tts mock 落盘，静态复用；sleep 后重建 items 收尾。"""
        await self._mock_regen_delay()
        state = self._load(job_id)
        n = state.nodes.get("tts")
        if n is None:
            return
        ep_path = self.video_jobs_dir / job_id / "02_rw" / "episode.json"
        if ep_path.is_file():
            ep = json.loads(ep_path.read_text(encoding="utf-8"))
            n.outputs["items"] = media_helpers._rebuild_tts_items_015(ep)
        n.finished_at = time.time()
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "tts", "state": asdict(n)})

    async def cancel_node(self, job_id: str, node_name: str) -> bool:
        """取消节点。幂等：内存里没有活着的 task 也视为取消成功。

        S2 变更：先写文件标记（跨进程取消信号），再走内存 task.cancel（同进程快路径）。
        文件标记让跨进程 worker（S3）的执行侧 watcher 也能读到并中止节点；
        内存 cancel 保留用于同进程下的即时响应。

        幽灵任务兜底（server 热重载/重启后 _running_nodes 清空）：内存无活 task 时，
        若磁盘仍 running/queued 就直接落盘 idle（与 CancelledError 分支一致），并一律返回 True。
        """
        # 先写文件标记（跨进程信号；同进程路径的 watcher 也会读到）。
        # Option A：flag 存活到工作线程协作式自停（_execute 的 TaskCancelled 分支清 flag），
        # 不在此处提前 clear，否则工作线程下个轮询点读到 flag=False 变孤儿。
        _cancel.set_flag(self._cancel_flag(job_id, node_name))

        # AC#5：连带 cancel 正在后台跑的 enrich（音轨/抠图）task。
        # enrich 工作线程靠存活的 flag 协作式终止 Demucs 子进程。
        enrich = self._enrich_tasks.get(job_id)
        enrich_cancelled = False
        if enrich is not None and not enrich.done():
            enrich.cancel()  # asyncio 层停 enrich loop；工作线程靠存活的 flag 协作式终止 Demucs
            enrich_cancelled = True

        key = (job_id, node_name)
        task = self._running_nodes.get(key)
        if task is not None and not task.done():
            # Option A 真实节点：不直接 cancel asyncio.Task。
            # watcher 已在跑，检测到 flag 后给宽限期等工作线程协作式自停，超时才 fallback cancel。
            # mock 节点（state.mock=True）无协作式停止点（纯 sleep），asyncio cancel 是唯一手段。
            try:
                is_mock = self._load(job_id).mock
            except FileNotFoundError:
                is_mock = False
            if is_mock:
                # mock 无子进程/checkpoint，直接 cancel；flag 由 _execute CancelledError 分支清
                task.cancel()
            # 真实节点：不 cancel task，靠 watcher+协作式（flag 存活到线程自停）
            return True

        # 幽灵任务兜底：内存没句柄，直接重置磁盘上残留的 running/queued 状态，并清标记。
        try:
            state = self._load(job_id)
        except FileNotFoundError:
            _cancel.clear_flag(self._cancel_flag(job_id, node_name))
            return True
        n = state.nodes.get(node_name)
        if n is not None and n.status in ("running", "queued"):
            # _reset_node 先清各字段（含 error→None），再覆写 error/finished_at；
            # 否则先设 error 会被 _reset_node 清掉（与 _execute 的 cancelled 分支统一）。
            self._reset_node(n)
            n.error = "cancelled"
            n.finished_at = time.time()
            self._save(state)
            self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})
        # 幽灵路径清标记：但若刚才有活 enrich 被 cancel（说明 asr 已 done、有后台 enrich 在跑），
        # 不清 flag（让 enrich 工作线程靠 flag 终止 Demucs）；其余幽灵情形正常清。
        if not enrich_cancelled:
            _cancel.clear_flag(self._cancel_flag(job_id, node_name))
        return True

    async def _execute_real(self, job_id: str, node_name: str) -> None:
        """真实执行：按 node_name 分发到对应实现。

        默认 rw/lines/storyboard/tts/image/render 经 engine strangler 执行；
        asr 固定走 legacy 采集路径。NOF_ENGINE_NODES=legacy 可全量回旧实现。
        除 asr 外的 `_execute_*` 分支现在只是冷回退：engine performer 是 015 执行真源，
        legacy lines/storyboard 已知缺少引擎侧 JSON 重试与 domain_image_style 注入，不再作为
        新功能对齐目标；只有显式回退/排障时才应使用。
        """
        # 4h：重跑前清残留 flag（防幽灵边界 DOA）。节点(重)启动前清掉任何残留 flag，
        # 确保 fresh run 不被旧 flag 秒取消。submit_node 已防双跑，故这里只在真正新启动时执行。
        _cancel.clear_flag(self._cancel_flag(job_id, node_name))
        state = self._load(job_id)
        n = state.nodes[node_name]
        n.status = "running"
        n.started_at = time.time()
        n.progress = "启动..."
        n.error = None
        n.outputs = {}
        self._save(state)
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

        if self._engine is not None and node_name in self._engine_nodes and node_name != "asr":
            # 绞杀者：该节点改走生产引擎执行（JobState 仍是 facade 真相源）。
            # asr 例外：沈括采集要 mid-run 增量 outputs（collected 渐进推送）+ 节点 done 后台补
            # 音轨/抠图，引擎 slice-1 不支持步内增量，故 asr 固定走 legacy 采集 _execute_asr_collect。
            outputs = await self._execute_via_engine(job_id, node_name)
        elif node_name == "tts":
            outputs = await self._execute_tts(job_id)
        elif node_name == "image":
            outputs = await self._execute_image(job_id)
        elif node_name == "asr":
            outputs = await self._execute_asr_collect(job_id)
        elif node_name == "rw":
            outputs = await self._execute_rw(job_id)
        elif node_name == "lines":
            outputs = await self._execute_lines(job_id)
        elif node_name == "storyboard":
            outputs = await self._execute_storyboard(job_id)
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
        self._emit(job_id, {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)})

        # 沈括采集快采 done 后，后台补 Demucs 音轨分离 + 抠图（重活，不阻塞下游 rw）。
        if node_name == "asr":
            self._spawn_asr_enrich(job_id)

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
            self._emit(
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
            self._emit(
                job_id,
                {"type": "node_status", "job_id": job_id, "node": node_name, "state": asdict(n)},
            )
        except Exception as exc:
            logger.warning("[pipeline] push_outputs_patch failed: %s", exc)

    def _push_model_progress(self, job_id: str, node_name: str, model_progress: dict[str, Any]) -> None:
        self._push_outputs_patch(job_id, node_name, "model_progress", model_progress)

    # ------------------------------------------------------------
    # Legacy fallback for non-ASR nodes.
    #
    # 默认非 asr 节点经 _execute_via_engine -> pct015_* performer 执行；下面这些
    # `_execute_tts/_execute_image/_execute_rw/_execute_lines/_execute_storyboard/_execute_render`
    # 只在 NOF_ENGINE_NODES=legacy/off/none 或显式排除节点时作为回退护城河。
    # 已知漂移（task-3.1/A2）：legacy lines/storyboard 没有引擎侧 JSON parse retry；
    # storyboard 也不会注入 domain_image_style。不要在这里继续扩展主链行为。
    # ------------------------------------------------------------

    # ------------------------------------------------------------
    # tts 节点
    # ------------------------------------------------------------
    async def _execute_tts(self, job_id: str) -> dict[str, Any]:
        """按 02_rw/episode.json 按 scene 整段合成配音（scene-<sid>.mp3 + 字级时间戳
        写回 beats），韵律更连贯。spawn 015 模板自带的 tts_gen.py。
        """
        job_dir = self.video_jobs_dir / job_id
        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found; run rw first (or manually seed it)")

        tts_gen = _template_dir("paper_card_talk_015") / ".015-draft-assets" / "tts_gen.py"
        return await PipelineTtsRun(
            runner=self,
            job_id=job_id,
            job_dir=job_dir,
            episode=ep,
            tts_gen_script=tts_gen,
            run_tts_gen=media_helpers._run_tts_gen_015,
            rebuild_tts_items=media_helpers._rebuild_tts_items_015,
        ).run()

    # ------------------------------------------------------------
    # 真接入：image 节点
    # ------------------------------------------------------------
    async def _execute_image(self, job_id: str) -> dict[str, Any]:
        """按 02_rw/episode.json scenes[].prompt 批量调 gpt-image-2 出图 → WebP。

        复刻模板自带 pic_gen.py 的 orchestration：
        - beats 出场顺序去重 → scene_id 列表
        - 跳过 ch* 章节卡（CSS 渲染，不需要图）
        - 每个 scene 用 gpt_image/gpt_image_gen.py 出 PNG，Pillow 转 WebP 落 03_image/{sid}.webp
        - 幂等：已存在跳过
        """
        job_dir = self.video_jobs_dir / job_id
        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found; run rw first (or manually seed it)")
        return await PipelineImageRun(
            runner=self,
            job_id=job_id,
            job_dir=job_dir,
            episode=ep,
            generate_scene_image=media_helpers._generate_scene_image,
        ).run()

    async def _execute_asr_collect(self, job_id: str) -> dict[str, Any]:
        """沈括采集（统一走 collect_one 快采趟）：对 inputs.urls 每条作品解析 aweme_id →
        取展示元数据 → collect_one(do_audio/do_frames=False) 只跑下载+转写+清洗+评论，
        产出可让下游 rw 先走的 text + 抽屉先展示的文案/评论/播放数据/封面。重活（Demucs
        音轨分离 / 抠图）由本节点 done 后台 _enrich_asr_collected 补（见 _execute_real）。

        outputs.collected = [entry...]（对齐 app ShenkuoEntry：aweme_id/desc/digg/stats/
        top_comments/cover/hashtags/text/audio?/cutouts?/frames?/status + index/url/error）。
        collect_one 幂等 + works_repo 缓存：app/订阅采过的作品直接命中、秒出。
        """
        state = self._load(job_id)
        job_dir = self.video_jobs_dir / job_id
        return await PipelineAsrCollectRun(
            runner=self,
            job_id=job_id,
            job_dir=job_dir,
            inputs=state.inputs,
            flag_path=self._cancel_flag(job_id, "asr"),
            run_in_thread_cancellable=self._run_in_thread_cancellable,
        ).run()

    # ------------------------------------------------------------
    # 真接入：rw 节点
    # ------------------------------------------------------------
    async def _execute_rw(self, job_id: str) -> dict[str, Any]:
        """多模型并行改写：按 MODEL_CANDIDATES（当前 opus + deepseek）同时出稿，domain 写作方法驱动。

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
        asr_out = asr_node.outputs or {}
        asr_items = list(asr_out.get("collected") or asr_out.get("items") or [])
        if not asr_items:
            raise ValueError("asr outputs empty; nothing to rewrite")

        job_dir = self.video_jobs_dir / job_id
        rw_root = job_dir / "02_rw"
        rw_root.mkdir(parents=True, exist_ok=True)

        # 拼 sourceText：沈括采集的清洗稿 text（缺失回退 legacy article 文件）
        source_text = rw_helpers._rw_source_text(asr_items, job_dir)
        if not source_text:
            raise RuntimeError("asr 采集文案全部为空，无法 rw")

        domain_guidance = rw_helpers._rw_domain_guidance(state.inputs.get("domain"))
        system_prompt, user_prompt = rw_helpers._build_rw_prompt(source_text, domain_guidance=domain_guidance)

        return await PipelineRwRun(
            runner=self,
            job_id=job_id,
            rw_root=rw_root,
            source_text=source_text,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model_candidates=rw_helpers.MODEL_CANDIDATES,
            model_unavailable_cls=rw_helpers._ModelUnavailable,
            invoke_rw_candidate=rw_helpers._invoke_rw_candidate,
            apply_rw_qc=rw_helpers._apply_rw_qc,
        ).run()

    # ------------------------------------------------------------
    # lines 节点：校验型 no-op（实际"抽取台词"由前端 LinesPanel 直接编辑
    # 02_rw/episode.json 的 beats[] 完成；后端只确认数据齐了就 done）
    # ------------------------------------------------------------
    async def _execute_lines(self, job_id: str) -> dict[str, Any]:
        """读 02_rw/draft.md 定稿 → 调 opus 结构化成逐句字幕 beats[]。

        只产脚本层（meta + beats），scenes 留空 {} 交给下游 storyboard 节点的
        director agent 产出。合并模板骨架（保留 audio/visual/playback/fonts/image
        等渲染配置），写 02_rw/episode.json。
        """
        pipeline_id = self._load(job_id).pipeline_id
        return await PipelineLinesRun(
            runner=self,
            job_id=job_id,
            job_dir=self.video_jobs_dir / job_id,
            pipeline_id=pipeline_id,
            call_opus_for_rw=rw_helpers._call_opus_for_rw,
            model_id=DEFAULT_OPUS_MODEL_ID,
        ).run()

    # ------------------------------------------------------------
    # storyboard（分镜）节点：director agent 产出视觉层 scenes{}
    # ------------------------------------------------------------
    async def _execute_storyboard(self, job_id: str) -> dict[str, Any]:
        """读 lines 产出的 beats[] → 调 director agent（whisper-reel 导演人格）切子场景
        + 设计容器图 prompt 与简笔画 → 回填 beats[].scene + 写 scenes{} 到 episode.json。

        scenes 的简笔画风格圣经（sketchStylePrefix）取自 episode.image，缺省用
        storyboard_director.DEFAULT_SKETCH_STYLE_PREFIX 兜底。
        """
        ep = self.get_episode(job_id)
        if ep is None:
            raise ValueError("episode.json not found; run lines first")
        beats_raw = ep.get("beats") or []
        if not beats_raw:
            raise ValueError("episode.beats is empty; run lines first")
        return await PipelineStoryboardRun(
            runner=self,
            job_id=job_id,
            episode=ep,
            beats_raw=beats_raw,
            call_opus_for_rw=rw_helpers._call_opus_for_rw,
            model_id=DEFAULT_OPUS_MODEL_ID,
        ).run()

    # ------------------------------------------------------------
    # 真接入：render 节点
    # ------------------------------------------------------------
    async def _execute_render(self, job_id: str) -> dict[str, Any]:
        """出 1920x1080 MP4（commands/render_015，015 render.mjs，scene 整段合音）。
        依赖 02_rw/episode.json + 04_tts/*.mp3 + 03_image/*.webp。
        """
        job_dir = self.video_jobs_dir / job_id
        from ncds_opus_factory.commands import render_015 as render_cmd

        return await PipelineRenderRun(
            runner=self,
            job_id=job_id,
            job_dir=job_dir,
            render_run=render_cmd.run,
        ).run()
