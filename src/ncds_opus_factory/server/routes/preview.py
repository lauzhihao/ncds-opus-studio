"""HTML 预览路由：给 PREVIEW 节点的 iframe 提供"模板 + 用户 episode + 已生成素材"的合成视图。

模板形态：paper_card_talk_015（015-draft.html + .015-draft-assets/，含内置 edit-mode + inspector）

URL 形态
--------
GET  /preview/{job_id}/                                  → 015-draft.html
GET  /preview/{job_id}/015-draft.html                    → 同上
GET  /preview/{job_id}/.015-draft-assets/episode.json
        → video-jobs/{job_id}/02_rw/episode.json，没有 404（rw 节点必须先跑）
GET  /preview/{job_id}/.015-draft-assets/audio/<file>
        → video-jobs/{job_id}/04_tts/<file>，没有 404（tts 节点必须先跑）
GET  /preview/{job_id}/.015-draft-assets/pictures/<file>
        → video-jobs/{job_id}/03_image/<file>，没有 404（image 节点必须先跑）
GET  /preview/{job_id}/.015-draft-assets/fonts/<path>
        → 模板内置字体目录（35 个家族）
GET  /preview/{job_id}/.015-draft-assets/<其他>
        → 模板原资产（bootstrap / player / edit-mode / inspector 等脚本与样式）

设计原则
--------
内容产物（episode / audio / pictures）只从对应 pipeline 节点拿，不降级到模板自带的样例
数据。模板目录只提供引擎层（HTML / JS / CSS / 字体）。这样用户看到"模板自带的旧
内容"必然是 bug——产物缺失就明确 404。

编辑写盘端点（被 015 内置 edit-mode.js / inspector.jsx / tweaks.jsx 调用，全 iframe 相对路径）
GET  /preview/{job_id}/__ping                            → 200 OK，启用编辑 UI
POST /preview/{job_id}/__save_overlays                   → 按 scene+index 深合并 patch 到 overlays[]
POST /preview/{job_id}/__save_episode                    → 按 dot-path 写回 episode（meta/visual/playback 等）
POST /preview/{job_id}/__add_overlay                     → 给指定 scene 追加 overlay
POST /preview/{job_id}/__del_overlay                     → 删指定 scene 的指定 overlay
GET  /preview/{job_id}/__reload_events                   → SSE 空连接（保持 015 dev-reload.js 不发疯重连，但永不推 reload）

安全
----
target 路径必须落在 模板目录或 job 目录之下，否则 403。
POST body 里的 `slug` 字段忽略（保留兼容原协议）；落点完全由 URL 里的 {job_id} 决定。
所有写盘走 PIPELINE_RUNNER.write_episode，自动触发下游节点 invalidate。
"""

from __future__ import annotations

import asyncio
import copy
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from ncds_opus_factory.server.state import PIPELINE_RUNNER

logger = logging.getLogger(__name__)

router = APIRouter()

# preview.py 路径：routes/preview.py
# parents[0]=routes, [1]=server, [2]=ncds_opus_factory, [3]=src, [4]=repo root
_PACKAGE_DIR = Path(__file__).resolve().parents[2]  # ncds_opus_factory/
_TEMPLATE_DIR = _PACKAGE_DIR / "templates" / "paper_card_talk_015"
_ASSETS_DIR_NAME = ".015-draft-assets"
_ASSETS_PREFIX = _ASSETS_DIR_NAME + "/"
_HTML_FILE = "015-draft.html"


def _safe_join(base: Path, relpath: str) -> Path:
    """把 relpath 拼到 base 下并防 path traversal。"""
    base_resolved = base.resolve()
    target = (base / relpath).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise HTTPException(403, "path traversal blocked") from exc
    return target


def _require_job(job_id: str) -> None:
    try:
        PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")


# ──────────────────────────────────────────────────────────────────
# 编辑端点：放在静态路由前面，避免被 {full_path:path} 兜底吞掉
# ──────────────────────────────────────────────────────────────────

@router.get("/preview/{job_id}/__ping")
async def preview_ping(job_id: str) -> dict[str, Any]:
    """015 bootstrap.js 用 GET __ping 判定编辑 UI 是否启用；200 → 启用。"""
    _require_job(job_id)
    return {"ok": True, "service": "ncds-opus-studio:preview"}


@router.get("/preview/{job_id}/__reload_events")
async def preview_reload_events(job_id: str) -> StreamingResponse:
    """SSE 空连接：保持 dev-reload.js 不退化到指数退避重连，但永不推 reload。

    015 自带的 watchfiles 热重载是开发期 fs watcher，studio 用户面前不需要它（编辑
    操作走 episode.json patch、iframe 自己重渲；磁盘上脚本文件用户改不到）。
    所以这里给个 hello 之后只发 keep-alive 注释行，浏览器认为连接活着就不再重连。
    """
    _require_job(job_id)

    async def gen():
        yield b"event: hello\ndata: ok\n\n"
        while True:
            await asyncio.sleep(15)
            yield b": ping\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


def _load_or_404(job_id: str) -> dict[str, Any]:
    ep = PIPELINE_RUNNER.get_episode(job_id)
    if ep is None:
        raise HTTPException(409, "episode.json not yet produced; run rw first")
    return ep


def _deep_merge(dst: Any, patch: Any) -> Any:
    """patch dict 深合并进 dst dict。list / 标量 整体替换；dict 递归合并。

    复刻 ~/projects/ncds-materials/.014-draft-assets/edit-server.py 的同名实现，
    保持和 015 客户端的预期 (deep merge + 整体替换) 一致。
    """
    if not isinstance(dst, dict) or not isinstance(patch, dict):
        return patch
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            dst[k] = _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


@router.post("/preview/{job_id}/__save_overlays")
async def preview_save_overlays(job_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """按 scene+index 深合并 patch 到 overlays[]（edit-mode.js Save 调用）。"""
    _require_job(job_id)
    patches = body.get("patches")
    if not isinstance(patches, list):
        raise HTTPException(400, "patches must be list")

    ep = _load_or_404(job_id)
    scenes = ep.get("scenes") or {}
    touched = 0
    for p in patches:
        if not isinstance(p, dict):
            raise HTTPException(400, "patch entry must be object")
        sid = p.get("scene")
        idx = p.get("index")
        patch = p.get("patch") or {}
        if sid not in scenes:
            raise HTTPException(400, f"scene not found: {sid}")
        ovs = scenes[sid].get("overlays") or []
        if not isinstance(idx, int) or idx < 0 or idx >= len(ovs):
            raise HTTPException(400, f"overlay index out of range: {sid}#{idx} (len={len(ovs)})")
        _deep_merge(ovs[idx], patch)
        touched += 1
    PIPELINE_RUNNER.write_episode(job_id, ep)
    return {"ok": True, "touched": touched}


@router.post("/preview/{job_id}/__save_episode")
async def preview_save_episode(job_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """按 dot-path 写回 episode（Tweaks panel / Inspector 场景设置调用）。

    body.patches = {'meta.title': '...', 'visual.palette': 'sage', ...}
    走中间路径时缺 dict 自动建。值整体替换（不深合并），适合简单标量。
    """
    _require_job(job_id)
    patches = body.get("patches")
    if not isinstance(patches, dict):
        raise HTTPException(400, "patches must be dict of {path: value}")

    ep = _load_or_404(job_id)
    for path, value in patches.items():
        if not isinstance(path, str) or not path:
            raise HTTPException(400, f"bad path: {path!r}")
        parts = path.split(".")
        cur: Any = ep
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = value
    PIPELINE_RUNNER.write_episode(job_id, ep)
    return {"ok": True, "touched": len(patches)}


@router.post("/preview/{job_id}/__add_overlay")
async def preview_add_overlay(job_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """给指定 scene 的 overlays 末尾 append 一个新 overlay；返回新 index。"""
    _require_job(job_id)
    scene = body.get("scene")
    overlay = body.get("overlay") or {}
    if not scene:
        raise HTTPException(400, "missing scene")
    if not isinstance(overlay, dict):
        raise HTTPException(400, "overlay must be an object")

    ep = _load_or_404(job_id)
    scenes = ep.get("scenes") or {}
    if scene not in scenes:
        raise HTTPException(400, f"scene not found: {scene}")
    sc = scenes[scene]
    if not isinstance(sc.get("overlays"), list):
        sc["overlays"] = []
    sc["overlays"].append(copy.deepcopy(overlay))
    new_idx = len(sc["overlays"]) - 1
    PIPELINE_RUNNER.write_episode(job_id, ep)
    return {"ok": True, "index": new_idx}


@router.post("/preview/{job_id}/__del_overlay")
async def preview_del_overlay(job_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """从指定 scene 的 overlays.pop(index)；返回剩余 overlay 数。"""
    _require_job(job_id)
    scene = body.get("scene")
    index = body.get("index")
    if not scene:
        raise HTTPException(400, "missing scene")
    if not isinstance(index, int):
        raise HTTPException(400, "index must be int")

    ep = _load_or_404(job_id)
    scenes = ep.get("scenes") or {}
    if scene not in scenes:
        raise HTTPException(400, f"scene not found: {scene}")
    ovs = scenes[scene].get("overlays")
    if not isinstance(ovs, list):
        raise HTTPException(400, f"scene {scene} has no overlays list")
    if index < 0 or index >= len(ovs):
        raise HTTPException(400, f"overlay index out of range: {scene}#{index} (len={len(ovs)})")
    ovs.pop(index)
    PIPELINE_RUNNER.write_episode(job_id, ep)
    return {"ok": True, "remaining": len(ovs)}


# ──────────────────────────────────────────────────────────────────
# 静态资源 / HTML 入口
# ──────────────────────────────────────────────────────────────────

@router.get("/preview/{job_id}")
@router.get("/preview/{job_id}/")
async def preview_root(job_id: str) -> FileResponse:
    _require_job(job_id)
    return FileResponse(_TEMPLATE_DIR / _HTML_FILE)


@router.get("/preview/{job_id}/{full_path:path}")
async def preview_serve(job_id: str, full_path: str) -> FileResponse:
    _require_job(job_id)
    job_dir = PIPELINE_RUNNER.video_jobs_dir / job_id

    # —— episode.json：必须 rw 节点已产出，否则 404（不降级模板自带）
    if full_path == _ASSETS_PREFIX + "episode.json":
        ep_job = job_dir / "02_rw" / "episode.json"
        if not ep_job.is_file():
            raise HTTPException(404, "episode.json not produced yet; run rw first")
        return FileResponse(ep_job)

    # —— audio：必须 tts 节点已产出，否则 404
    if full_path.startswith(_ASSETS_PREFIX + "audio/"):
        rel = full_path[len(_ASSETS_PREFIX + "audio/"):]
        target = _safe_join(job_dir / "04_tts", rel)
        if not target.is_file():
            raise HTTPException(404, f"audio not yet generated: {rel}")
        return FileResponse(target)

    # —— pictures：必须 image 节点已产出，否则 404（不降级模板自带）
    if full_path.startswith(_ASSETS_PREFIX + "pictures/"):
        rel = full_path[len(_ASSETS_PREFIX + "pictures/"):]
        job_pic = _safe_join(job_dir / "03_image", rel)
        if not job_pic.is_file():
            raise HTTPException(404, f"picture not yet generated: {rel}")
        return FileResponse(job_pic)

    # —— 其余资产 / HTML（含 fonts/、bootstrap.js / styles.css / motion.css /
    #     edit-mode.js / inspector.jsx / tweaks*.jsx 等）：从模板返回
    target = _safe_join(_TEMPLATE_DIR, full_path)
    if not target.is_file():
        raise HTTPException(404, f"asset not found: {full_path}")
    return FileResponse(target)
