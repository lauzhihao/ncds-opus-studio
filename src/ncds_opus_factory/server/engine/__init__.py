"""生产引擎（production engine）—— 统一"生产实例 + 步骤"运行时。

设计：[docs/PRODUCTION-ENGINE-DESIGN.md](../../../../docs/PRODUCTION-ENGINE-DESIGN.md)。
一个生产实例 = 一条 recipe 的一次执行（有序步骤），每步可挂草稿 + 人工介入点。
引擎只负责"执行一步 + 维护实例/步骤状态 + 推事件"；谁决定下一步跑什么 = driver（web 手动 / 卧龙自治）。
靠 build_full_registry() 晚绑定派发，不直接 import 任何 agent。

E0：骨架（types + store + runner + recipes），先不接任何视图。
"""

from ncds_opus_factory.server.engine.types import (
    InstanceEvent,
    InstanceMeta,
    InstanceState,
    InstanceStatus,
    Recipe,
    RecipeStep,
    StepState,
    StepStatus,
    can_transition,
)

__all__ = [
    "InstanceEvent",
    "InstanceMeta",
    "InstanceState",
    "InstanceStatus",
    "Recipe",
    "RecipeStep",
    "StepState",
    "StepStatus",
    "can_transition",
]
