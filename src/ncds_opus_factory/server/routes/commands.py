"""命令（command）资源 —— 与「任务实例」(task) 分开的 catalog/定义资源。

REST 拆分动机：命令是"能做什么"的定义（catalog），任务是命令的一次执行（instance）。
二者是不同资源，所以命令走 /commands、任务走 /tasks，URL 标识的资源与 HTTP method 无关。

端点
----
GET /commands               列出所有可执行命令（含 label/group/summary，给移动端渲染选择器）
GET /commands/{cmd}/schema  某命令的参数表单 schema（给移动端渲染输入表单）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ncds_opus_factory.server.command_schemas import get_schema
from ncds_opus_factory.server.state import RUNNER

router = APIRouter()

INTERNAL_COMMANDS = {"pipeline_node"}


@router.get("/commands")
async def list_commands() -> dict[str, list[dict[str, Any]]]:
    """列出当前注册的所有命令（catalog）。

    每条带 name/label/group/summary；未登记 schema 的命令回退到 name-only。
    """
    items: list[dict[str, Any]] = []
    for name in RUNNER.list_commands():
        if name in INTERNAL_COMMANDS:
            continue
        schema = get_schema(name)
        if schema is None:
            items.append({"name": name, "label": name, "group": "primitive", "summary": ""})
        else:
            items.append({
                "name": name,
                "label": schema.get("label", name),
                "group": schema.get("group", "primitive"),
                "summary": schema.get("summary", ""),
            })
    return {"commands": items}


@router.get("/commands/{cmd}/schema")
async def get_command_schema(cmd: str) -> dict[str, Any]:
    """返回某命令的参数 schema（给移动端渲染输入表单）。"""
    if cmd in INTERNAL_COMMANDS or cmd not in RUNNER.registry:
        raise HTTPException(
            status_code=404,
            detail=f"unknown command: {cmd}. available: {RUNNER.list_commands()}",
        )
    schema = get_schema(cmd)
    if schema is None:
        # 已注册但未登记 schema：返回空字段，前端可回退到原始 JSON 输入
        return {"cmd": cmd, "label": cmd, "group": "primitive", "summary": "", "fields": []}
    return {"cmd": cmd, **schema}
