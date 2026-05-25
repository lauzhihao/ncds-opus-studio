"""Job artifacts 路由：把 video-jobs/{job_id}/ 下的本地产物以 HTTP 形式暴露。

设计动机
--------
asr / rw / vid 等 commands 在 `video-jobs/{job_id}/deliverables/` 下落本地
产物（results.json、summary.md、drafts/*.json 等）。下游 daoer 需要拉这些
文件以推进画布流程，但 daoer 与 ncds 可能跨机器部署，所以走 HTTP 而不是
共享 fs。

端点
----
GET /jobs/{job_id}/files/{relpath:path}
    relpath 相对 `video-jobs/{job_id}/`，例如 `deliverables/results.json`。
    成功返 FileResponse；不存在 404；非文件 400；越界 403。

安全
----
relpath 经过 `Path.resolve()` 后必须仍在 `video-jobs/{job_id}/` 之下，
否则视为 path-traversal 攻击拒绝。

env override
------------
NOF_VIDEO_JOBS_DIR：覆盖默认 video-jobs 根目录（沿用 state.py 套路）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# ncds 仓库根；jobs.py 在 src/ncds_opus_factory/server/routes/ 下，parents[4] 才到仓库根
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_VIDEO_JOBS_DIR = _REPO_ROOT / "video-jobs"

VIDEO_JOBS_DIR: Path = Path(
    os.environ.get("NOF_VIDEO_JOBS_DIR", _DEFAULT_VIDEO_JOBS_DIR)
)


def _resolve_safe(job_id: str, relpath: str) -> Path:
    """把 (job_id, relpath) 拼成绝对路径，并防御 path traversal。

    Raises:
        HTTPException(403): relpath 试图越界出 job_dir
        HTTPException(404): job_dir 不存在
    """
    if not job_id or "/" in job_id or job_id in (".", ".."):
        raise HTTPException(400, f"invalid job_id: {job_id!r}")

    job_dir = (VIDEO_JOBS_DIR / job_id).resolve()
    # job_dir 自身存在性检查
    if not job_dir.is_dir():
        raise HTTPException(404, f"job not found: {job_id}")

    # relpath 拼接后再次 resolve，校验仍在 job_dir 之下
    target = (job_dir / relpath).resolve()
    try:
        target.relative_to(job_dir)
    except ValueError as exc:
        # relpath 含 `..` 等导致越界
        logger.warning(
            "[jobs] path-traversal blocked: job_id=%s relpath=%r resolved=%s",
            job_id, relpath, target,
        )
        raise HTTPException(403, "relpath escapes job directory") from exc

    return target


@router.get("/jobs/{job_id}/files/{relpath:path}")
async def get_job_file(job_id: str, relpath: str) -> FileResponse:
    """流式返回 video-jobs/{job_id}/{relpath} 文件内容。"""
    target = _resolve_safe(job_id, relpath)

    if not target.exists():
        raise HTTPException(404, f"file not found: {relpath}")
    if not target.is_file():
        raise HTTPException(400, f"not a regular file: {relpath}")

    return FileResponse(path=str(target))


class WriteFileBody(BaseModel):
    text: str


@router.put("/jobs/{job_id}/files/{relpath:path}")
async def put_job_file(job_id: str, relpath: str, body: WriteFileBody) -> dict:
    """文本写回 video-jobs/{job_id}/{relpath}；用于用户在抽屉里编辑精华稿等场景。

    只接 UTF-8 文本。安全检查走 _resolve_safe 防 path-traversal。
    """
    target = _resolve_safe(job_id, relpath)
    if target.exists() and not target.is_file():
        raise HTTPException(400, f"not a regular file: {relpath}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.text, encoding="utf-8")
    return {"ok": True, "relpath": relpath, "bytes": len(body.text.encode("utf-8"))}
