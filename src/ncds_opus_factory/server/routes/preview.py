"""HTML 预览路由：给 PREVIEW 节点的 iframe 提供"模板 + 用户 episode + 已生成素材"的合成视图。

URL 形态
--------
GET /preview/{job_id}/                                  → 011-reading-confidence.html
GET /preview/{job_id}/011-reading-confidence.html       → 同上
GET /preview/{job_id}/.011-reading-confidence-assets/episode.json
        → 优先 video-jobs/{job_id}/02_rw/episode.json，没有时降级到模板自带
GET /preview/{job_id}/.011-reading-confidence-assets/audio/<file>
        → video-jobs/{job_id}/04_tts/<file>，没有 404
GET /preview/{job_id}/.011-reading-confidence-assets/pictures/<file>
        → 优先 video-jobs/{job_id}/03_image/<file>，没有降级到模板自带
GET /preview/{job_id}/.011-reading-confidence-assets/<其他>
        → 模板原资产（bootstrap/player/styles/motion 等脚本与样式）

安全
----
target 路径必须落在 模板目录或 job 目录之下，否则 403。

使用动机
--------
让前端 PREVIEW 节点抽屉里的 <iframe src="/preview/{job_id}/011-reading-confidence.html">
能立刻看到"我的 episode + 已生成素材"的真实渲染效果，而不是模板默认 episode。
用户在编辑器里改 episode 保存后，iframe reload 即可见新效果。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ncds_opus_factory.server.state import PIPELINE_RUNNER

logger = logging.getLogger(__name__)

router = APIRouter()

# preview.py 路径：routes/preview.py
# parents[0]=routes, [1]=server, [2]=ncds_opus_factory, [3]=src, [4]=repo root
_PACKAGE_DIR = Path(__file__).resolve().parents[2]  # ncds_opus_factory/
_TEMPLATE_DIR = _PACKAGE_DIR / "templates" / "paper_card_talk_011"
_ASSETS_DIR_NAME = ".011-reading-confidence-assets"
_ASSETS_PREFIX = _ASSETS_DIR_NAME + "/"
_HTML_FILE = "011-reading-confidence.html"


def _safe_join(base: Path, relpath: str) -> Path:
    """把 relpath 拼到 base 下并防 path traversal。"""
    base_resolved = base.resolve()
    target = (base / relpath).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise HTTPException(403, "path traversal blocked") from exc
    return target


@router.get("/preview/{job_id}")
@router.get("/preview/{job_id}/")
async def preview_root(job_id: str) -> FileResponse:
    _require_job(job_id)
    return FileResponse(_TEMPLATE_DIR / _HTML_FILE)


@router.get("/preview/{job_id}/{full_path:path}")
async def preview_serve(job_id: str, full_path: str) -> FileResponse:
    _require_job(job_id)
    job_dir = PIPELINE_RUNNER.video_jobs_dir / job_id

    # —— episode.json：优先 job 产出
    if full_path == _ASSETS_PREFIX + "episode.json":
        ep_job = job_dir / "02_rw" / "episode.json"
        if ep_job.is_file():
            return FileResponse(ep_job)
        return FileResponse(_TEMPLATE_DIR / _ASSETS_DIR_NAME / "episode.json")

    # —— audio：仅从 job 04_tts 取，缺失 404（HTML 会自己 graceful fallback）
    if full_path.startswith(_ASSETS_PREFIX + "audio/"):
        rel = full_path[len(_ASSETS_PREFIX + "audio/"):]
        target = _safe_join(job_dir / "04_tts", rel)
        if not target.is_file():
            raise HTTPException(404, f"audio not yet generated: {rel}")
        return FileResponse(target)

    # —— pictures：优先 job 03_image，缺失降级模板
    if full_path.startswith(_ASSETS_PREFIX + "pictures/"):
        rel = full_path[len(_ASSETS_PREFIX + "pictures/"):]
        job_pic = _safe_join(job_dir / "03_image", rel)
        if job_pic.is_file():
            return FileResponse(job_pic)
        tpl_pic = _safe_join(_TEMPLATE_DIR / _ASSETS_DIR_NAME / "pictures", rel)
        if tpl_pic.is_file():
            return FileResponse(tpl_pic)
        raise HTTPException(404, f"picture not found: {rel}")

    # —— 其余资产 / HTML：从模板返回
    target = _safe_join(_TEMPLATE_DIR, full_path)
    if not target.is_file():
        raise HTTPException(404, f"asset not found: {full_path}")
    return FileResponse(target)


def _require_job(job_id: str) -> None:
    try:
        PIPELINE_RUNNER.get_job(job_id)
    except KeyError:
        raise HTTPException(404, f"job not found: {job_id}")
