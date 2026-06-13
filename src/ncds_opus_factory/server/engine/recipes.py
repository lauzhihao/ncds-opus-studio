"""配方注册表（RECIPE_REGISTRY）。

E0：把现有 015 DAG（ncds_opus_core.pipelines.paper_card_talk_015）表达成一条 Recipe，
作为引擎的第一条配方。步骤的 ``cmd`` 是 build_full_registry() 的 key（引擎晚绑定派发）；
``input``/``process``(无 cmd)/``output`` 步无执行体（由 driver 或 UI 处理）。

注：E0 暂沿用 015 现有 cmd 绑定（asr/rw/tts/wst/render_015）；统一设计里
asr→沈括、rw→柳永 等 agent 重绑定在后续 E 期做（见设计 §3/§8），不影响骨架。
figure_talk 等更多配方在 E3 入册。
"""

from __future__ import annotations

from ncds_opus_factory.server.engine.types import Recipe, RecipeStep

# 015 纸卡口播：10 步线性 DAG。expensive=贵步骤（生图/tts/render）；
# intervention=该步产出可挂人工介入点（content_edit / decision_only）。
PAPER_CARD_TALK_015 = Recipe(
    recipe_id="paper_card_talk_015",
    name="Paper Card Talk · 015",
    description="抖音爆款 → 暖纸卡片口播 1920x1080 MP4（scene 整段配音 + 字级时间戳）",
    template_renderer="paper_card_talk_015",
    steps=[
        RecipeStep(step_id="input", label="START", kind="input", deps=[]),
        RecipeStep(step_id="asr", label="ASR", cmd="asr", deps=["input"]),
        RecipeStep(step_id="rw", label="RW", cmd="rw", deps=["asr"],
                   intervention="content_edit"),
        RecipeStep(step_id="lines", label="BEATS", deps=["rw"],
                   intervention="content_edit"),
        RecipeStep(step_id="storyboard", label="STORYBOARD", deps=["lines"],
                   intervention="content_edit"),
        RecipeStep(step_id="tts", label="TTS", cmd="tts", deps=["storyboard"],
                   expensive=True),
        RecipeStep(step_id="image", label="IMAGE", cmd="wst", deps=["tts"],
                   expensive=True, material_source="generated"),
        RecipeStep(step_id="preview", label="PREVIEW", deps=["image"],
                   intervention="content_edit"),
        RecipeStep(step_id="render", label="RENDER", cmd="render_015", deps=["preview"],
                   expensive=True),
        RecipeStep(step_id="download", label="DOWNLOAD", kind="output", deps=["render"]),
    ],
)


RECIPE_REGISTRY: dict[str, Recipe] = {
    PAPER_CARD_TALK_015.recipe_id: PAPER_CARD_TALK_015,
}


def get_recipe(recipe_id: str) -> Recipe:
    if recipe_id not in RECIPE_REGISTRY:
        raise KeyError(f"unknown recipe_id: {recipe_id}")
    return RECIPE_REGISTRY[recipe_id]


__all__ = ["RECIPE_REGISTRY", "PAPER_CARD_TALK_015", "get_recipe"]
