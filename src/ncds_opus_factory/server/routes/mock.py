"""Mock 路由：种一个 final_preview mock 作品（开发预览/录屏用）。

studio 前端 URL 带 m=1/mock=1 时调 POST /mock/ensure 确保 mock 作品存在，返回其 job_id。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ncds_opus_factory.server import mock as mock_mod
from ncds_opus_factory.server.access import current_owner_id
from ncds_opus_factory.server.state import PIPELINE_RUNNER

router = APIRouter()


@router.post("/mock/ensure")
async def mock_ensure(request: Request) -> dict[str, Any]:
    try:
        job_id = mock_mod.ensure_mock_job(
            PIPELINE_RUNNER,
            owner_id=current_owner_id(request),
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {"job_id": job_id, "pipeline_id": mock_mod.MOCK_PIPELINE_ID}
