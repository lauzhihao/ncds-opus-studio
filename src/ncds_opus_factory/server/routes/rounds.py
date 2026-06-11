"""round 查看与止损端点。

- GET  /rounds                  列出 round(最新在前,带阶段/产线摘要)
- GET  /rounds/{round_id}       round 全量状态(战报/产线/事件)
- POST /rounds/{round_id}/terminate  显式止损:终止本轮并清场(在途任务取消)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from ncds_opus_factory.server import rounds_gate

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/rounds")
async def list_rounds() -> list[dict[str, Any]]:
    out = []
    for f in sorted(rounds_gate.ROUNDS.base_dir.glob("round_*.json"), reverse=True):
        r = rounds_gate.ROUNDS.load(f.stem)
        if r is None:
            continue
        out.append({
            "round_id": r["round_id"],
            "status": r["status"],
            "stage": r.get("stage"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
            "goal_count": (r.get("goal") or {}).get("count"),
            "lines": [
                {"slot": ln.get("slot"), "status": ln.get("status"),
                 "title": str((ln.get("topic") or {}).get("title") or "")[:60],
                 "rework": ln.get("rework", 0)}
                for ln in r.get("lines", [])
            ],
            "report": r.get("report"),
        })
    return out


@router.get("/rounds/{round_id}")
async def get_round(round_id: str) -> dict[str, Any]:
    try:
        r = rounds_gate.ROUNDS.load(round_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"round not found: {round_id}")
    if r is None:
        raise HTTPException(status_code=404, detail=f"round not found: {round_id}")
    return r


@router.post("/rounds/{round_id}/terminate")
async def terminate_round(round_id: str) -> dict[str, Any]:
    try:
        r = await rounds_gate.terminate_round(round_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"round not found: {round_id}")
    if r is None:
        raise HTTPException(status_code=404, detail=f"round not found: {round_id}")
    logger.info("[server] round terminated by user: %s", round_id)
    return {"ok": True, "status": r["status"]}
