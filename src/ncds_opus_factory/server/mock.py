"""Mock 数据：用 015 素材在 video-jobs/ 里种一个完整的 mock 作品，供 studio 开发预览。

设计
----
- 不拦截任何接口：直接在磁盘上种一个真实的 job 目录（默认 id=mock015），节点状态置为
  done、产物文件实拷自 015 素材。这样 studio 的所有抽屉（episode / 图片 / 音频 / RW 4
  模型 draft）都走正常的文件服务读到真实内容，零 mock 分支侵入业务路由。
- 前端 URL 带 mock=1（开关）时调 POST /mock/ensure 确保该作品存在并打开它。
- RW 产物：4 个模型的 draft 都用 015 的「正文」（beats 的 zh 拼成的口播稿）。

素材来源优先 ../ncds-materials/.015-draft-assets（用户素材），回退仓库内模板。
调 mock 行为只需改 MOCK_CONFIG。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from ncds_opus_factory.server.pipeline_runner import (
    JobState,
    NodeState,
    _rebuild_tts_items_015,
)

MOCK_JOB_ID = "mock015"
MOCK_PIPELINE_ID = "paper_card_talk_015"

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
# 015 素材源候选：优先用户的 ncds-materials，回退仓库内模板（保证 CI/无 sibling 时也能种）
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


def ensure_mock_job(runner: Any) -> str:
    """（重新）种一个 mock 作品，返回 job_id。幂等：每次调用重建以刷新素材。"""
    src = _source_dir()
    episode = json.loads((src / "episode.json").read_text(encoding="utf-8"))
    beats = episode.get("beats") or []
    scenes = episode.get("scenes") or {}

    job_dir = runner.video_jobs_dir / MOCK_JOB_ID
    shutil.rmtree(job_dir, ignore_errors=True)
    for sub in ("02_rw", "03_image", "04_tts", "01_asr/1"):
        (job_dir / sub).mkdir(parents=True, exist_ok=True)

    # 正文 = beats 的 zh 拼成的口播稿（RW 4 模型产物都用它）
    body = "\n".join(str(b.get("zh") or "") for b in beats).strip() or "（空正文）"

    episode_json = json.dumps(episode, ensure_ascii=False, indent=2)
    (job_dir / "02_rw" / "draft.md").write_text(body + "\n", encoding="utf-8")
    (job_dir / "02_rw" / "episode.json").write_text(episode_json, encoding="utf-8")
    (job_dir / "01_asr" / "1" / "article.md").write_text(body + "\n", encoding="utf-8")

    drafts: list[dict[str, Any]] = []
    for m in MOCK_CONFIG["rw_models"]:
        md = job_dir / "02_rw" / m["id"]
        md.mkdir(parents=True, exist_ok=True)
        (md / "draft.md").write_text(body + "\n", encoding="utf-8")
        (md / "episode.json").write_text(episode_json, encoding="utf-8")
        drafts.append({
            "model_id": m["id"], "label": m["label"], "status": "success", "reason": None,
            "draft_relpath": f"02_rw/{m['id']}/draft.md",
            "episode_relpath": f"02_rw/{m['id']}/episode.json",
        })

    # 拷贝样例图 / 音频
    pics = src / "pictures"
    if pics.is_dir():
        for p in pics.glob("*.webp"):
            shutil.copyfile(p, job_dir / "03_image" / p.name)
    aud = src / "audio"
    if aud.is_dir():
        for a in aud.glob("*.mp3"):
            shutil.copyfile(a, job_dir / "04_tts" / a.name)

    # 出场顺序去重 scene → image items
    seen: set[str] = set()
    scene_order: list[str] = []
    for b in beats:
        sid = b.get("scene")
        if sid and sid not in seen:
            seen.add(sid)
            scene_order.append(sid)
    image_items = []
    for sid in scene_order:
        sc = scenes.get(sid) or {}
        rel = f"03_image/{sid}.webp"
        has = (job_dir / rel).is_file()
        image_items.append({
            "scene_id": sid, "prompt": str(sc.get("prompt") or ""),
            "image_relpath": rel if has else None, "sketches": [],
        })

    tts_items = _rebuild_tts_items_015(episode)
    scene_files = {it["audio_relpath"] for it in tts_items if it.get("audio_relpath")}

    now = time.time()

    def done(outputs: dict[str, Any]) -> dict[str, Any]:
        return {"status": "done", "started_at": now, "finished_at": now,
                "progress": "完成", "outputs": outputs, "error": None, "task_id": None}

    nodes = {
        "input": NodeState(name="input", **done({
            "url": "mock://015", "urls": ["mock://015"],
            "shares": [{"url": "mock://015", "title": MOCK_CONFIG["title"], "author": "mock", "tags": []}],
        })),
        "asr": NodeState(name="asr", **done({
            "items": [{"index": 1, "url": "mock://015", "title": MOCK_CONFIG["title"],
                       "author": "mock", "transcript_relpath": "01_asr/1/article.md",
                       "article_relpath": "01_asr/1/article.md", "error": None}],
            "asr_dir": "01_asr",
        })),
        "rw": NodeState(name="rw", **done({"drafts": drafts, "selected_model_id": drafts[0]["model_id"]})),
        "lines": NodeState(name="lines", **done({
            "episode_relpath": "02_rw/episode.json", "beats_count": len(beats)})),
        "storyboard": NodeState(name="storyboard", **done({
            "episode_relpath": "02_rw/episode.json", "scenes_count": len(scenes),
            "sketches_count": 0, "groups_count": len(scene_order), "beats_count": len(beats)})),
        "tts": NodeState(name="tts", **done({
            "items": tts_items, "audio_dir": "04_tts", "mode": "segmented",
            "scene_count": len(scene_files), "audio_count": len(scene_files)})),
        "image": NodeState(name="image", **done({
            "items": image_items, "pictures_dir": "03_image",
            "pictures_count": sum(1 for it in image_items if it["image_relpath"]),
            "ok": 0, "skipped": 0, "failed": 0, "sketch_ok": 0, "sketch_failed": 0})),
        "preview": NodeState(name="preview", status="idle"),
        "render": NodeState(name="render", status="idle"),
        "download": NodeState(name="download", status="idle"),
    }

    state = JobState(
        job_id=MOCK_JOB_ID, pipeline_id=MOCK_PIPELINE_ID, title=MOCK_CONFIG["title"],
        created_at=now, updated_at=now,
        inputs={"url": "mock://015", "urls": ["mock://015"]},
        nodes=nodes,
    )
    runner._save(state)
    return MOCK_JOB_ID
