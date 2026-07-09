"""Mock 数据：用 final_preview 素材把演示作品改造成「可交互模拟器」。

设计（v2）
---------
- 演示作品不再预置成品：种作品时只把 START(input) 置 done + 打 mock=True 标志，
  其余节点 idle —— 用户从 START 一路点下去。
- 每个节点的「执行」由 pipeline_runner._execute_mock 接管：running 态内 sleep
  按节点类型随机 sleep，再调本模块 run_mock_node(job_dir, node) —— 实时从 final_preview
  素材写该节点产物并返回 outputs。
- 前端零 mock：触发仍走真实 /run + SSE，状态正常 idle->queued->running->done 流转。
- regen 类操作（重生单图 / 单段音 / 单模型）也在 runner 里短路，复用素材，
  不打真实 gpt-image / TTS。

产物落点对齐 routes/preview.py 的硬性期望：
    01_asr/1/article.md | 02_rw/episode.json(+4 模型) | 04_tts/scene-*.mp3 | 03_image/*.webp

素材源优先 ../ncds-materials/.final-preview-assets（用户素材），回退仓库内模板（保证
CI / 无 sibling 时也能种）。调 mock 行为只需改 MOCK_CONFIG / MOCK_DELAY_RANGES。
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from ncds_opus_core.pipelines import get_pipeline
from ncds_opus_core.templates import template_dir as _core_template_dir
from ncds_opus_factory.server.pipeline_media_helpers import _rebuild_tts_items_015
from ncds_opus_factory.server.pipeline_runner import (
    JobState,
    NodeState,
)

MOCK_JOB_ID = "36aacfec847e"
MOCK_SOURCE_JOB_ID = "36aacfec847d"
MOCK_PIPELINE_ID = "final_preview"
FALLBACK_SOURCE_URL = "https://www.douyin.com/video/7585215466331737398"
# 每个 mock 请求的模拟耗时（秒）：所有功能区间都会被夹在 3-10 秒内。
MOCK_NODE_DELAY_MIN_SEC = 3.0
MOCK_NODE_DELAY_MAX_SEC = 10.0
# Backward-compatible constant for legacy imports; new code should call mock_delay_seconds().
MOCK_NODE_DELAY_SEC = MOCK_NODE_DELAY_MIN_SEC
MOCK_DELAY_RANGES: dict[str, tuple[float, float]] = {
    "asr": (3.0, 5.0),
    "lines": (3.0, 5.0),
    "preview": (3.0, 4.0),
    "rw": (5.0, 8.0),
    "storyboard": (5.0, 8.0),
    "guiguzi_analysis": (4.0, 7.0),
    "guiguzi_topics": (5.0, 8.0),
    "tts": (6.0, 10.0),
    "image": (6.0, 10.0),
    "render": (6.0, 10.0),
    "regen_model": (4.0, 7.0),
    "regen_image": (4.0, 8.0),
    "regen_sketch": (4.0, 8.0),
    "regen_tts": (5.0, 9.0),
    "default": (3.0, 10.0),
}


def mock_delay_seconds(kind: str | None = None) -> float:
    lo, hi = MOCK_DELAY_RANGES.get(kind or "default", MOCK_DELAY_RANGES["default"])
    lo = max(MOCK_NODE_DELAY_MIN_SEC, min(float(lo), MOCK_NODE_DELAY_MAX_SEC))
    hi = max(lo, min(float(hi), MOCK_NODE_DELAY_MAX_SEC))
    return random.uniform(lo, hi)


def sleep_mock_delay(kind: str | None = None) -> None:
    time.sleep(mock_delay_seconds(kind))

# 开关参数：mock 作品如何拼装（改这里即可调 mock 行为）
MOCK_CONFIG: dict[str, Any] = {
    "title": "一口气说清：海南封关意味着啥？ #零距离看懂财经 #燃起来了大国重器",
    "rw_models": [
        # label 仅作前端未知 id 的兜底展示；前端 MODEL_LABELS 已统一泛化为「改写方案 X」
        {"id": "opus", "label": "改写方案 A"},
        {"id": "gpt5", "label": "改写方案 B"},
        {"id": "gemini_local", "label": "改写方案 C"},
        {"id": "deepseek", "label": "改写方案 D"},
    ],
}

_REPO_ROOT = Path(__file__).resolve().parents[3]
# final_preview 素材源候选：优先用户的 ncds-materials，回退仓库内模板
_SOURCE_CANDIDATES = [
    _REPO_ROOT.parent / "ncds-materials" / ".final-preview-assets",
    _core_template_dir("final_preview") / ".final-preview-assets",
]

_REFERENCE_DIRS_BY_NODE: dict[str, tuple[str, ...]] = {
    "asr": ("01_collect",),
    "rw": ("02_rw",),
    "lines": ("02_rw",),
    "storyboard": ("02_rw",),
    "tts": ("02_rw", "04_tts"),
    "image": ("02_rw", "03_image"),
    "preview": ("02_rw", "03_image", "04_tts"),
    "render": ("02_rw", "03_image", "04_tts", "06_render"),
}


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _source_job_dir(video_jobs_dir: Path | None = None) -> Path | None:
    roots: list[Path] = []
    if video_jobs_dir is not None:
        roots.append(video_jobs_dir)
    roots.append(_REPO_ROOT / "video-jobs")
    for root in roots:
        d = root / MOCK_SOURCE_JOB_ID
        if (d / "pipeline_state.json").is_file():
            return d
    return None


def _load_reference_state(video_jobs_dir: Path | None = None) -> dict[str, Any] | None:
    d = _source_job_dir(video_jobs_dir)
    if d is None:
        return None
    try:
        return json.loads((d / "pipeline_state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _copy_reference_dirs(job_dir: Path, node_name: str) -> None:
    src_job = _source_job_dir(job_dir.parent)
    if src_job is None:
        return
    for rel in _REFERENCE_DIRS_BY_NODE.get(node_name, ()):
        src = src_job / rel
        if not src.exists():
            continue
        dst = job_dir / rel
        if src.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)


def _reference_node_outputs(job_dir: Path, node_name: str) -> dict[str, Any] | None:
    state = _load_reference_state(job_dir.parent)
    if state is None:
        return None
    node = (state.get("nodes") or {}).get(node_name)
    if not isinstance(node, dict):
        return None
    outputs = node.get("outputs")
    if not isinstance(outputs, dict):
        return None
    if outputs or node_name == "preview":
        _copy_reference_dirs(job_dir, node_name)
        return _json_clone(outputs)
    return None


def _load_reference_guiguzi(video_jobs_dir: Path | None = None) -> dict[str, Any] | None:
    d = _source_job_dir(video_jobs_dir)
    if d is None:
        return None
    p = d / "guiguzi.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def reference_guiguzi_analysis(
    video_jobs_dir: Path,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    doc = _load_reference_guiguzi(video_jobs_dir)
    analysis = (doc or {}).get("analysis")
    if not isinstance(analysis, dict):
        return None
    if on_progress:
        on_progress("鬼谷子：分析爆款原因中...")
    sleep_mock_delay("guiguzi_analysis")
    return _json_clone(analysis)


def _reference_agy_topics(anchor_comment: str) -> list[dict[str, Any]]:
    return [
        {
            "title": "海南封关后，普通人第一笔钱该怎么赚？从免税账单拆到小生意机会",
            "angle": "不讲宏大政策，直接把封关拆成普通人能看见的三笔账：省钱、倒货、开小店",
            "why": "承接原片里“苹果省两千、榴莲腰斩”的强利益钩子，把评论里的省钱兴奋点升级成可执行的机会判断",
            "potential": 96,
            "anchor_comment": anchor_comment,
            "source_model": "agy",
        },
        {
            "title": "别只盯着免税店，海南封关真正值钱的是这条隐藏生意链",
            "angle": "用一条货物流转链讲清一线放开、二线管住、岛内自由分别让谁赚钱",
            "why": "把抽象规则变成“货从哪来、税在哪省、钱被谁赚走”的故事线，更适合短视频连续留存",
            "potential": 94,
            "anchor_comment": anchor_comment,
            "source_model": "agy",
        },
        {
            "title": "为什么说现在去海南，不是旅游，而是在看下一轮造富地图",
            "angle": "对比迪拜、新加坡和海南，把普通人的窗口期讲成一张提前卡位地图",
            "why": "原片后半段的迪拜类比已经具备情绪势能，适合放大为“早去的人吃红利”的行动钩子",
            "potential": 92,
            "anchor_comment": anchor_comment,
            "source_model": "agy",
        },
        {
            "title": "海南封关不是围起来，而是给钱换了条路走",
            "angle": "用“不管人，只管货”做反差钩子，专门纠正大众对封关的第一误解",
            "why": "直接回应高频误解，开头冲突强，适合把政策解释做成反常识爆款",
            "potential": 90,
            "anchor_comment": anchor_comment,
            "source_model": "agy",
        },
    ]


def _fill_reference_guiguzi_topics(
    candidates: dict[str, Any],
    topics: list[Any],
) -> tuple[dict[str, Any], list[Any]]:
    next_candidates = _json_clone(candidates)
    next_topics = _json_clone(topics)
    agy = next_candidates.get("agy")
    if isinstance(agy, dict) and agy.get("topics"):
        return next_candidates, next_topics

    anchor = ""
    for topic in next_topics:
        if isinstance(topic, dict) and topic.get("anchor_comment"):
            anchor = str(topic.get("anchor_comment") or "")
            break
    if not anchor:
        anchor = "明年换新手机的时候去趟海南用省下来的钱当路费，相当于免路费旅游"

    agy_topics = _reference_agy_topics(anchor)
    next_candidates["agy"] = {"topics": agy_topics, "error": None}
    seen = {str(t.get("title") or "") for t in next_topics if isinstance(t, dict)}
    for topic in agy_topics:
        if topic["title"] not in seen:
            next_topics.append(topic)
            seen.add(topic["title"])
    return next_candidates, next_topics


def reference_guiguzi_topics(
    video_jobs_dir: Path,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    doc = _load_reference_guiguzi(video_jobs_dir)
    if not isinstance(doc, dict):
        return None
    candidates = doc.get("candidates")
    topics = doc.get("topics")
    if not isinstance(candidates, dict) or not isinstance(topics, list):
        return None
    if on_progress:
        on_progress("鬼谷子：生成候选选题中...")
    sleep_mock_delay("guiguzi_topics")
    filled_candidates, filled_topics = _fill_reference_guiguzi_topics(candidates, topics)
    return {
        "candidates": filled_candidates,
        "topics": filled_topics,
        "out": doc.get("out"),
        "prompt": doc.get("prompt"),
    }


def _source_dir() -> Path:
    for d in _SOURCE_CANDIDATES:
        if (d / "episode.json").is_file():
            return d
    raise FileNotFoundError(
        "final_preview 素材未找到：ncds-materials/.final-preview-assets 与仓库模板均缺 episode.json"
    )


def _load_episode() -> dict[str, Any]:
    episode = json.loads((_source_dir() / "episode.json").read_text(encoding="utf-8"))
    _ensure_visual_shots(episode)
    return episode


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


def _ensure_visual_shots(episode: dict[str, Any]) -> None:
    """把样例素材迁到当前 visual.shots 契约，供 mock 跑新链路。"""
    visual = episode.get("visual") if isinstance(episode.get("visual"), dict) else {}
    visual = dict(visual or {})
    if isinstance(visual.get("shots"), list) and visual["shots"]:
        episode["visual"] = visual
        episode["scenes"] = {}
        return

    scenes = episode.get("scenes") if isinstance(episode.get("scenes"), dict) else {}
    shots: list[dict[str, Any]] = []
    for i, beat in enumerate(episode.get("beats") or [], start=1):
        if not isinstance(beat, dict):
            continue
        sid = str(beat.get("scene") or "").strip()
        sc = scenes.get(sid) if isinstance(scenes.get(sid), dict) else {}
        raw_assets = sc.get("sketches") if isinstance(sc.get("sketches"), list) else []
        assets: list[dict[str, Any]] = []
        for n, sk in enumerate([a for a in raw_assets if isinstance(a, dict)], start=1):
            aid = f"a{n}"
            assets.append({
                "id": aid,
                "role": "main" if n == 1 else "support",
                "prompt": str(sk.get("prompt") or f"a simple pictogram for: {beat.get('zh') or ''}"),
                "pos": sk.get("pos") if isinstance(sk.get("pos"), dict) else {"x": 50, "y": 52},
                "size": sk.get("size") if isinstance(sk.get("size"), (int, float)) else 34,
                "motion": sk.get("motion") if isinstance(sk.get("motion"), dict) else {"enter": "zoom-pop", "duration": 500},
                "imageFile": f"pictures/b{i:03d}-{aid}.webp",
            })
        if not assets:
            assets.append({
                "id": "a1",
                "role": "main",
                "prompt": f"a simple black pictogram symbolizing this line: {beat.get('zh') or ''}",
                "pos": {"x": 50, "y": 52},
                "size": 34,
                "motion": {"enter": "zoom-pop", "duration": 500},
                "imageFile": f"pictures/b{i:03d}-a1.webp",
            })
        shots.append({
            "beatIndex": i,
            "shotId": f"b{i:03d}",
            "group": str((sc or {}).get("group") or sid or f"S1-{((i - 1) // 4) + 1:02d}"),
            "intent": str((sc or {}).get("prompt") or beat.get("zh") or ""),
            "layout": "center_icon",
            "transition": "replace",
            "motion": {"enter": "fade", "duration": 500},
            "emphasis": [],
            "assets": assets,
        })

    image_cfg = episode.get("image") if isinstance(episode.get("image"), dict) else {}
    bg = image_cfg.get("background") if isinstance(image_cfg.get("background"), dict) else {}
    visual["style"] = visual.get("style") or "paper_card_talk"
    visual["stage"] = {
        "background": {
            "prompt": str((bg or {}).get("prompt") or "16:9 全幅暖纸质感页面背景，淡雅远景层次贯穿整张画面，细腻纸张纹理，主体元素留给前景素材，无文字，无数字。"),
            "imageFit": "cover",
            "imageFile": "pictures/background.webp",
        },
        "palette": visual.get("palette") if isinstance(visual.get("palette"), dict) else {},
        "shotRhythm": "one-shot-per-beat",
    }
    visual["shots"] = shots
    episode["visual"] = visual
    episode["scenes"] = {}


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
            "index": 1, "url": FALLBACK_SOURCE_URL, "title": MOCK_CONFIG["title"],
            "author": "直男财经", "transcript_relpath": "01_asr/1/article.md",
            "article_relpath": "01_asr/1/article.md", "error": None,
        }],
        "asr_dir": "01_asr",
    }


def _mock_rw(job_dir: Path, episode: dict[str, Any]) -> dict[str, Any]:
    body = _body_from_beats(episode)
    episode_json = json.dumps(episode, ensure_ascii=False, indent=2)
    rw = job_dir / "02_rw"
    rw.mkdir(parents=True, exist_ok=True)
    # 02_rw/episode.json 是 preview.py 与下游的 canonical 来源：rw 阶段就落整份 episode
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
    visual = episode.get("visual") if isinstance(episode.get("visual"), dict) else {}
    shots = [s for s in (visual.get("shots") or []) if isinstance(s, dict)]
    asset_total = sum(len(s.get("assets") or []) for s in shots)
    groups = {str(s.get("group") or "").strip() for s in shots if s.get("group")}
    return {
        "episode_relpath": "02_rw/episode.json",
        "shots_count": len(shots),
        "assets_count": asset_total,
        "groups_count": len(groups),
        "beats_count": len(episode.get("beats") or []),
        "background_count": 1,
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
    background_src = img_dir / "background.webp"
    if not background_src.is_file():
        for sid in _scene_order(episode):
            cand = img_dir / f"{sid}.webp"
            if cand.is_file():
                shutil.copyfile(cand, background_src)
                break
    visual = episode.get("visual") if isinstance(episode.get("visual"), dict) else {}
    shots = [s for s in (visual.get("shots") or []) if isinstance(s, dict)]
    items = []
    for i, shot in enumerate(shots, start=1):
        sid = str(shot.get("shotId") or f"b{i:03d}")
        assets = []
        for n, asset in enumerate([a for a in (shot.get("assets") or []) if isinstance(a, dict)], start=1):
            aid = str(asset.get("id") or f"a{n}")
            rel = f"03_image/{sid}-{aid}.webp"
            target = job_dir / rel
            if not target.is_file() and background_src.is_file():
                shutil.copyfile(background_src, target)
            has = (job_dir / rel).is_file()
            assets.append({
                "index": n,
                "asset_id": aid,
                "role": str(asset.get("role") or ""),
                "prompt": str((asset or {}).get("prompt") or ""),
                "pos": asset.get("pos") if isinstance(asset.get("pos"), dict) else {"x": 50, "y": 50},
                "size": asset.get("size") if isinstance(asset.get("size"), (int, float)) else 32,
                "motion": asset.get("motion") if isinstance(asset.get("motion"), dict) else {},
                "image_relpath": rel if has else None,
                "status": "done" if has else "queued",
            })
        items.append({
            "shot_id": sid,
            "beat_index": int(shot.get("beatIndex") or i),
            "group": str(shot.get("group") or ""),
            "intent": str(shot.get("intent") or ""),
            "layout": str(shot.get("layout") or ""),
            "transition": str(shot.get("transition") or ""),
            "background_relpath": "03_image/background.webp",
            "status": "done",
            "assets": assets,
        })
    bg_rel = "03_image/background.webp"
    bg_has = (job_dir / bg_rel).is_file()
    background = {
        "id": "background",
        "prompt": str(((episode.get("image") or {}).get("background") or {}).get("prompt") or ""),
        "image_relpath": bg_rel if bg_has else None,
        "status": "done" if bg_has else "queued",
        "variants": [],
        "selected_variant_relpath": bg_rel if bg_has else None,
    }
    foreground_ready = sum(1 for it in items for asset in (it.get("assets") or []) if asset.get("image_relpath"))
    foreground_total = sum(len(it.get("assets") or []) for it in items)
    return {
        "background": background,
        "items": items,
        "pictures_dir": "03_image",
        "pictures_count": (1 if bg_has else 0) + foreground_ready,
        "ok": 1 if bg_has else 0,
        "skipped": 0,
        "failed": 0 if bg_has else 1,
        "asset_ok": foreground_ready,
        "asset_failed": 0,
        "asset_summary": {
            "background_total": 1,
            "background_ready": 1 if bg_has else 0,
            "shot_total": len(items),
            "foreground_total": foreground_total,
            "foreground_ready": foreground_ready,
            "foreground_failed": 0,
        },
    }


def _mock_preview(job_dir: Path, episode: dict[str, Any]) -> dict[str, Any]:
    # preview 真实流程没有后台任务（iframe 直接读 02_rw/episode.json + 已落盘素材）；
    # mock 里只需「通过审核」即 done，让下游 render 的 dep 满足。
    return {}


def _first_file(*roots: Path, patterns: tuple[str, ...]) -> Path | None:
    for root in roots:
        if root.is_file():
            return root
        if not root.is_dir():
            continue
        for pattern in patterns:
            found = sorted(root.glob(pattern))
            if found:
                return found[0]
    return None


def _synthesize_mock_mp4(job_dir: Path, source_dir: Path, out_path: Path) -> int:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found; cannot synthesize demo render video")

    image = _first_file(
        job_dir / "03_image" / "background.webp",
        job_dir / "03_image",
        source_dir / "pictures" / "background.webp",
        source_dir / "pictures",
        patterns=("*.webp", "*.png", "*.jpg", "*.jpeg"),
    )
    audio = _first_file(job_dir / "04_tts", source_dir / "audio", patterns=("*.mp3", "*.wav", "*.m4a"))

    vf = (
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
        "format=yuv420p"
    )
    if image is not None:
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-i",
            str(image),
        ]
    else:
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0xf4eadc:s=1920x1080:r=30",
        ]

    if audio is not None:
        cmd.extend(["-i", str(audio)])

    cmd.extend([
        "-t",
        "8",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
    ])
    if audio is not None:
        cmd.extend(["-c:a", "aac", "-b:a", "128k", "-shortest"])
    else:
        cmd.extend(["-an"])
    cmd.append(str(out_path))

    subprocess.run(cmd, check=True, timeout=15)
    return out_path.stat().st_size


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
    if size is None:
        size = _synthesize_mock_mp4(job_dir, src, out_path)
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
    reference_outputs = _reference_node_outputs(job_dir, node_name)
    if reference_outputs is not None:
        return reference_outputs

    builder = _NODE_BUILDERS.get(node_name)
    if builder is None:
        return {}
    return builder(job_dir, _load_episode())


def ensure_mock_job(runner: Any) -> str:
    """（重新）种一个 mock 作品，返回 job_id。

    幂等：每次调用重置成「只有 START done、其余 idle、mock=True」的初始态，并清掉历史
    产物，让用户每次都能从 START 一路点到底。逐节点产物在各节点被 run 时才生成。
    """
    source_state = _load_reference_state(runner.video_jobs_dir)
    if source_state is None:
        _source_dir()  # 提前校验素材在；缺失直接 FileNotFoundError -> 路由 404
    job_dir = runner.video_jobs_dir / MOCK_JOB_ID
    shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    title = str((source_state or {}).get("title") or MOCK_CONFIG["title"])
    inputs = dict((source_state or {}).get("inputs") or {})
    if not inputs:
        shares = [{"url": FALLBACK_SOURCE_URL, "title": title, "author": "直男财经", "tags": []}]
        inputs = {"url": FALLBACK_SOURCE_URL, "urls": [FALLBACK_SOURCE_URL], "raw_text": "", "shares": shares}
    shares = inputs.get("shares")
    if not isinstance(shares, list):
        shares = [{"url": inputs.get("url") or FALLBACK_SOURCE_URL, "title": title, "author": "直男财经", "tags": []}]
        inputs["shares"] = shares

    nodes: dict[str, NodeState] = {}
    for nd in get_pipeline(MOCK_PIPELINE_ID).nodes:
        if nd.kind == "input":
            nodes[nd.name] = NodeState(
                name=nd.name, status="done", started_at=now, finished_at=now,
                progress="完成",
                outputs=dict(inputs),
                error=None, task_id=None,
            )
        else:
            nodes[nd.name] = NodeState(name=nd.name, status="idle")

    state = JobState(
        job_id=MOCK_JOB_ID, pipeline_id=MOCK_PIPELINE_ID, title=title,
        created_at=now, updated_at=now, inputs=inputs, nodes=nodes,
        node_configs=dict((source_state or {}).get("node_configs") or {}),
        mock=True,
    )
    runner._save(state)
    return MOCK_JOB_ID
