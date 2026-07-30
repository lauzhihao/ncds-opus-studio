"""Pipeline registry：把每个模板对应的 5+节点 DAG 声明集中放这里。

每个 pipeline 是一份 `PipelineDef`，由 server.pipeline_runner 解释执行。
新加模板时往 PIPELINE_REGISTRY 里追加一行即可。
"""

from __future__ import annotations

from ncds_opus_core.pipelines.final_preview import (
    PIPELINE as FINAL_PREVIEW,
)
from ncds_opus_core.pipelines.types import (
    NodeStatus,
    PipelineDef,
    PipelineNode,
)

# 单模板：final_preview（scene 整段配音 + 字级时间戳 + 分镜简笔画）
PIPELINE_REGISTRY: dict[str, PipelineDef] = {
    FINAL_PREVIEW.id: FINAL_PREVIEW,
}


def get_pipeline(pipeline_id: str) -> PipelineDef:
    if pipeline_id not in PIPELINE_REGISTRY:
        raise KeyError(f"unknown pipeline_id: {pipeline_id}")
    return PIPELINE_REGISTRY[pipeline_id]


__all__ = [
    "PIPELINE_REGISTRY",
    "PipelineDef",
    "PipelineNode",
    "NodeStatus",
    "get_pipeline",
]
