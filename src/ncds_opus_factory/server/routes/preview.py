"""HTML 预览路由：给 PREVIEW 节点的 iframe 提供"模板 + 用户 episode + 已生成素材"的合成视图。

模板形态：final_preview（final-preview.html + .final-preview-assets/）

URL 形态
--------
GET  /preview/{job_id}/                                  → final-preview.html
GET  /preview/{job_id}/final-preview.html                    → 同上
GET  /preview/{job_id}/.final-preview-assets/episode.json
        → video-jobs/{job_id}/02_rw/episode.json，缺失就 404（rw 节点必须先跑）
GET  /preview/{job_id}/.final-preview-assets/audio/<file>
        → video-jobs/{job_id}/04_tts/<file>，缺失就 404（tts 节点必须先跑）
GET  /preview/{job_id}/.final-preview-assets/pictures/<file>
        → video-jobs/{job_id}/03_image/<file>，缺失就 404（image 节点必须先跑）
GET  /preview/{job_id}/.final-preview-assets/fonts/<path>
        → 模板内置字体目录（35 个家族）
GET  /preview/{job_id}/.final-preview-assets/<其他>
        → 模板原资产（bootstrap / player / styles / motion / fonts 等）

设计原则
--------
内容产物（episode / audio / pictures）只从对应 pipeline 节点拿，不降级到模板自带的样例
数据。模板目录只提供引擎层（HTML / JS / CSS / 字体）。这样用户看到"模板自带的旧
内容"必然是 bug——产物缺失就明确 404。

安全
----
target 路径必须落在 模板目录或 job 目录之下，否则 403。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from ncds_opus_factory.server.state import PIPELINE_RUNNER

logger = logging.getLogger(__name__)

router = APIRouter()

# preview.py 路径：routes/preview.py
# parents[0]=routes, [1]=server, [2]=ncds_opus_factory, [3]=src, [4]=repo root
_PACKAGE_DIR = Path(__file__).resolve().parents[2]  # ncds_opus_factory/
# final_preview 已迁入 core（P1.3）
from ncds_opus_core.templates import template_dir as _core_template_dir

_TEMPLATE_DIR = _core_template_dir("final_preview")
_ASSETS_DIR_NAME = ".final-preview-assets"
_ASSETS_PREFIX = _ASSETS_DIR_NAME + "/"
_HTML_FILE = "final-preview.html"


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
# 静态资源 / HTML 入口
# ──────────────────────────────────────────────────────────────────

# GET + HEAD：iframe src 走 GET，但探活/预检逻辑可能用 HEAD 预取资源头，一并支持。
@router.api_route("/preview/{job_id}", methods=["GET", "HEAD"])
@router.api_route("/preview/{job_id}/", methods=["GET", "HEAD"])
async def preview_root(job_id: str) -> FileResponse:
    _require_job(job_id)
    return FileResponse(_TEMPLATE_DIR / _HTML_FILE)


@router.api_route("/preview/{job_id}/{full_path:path}", methods=["GET", "HEAD"])
async def preview_serve(job_id: str, full_path: str) -> Response:
    _require_job(job_id)
    job_dir = PIPELINE_RUNNER.video_jobs_dir / job_id

    # —— episode.json：必须 rw 节点已产出，否则 404（不降级模板自带）
    if full_path == _ASSETS_PREFIX + "episode.json":
        ep_job = job_dir / "02_rw" / "episode.json"
        if not ep_job.is_file():
            raise HTTPException(404, "episode.json not produced yet; run rw first")
        try:
            episode = json.loads(ep_job.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(500, f"episode.json invalid: {exc.msg}") from exc
        return Response(
            content=json.dumps(episode, ensure_ascii=False, separators=(",", ":")),
            media_type="application/json",
        )

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

    # —— 其余资产 / HTML（含 fonts/、bootstrap.js / player.js / styles.css / motion.css 等）：从模板返回
    target = _safe_join(_TEMPLATE_DIR, full_path)
    if not target.is_file():
        raise HTTPException(404, f"asset not found: {full_path}")
    return FileResponse(target)
