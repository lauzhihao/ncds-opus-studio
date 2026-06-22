"""Job state persistence and public state APIs for PipelineRunner."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ncds_opus_core.pipelines import PIPELINE_REGISTRY, get_pipeline
from ncds_opus_factory.server import pipeline_media_helpers as media_helpers
from ncds_opus_factory.server.pipeline_models import JobState, NodeState

logger = logging.getLogger(__name__)


class PipelineStateStoreMixin:
    """Disk-backed JobState store and small public mutation helpers."""

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
        pipeline_id = data["pipeline_id"]
        if pipeline_id in PIPELINE_REGISTRY:
            pipeline = PIPELINE_REGISTRY[pipeline_id]
            for n in pipeline.nodes:
                if n.name in nodes:
                    continue
                if n.kind == "input":
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
                running_node: str | None = None
                for name, n in (data.get("nodes") or {}).items():
                    if (n or {}).get("status") in ("running", "queued"):
                        running_node = name
                        break
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
        return "OPUS" + time.strftime("%Y%m%d%H%M%S", time.localtime(ts)) + job_id[:4].upper()

    @staticmethod
    def _is_default_title(title: str) -> bool:
        t = (title or "").strip()
        return t == "" or t.startswith("作品 ") or t.startswith("OPUS")

    def create_job(self, pipeline_id: str, title: str, inputs: dict[str, Any]) -> JobState:
        pipeline = get_pipeline(pipeline_id)
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
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
        ep = self.video_jobs_dir / job_id / "02_rw" / "episode.json"
        if not ep.exists():
            return None
        return json.loads(ep.read_text(encoding="utf-8"))

    async def job_cover_path(self, job_id: str) -> Path | None:
        job_dir = self.video_jobs_dir / job_id
        render_dir = job_dir / "06_render"
        cover = render_dir / "cover.jpg"
        mp4 = render_dir / "output.mp4"

        if mp4.is_file():
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

    def update_inputs(self, job_id: str, inputs: dict[str, Any]) -> None:
        state = self._load(job_id)
        state.inputs.update(inputs)
        for n in state.nodes.values():
            if n.name == "input":
                n.outputs.update(inputs)
                n.status = "done"
                n.finished_at = time.time()
                break
        for n in state.nodes.values():
            if n.name != "input" and n.status != "idle":
                self._reset_node(n)
        self._save(state)
        self._emit(job_id, {"type": "job_updated", "job_id": job_id})
