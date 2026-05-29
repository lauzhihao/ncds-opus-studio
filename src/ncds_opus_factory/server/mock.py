"""Mock 数据：用 015 素材把 mock015 作品改造成「可交互模拟器」。

设计（v2）
---------
- mock015 不再预置成品：种作品时只把 START(input) 置 done + 打 mock=True 标志，
  其余节点 idle —— 用户从 START 一路点下去。
- 每个节点的「执行」由 pipeline_runner._execute_mock 接管：running 态内 sleep
  MOCK_NODE_DELAY_SEC 秒，再调本模块 run_mock_node(job_dir, node) —— 实时从 015
  素材写该节点产物并返回 outputs。
- 前端零 mock：触发仍走真实 /run + SSE，状态正常 idle->queued->running->done 流转。
- regen 类操作（重生单图 / 单段音 / 单模型）也在 runner 里短路，复用素材，
  不打真实 gpt-image / TTS。

产物落点对齐 routes/preview.py 的硬性期望：
    01_asr/1/article.md | 02_rw/episode.json(+4 模型) | 04_tts/scene-*.mp3 | 03_image/*.webp

素材源优先 ../ncds-materials/.015-draft-assets（用户素材），回退仓库内模板（保证
CI / 无 sibling 时也能种）。调 mock 行为只需改 MOCK_CONFIG / MOCK_NODE_DELAY_SEC。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.pipelines import get_pipeline
from ncds_opus_factory.server.pipeline_runner import (
    JobState,
    NodeState,
    _rebuild_tts_items_015,
)

MOCK_JOB_ID = "mock015"
MOCK_PIPELINE_ID = "paper_card_talk_015"
# 每个节点 mock 执行的模拟耗时（秒）：running 态内 sleep 这么久再吐数据
MOCK_NODE_DELAY_SEC = 3.0

# 开关参数：mock 作品如何拼装（改这里即可调 mock 行为）
MOCK_CONFIG: dict[str, Any] = {
    "title": "MOCK · 015 素材",
    "rw_models": [
        {"id": "opus", "label": "Claude Opus 4.7"},
        {"id": "gpt5", "label": "GPT-5.5"},
        {"id": "gemini_local", "label": "GEMINI-3.5 FLASH"},
        {"id": "deepseek", "label": "DeepSeek V4 Pro"},
    ],
}

_REPO_ROOT = Path(__file__).resolve().parents[3]
# 015 素材源候选：优先用户的 ncds-materials，回退仓库内模板
_SOURCE_CANDIDATES = [
    _REPO_ROOT.parent / "ncds-materials" / ".015-draft-assets",
    _REPO_ROOT / "src" / "ncds_opus_factory" / "templates"
    / "paper_card_talk_015" / ".015-draft-assets",
]


def _source_dir() -> Path:
    for d in _SOURCE_CANDIDATES:
        if (d / "episode.json").is_file():
            return d
    raise FileNotFoundError(
        "015 mock 素材未找到：ncds-materials/.015-draft-assets 与仓库模板均缺 episode.json"
    )


def _load_episode() -> dict[str, Any]:
    return json.loads((_source_dir() / "episode.json").read_text(encoding="utf-8"))


def _body_from_beats(episode: dict[str, Any]) -> str:
    """正文 = beats 的 zh 拼成的口播稿（RW 4 模型产物都用它）。"""
    beats = episode.get("beats") or []
    return "\n".join(str(b.get("zh") or "") for b in beats).strip() or "（空正文）"


def _scene_order(episode: dict[str, Any]) -> list[str]:
    """出场顺序去重 scene id。"""
    seen: set[str] = set()
    order: list[str] = []
    for b in episode.get("beats") or []:
        sid = b.get("scene")
        if sid and sid not in seen:
            seen.add(sid)
            order.append(sid)
    return order


# ---------------------------------------------------------------------------
# 逐节点 mock builder：写该节点产物到 job_dir + 返回 outputs dict。
# outputs 形状须与 pipeline_runner._execute_* 真实产物一致，前端面板才认。
# ---------------------------------------------------------------------------

def _mock_asr(job_dir: Path, episode: dict[str, Any]) -> dict[str, Any]:
    body = _body_from_beats(episode)
    d = job_dir / "01_asr" / "1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "article.md").write_text(body + "\n", encoding="utf-8")
    return {
        "items": [{
            "index": 1, "url": "mock://015", "title": MOCK_CONFIG["title"],
            "author": "mock", "transcript_relpath": "01_asr/1/article.md",
            "article_relpath": "01_asr/1/article.md", "error": None,
        }],
        "asr_dir": "01_asr",
    }


def _mock_rw(job_dir: Path, episode: dict[str, Any]) -> dict[str, Any]:
    body = _body_from_beats(episode)
    episode_json = json.dumps(episode, ensure_ascii=False, indent=2)
    rw = job_dir / "02_rw"
    rw.mkdir(parents=True, exist_ok=True)
    # 02_rw/episode.json 是 preview.py 与下游的 canonical 来源：rw 阶段就落整份 015 episode
    (rw / "draft.md").write_text(body + "\n", encoding="utf-8")
    (rw / "episode.json").write_text(episode_json, encoding="utf-8")
    drafts: list[dict[str, Any]] = []
    for m in MOCK_CONFIG["rw_models"]:
        md = rw / m["id"]
        md.mkdir(parents=True, exist_ok=True)
        (md / "draft.md").write_text(body + "\n", encoding="utf-8")
        (md / "episode.json").write_text(episode_json, encoding="utf-8")
        drafts.append({
            "model_id": m["id"], "label": m["label"], "status": "success", "reason": None,
            "draft_relpath": f"02_rw/{m['id']}/draft.md",
            "episode_relpath": f"02_rw/{m['id']}/episode.json",
        })
    return {"drafts": drafts, "selected_model_id": drafts[0]["model_id"]}


def _mock_lines(job_dir: Path, episode: dict[str, Any]) -> dict[str, Any]:
    # episode.json 已由 rw 写入；防御性补一份，避免单独重跑 lines 时缺失
    ep_path = job_dir / "02_rw" / "episode.json"
    if not ep_path.is_file():
        ep_path.parent.mkdir(parents=True, exist_ok=True)
        ep_path.write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "episode_relpath": "02_rw/episode.json",
        "beats_count": len(episode.get("beats") or []),
    }


def _mock_storyboard(job_dir: Path, episode: dict[str, Any]) -> dict[str, Any]:
    scenes = episode.get("scenes") or {}
    return {
        "episode_relpath": "02_rw/episode.json",
        "scenes_count": len(scenes),
        "sketches_count": 0,
        "groups_count": len(_scene_order(episode)),
        "beats_count": len(episode.get("beats") or []),
    }


def _mock_tts(job_dir: Path, episode: dict[str, Any]) -> dict[str, Any]:
    src = _source_dir()
    tts_dir = job_dir / "04_tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    aud = src / "audio"
    if aud.is_dir():
        for a in aud.glob("*.mp3"):
            shutil.copyfile(a, tts_dir / a.name)
    items = _rebuild_tts_items_015(episode)
    scene_files = {it["audio_relpath"] for it in items if it.get("audio_relpath")}
    return {
        "items": items, "audio_dir": "04_tts", "mode": "segmented",
        "scene_count": len(scene_files), "audio_count": len(scene_files),
    }


def _mock_image(job_dir: Path, episode: dict[str, Any]) -> dict[str, Any]:
    src = _source_dir()
    img_dir = job_dir / "03_image"
    img_dir.mkdir(parents=True, exist_ok=True)
    pics = src / "pictures"
    if pics.is_dir():
        for p in pics.glob("*.webp"):
            shutil.copyfile(p, img_dir / p.name)
    scenes = episode.get("scenes") or {}
    items = []
    for sid in _scene_order(episode):
        sc = scenes.get(sid) or {}
        rel = f"03_image/{sid}.webp"
        has = (job_dir / rel).is_file()
        items.append({
            "scene_id": sid, "prompt": str(sc.get("prompt") or ""),
            "image_relpath": rel if has else None, "sketches": [],
        })
    return {
        "items": items, "pictures_dir": "03_image",
        "pictures_count": sum(1 for it in items if it["image_relpath"]),
        "ok": 0, "skipped": 0, "failed": 0, "sketch_ok": 0, "sketch_failed": 0,
    }


def _mock_preview(job_dir: Path, episode: dict[str, Any]) -> dict[str, Any]:
    # preview 真实流程没有后台任务（iframe 直接读 02_rw/episode.json + 已落盘素材）；
    # mock 里只需「通过审核」即 done，让下游 render 的 dep 满足。
    return {}


def _mock_render(job_dir: Path, episode: dict[str, Any]) -> dict[str, Any]:
    src = _source_dir()
    out_dir = job_dir / "06_render"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "output.mp4"
    # 源素材若带样例成片就拷一份（封面 / 下载即有真东西）；没有就只占位 done
    size: int | None = None
    out_src = src / "output"
    if out_src.is_dir():
        mp4s = sorted(out_src.glob("*.mp4"))
        if mp4s:
            shutil.copyfile(mp4s[0], out_path)
            size = out_path.stat().st_size
    return {
        "video_relpath": "06_render/output.mp4",
        "output_path": str(out_path),
        "video_size_bytes": size,
        "workdir": None,
    }


_NODE_BUILDERS: dict[str, Callable[[Path, dict[str, Any]], dict[str, Any]]] = {
    "asr": _mock_asr,
    "rw": _mock_rw,
    "lines": _mock_lines,
    "storyboard": _mock_storyboard,
    "tts": _mock_tts,
    "image": _mock_image,
    "preview": _mock_preview,
    "render": _mock_render,
}


def run_mock_node(job_dir: Path, node_name: str) -> dict[str, Any]:
    """执行某节点的 mock：写产物 + 返回 outputs dict。由 runner._execute_mock 调。

    input / download 等 UI-only 节点不该走到这里；未知节点返回空 outputs（不炸）。
    """
    builder = _NODE_BUILDERS.get(node_name)
    if builder is None:
        return {}
    return builder(job_dir, _load_episode())


def ensure_mock_job(runner: Any) -> str:
    """（重新）种一个 mock 作品，返回 job_id。

    幂等：每次调用重置成「只有 START done、其余 idle、mock=True」的初始态，并清掉历史
    产物，让用户每次都能从 START 一路点到底。逐节点产物在各节点被 run 时才生成。
    """
    _source_dir()  # 提前校验素材在；缺失直接 FileNotFoundError -> 路由 404
    job_dir = runner.video_jobs_dir / MOCK_JOB_ID
    shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    shares = [{"url": "mock://015", "title": MOCK_CONFIG["title"], "author": "mock", "tags": []}]
    inputs = {"url": "mock://015", "urls": ["mock://015"], "raw_text": "", "shares": shares}

    nodes: dict[str, NodeState] = {}
    for nd in get_pipeline(MOCK_PIPELINE_ID).nodes:
        if nd.kind == "input":
            nodes[nd.name] = NodeState(
                name=nd.name, status="done", started_at=now, finished_at=now,
                progress="完成",
                outputs={"url": "mock://015", "urls": ["mock://015"], "shares": shares, "raw_text": ""},
                error=None, task_id=None,
            )
        else:
            nodes[nd.name] = NodeState(name=nd.name, status="idle")

    state = JobState(
        job_id=MOCK_JOB_ID, pipeline_id=MOCK_PIPELINE_ID, title=MOCK_CONFIG["title"],
        created_at=now, updated_at=now, inputs=inputs, nodes=nodes, mock=True,
    )
    runner._save(state)
    return MOCK_JOB_ID
