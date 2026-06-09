"""产物审看路由：把 ``state/`` 和 ``video-jobs/`` 下的产物以 HTTP 暴露给移动端。

和 ``routes/jobs.py``（只服务 video-jobs/）的区别：本路由覆盖白名单内的多个根
（state/ + video-jobs/），供 agent 产物的统一审看 —— 读稿(.md)、听音(.mp3)、看片(.mp4)、
看数据(.json)、浏览采集目录。FileResponse 按扩展名给 content-type，且支持 Range 请求，
iPad 上可拖动音视频进度。

端点
----
GET /artifacts/files/{relpath:path}   返回单个文件（relpath 相对仓库根，首段须为 state/video-jobs）
GET /artifacts/dir/{relpath:path}     列目录条目（name/is_dir/size/url）

安全
----
relpath resolve 后必须仍在 ARTIFACTS_ROOT 之下，且首段在白名单 -> 否则 403；
不在白名单根（如试图读 src/ 或 .env）-> 403；文件不存在 -> 404。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ncds_opus_factory.server.artifacts import (
    ALLOWED_ROOTS,
    ARTIFACTS_ROOT,
    dir_url,
    file_url,
    kind_of,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_safe(relpath: str) -> Path:
    """把 relpath 拼到 ARTIFACTS_ROOT 下，校验未越界且首段在白名单根。"""
    root = ARTIFACTS_ROOT.resolve()
    target = (root / relpath).resolve()
    try:
        rel = target.relative_to(root)
    except ValueError as exc:
        logger.warning("[artifacts] path-traversal blocked: relpath=%r resolved=%s", relpath, target)
        raise HTTPException(403, "relpath escapes artifacts root") from exc
    if not rel.parts or rel.parts[0] not in ALLOWED_ROOTS:
        raise HTTPException(403, f"root not allowed: {rel.parts[0] if rel.parts else '<empty>'}")
    return target


@router.get("/artifacts/files/{relpath:path}")
async def get_artifact_file(relpath: str) -> FileResponse:
    """流式返回产物文件（含 Range 支持，供 iPad 拖动音视频）。"""
    target = _resolve_safe(relpath)
    if not target.exists():
        raise HTTPException(404, f"artifact not found: {relpath}")
    if not target.is_file():
        raise HTTPException(400, f"not a regular file: {relpath}")
    return FileResponse(path=str(target))


@router.get("/artifacts/dir/{relpath:path}")
async def list_artifact_dir(relpath: str) -> dict[str, Any]:
    """列出目录条目，便于移动端浏览采集目录 / 分镜实例 / 待验收清单。"""
    target = _resolve_safe(relpath)
    if not target.exists():
        raise HTTPException(404, f"dir not found: {relpath}")
    if not target.is_dir():
        raise HTTPException(400, f"not a directory: {relpath}")

    entries: list[dict[str, Any]] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
        is_dir = child.is_dir()
        entries.append({
            "name": child.name,
            "is_dir": is_dir,
            "size": None if is_dir else child.stat().st_size,
            "kind": "dir" if is_dir else kind_of(child),
            "url": dir_url(child) if is_dir else file_url(child),
        })
    return {"relpath": relpath, "entries": entries}
