"""Pipeline registry：把每个模板对应的 5+节点 DAG 声明集中放这里。

每个 pipeline 是一份 `PipelineDef`，由 server.pipeline_runner 解释执行。
新加模板时往 PIPELINE_REGISTRY 里追加一行即可。
"""

from __future__ import annotations

from ncds_opus_factory.pipelines.paper_card_talk_014 import (
    PIPELINE as PAPER_CARD_TALK_014,
)
from ncds_opus_factory.pipelines.paper_card_talk_015 import (
    PIPELINE as PAPER_CARD_TALK_015,
)
from ncds_opus_factory.pipelines.types import (
    NodeStatus,
    PipelineDef,
    PipelineNode,
)

# 015 排在前面 = 模板中心默认推荐（scene 整段配音 + 字级时间戳）
PIPELINE_REGISTRY: dict[str, PipelineDef] = {
    PAPER_CARD_TALK_015.id: PAPER_CARD_TALK_015,
    PAPER_CARD_TALK_014.id: PAPER_CARD_TALK_014,
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
