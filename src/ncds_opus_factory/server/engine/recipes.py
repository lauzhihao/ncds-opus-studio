"""配方注册表（RECIPE_REGISTRY）。

把 final_preview DAG（ncds_opus_core.pipelines.final_preview）表达成一条 Recipe，
作为引擎的第一条配方。步骤的 ``cmd`` 是派发 registry 的 key（引擎晚绑定派发）。

E1-b2：各执行步的 ``cmd`` 已绑到 final_preview orchestration performer（``final_*``，见
``pipeline_performers_final.PERFORMERS_FINAL``），引擎在合并 registry（build_full_registry ∪
PERFORMERS_FINAL，见 ``server/state.py``）里查表派发——即真实复刻 web 各 ``_execute_*`` 编排。
``input``/``download``（无 cmd）与 ``preview``（content_edit 人工闸、无 performer）无执行体。
统一设计里 asr→沈括、rw→柳永 等 agent 重绑定在后续 E 期做（见设计 §3/§8）；figure_talk 等更多配方在 E3 入册。
"""

from __future__ import annotations

from ncds_opus_factory.server.engine.types import Recipe, RecipeStep

# final_preview：10 步线性 DAG。expensive=贵步骤（生图/tts/render）；
# intervention=该步产出可挂人工介入点（content_edit / decision_only）。
FINAL_PREVIEW = Recipe(
    recipe_id="final_preview",
    name="Final Preview",
    description="抖音爆款 → 成品预览模板 1920x1080 MP4（scene 整段配音 + 字级时间戳）",
    template_renderer="final_preview",
    steps=[
        RecipeStep(step_id="input", label="START", kind="input", deps=[]),
        RecipeStep(step_id="asr", label="ASR", cmd="final_asr", deps=["input"]),
        RecipeStep(step_id="rw", label="RW", cmd="final_rw", deps=["asr"],
                   intervention="content_edit"),
        RecipeStep(step_id="lines", label="BEATS", cmd="final_lines", deps=["rw"],
                   intervention="content_edit"),
        RecipeStep(step_id="storyboard", label="STORYBOARD", cmd="final_storyboard", deps=["lines"],
                   intervention="content_edit"),
        RecipeStep(step_id="image", label="IMAGE", cmd="final_image", deps=["storyboard"],
                   expensive=True, material_source="generated"),
        RecipeStep(step_id="tts", label="TTS", cmd="final_tts", deps=["image"],
                   expensive=True),
        RecipeStep(step_id="preview", label="PREVIEW", deps=["tts"],
                   intervention="content_edit"),
        RecipeStep(step_id="render", label="RENDER", cmd="final_render", deps=["preview"],
                   expensive=True),
        RecipeStep(step_id="download", label="DOWNLOAD", kind="output", deps=["render"]),
    ],
)


RECIPE_REGISTRY: dict[str, Recipe] = {
    FINAL_PREVIEW.recipe_id: FINAL_PREVIEW,
}


def get_recipe(recipe_id: str) -> Recipe:
    if recipe_id not in RECIPE_REGISTRY:
        raise KeyError(f"unknown recipe_id: {recipe_id}")
    return RECIPE_REGISTRY[recipe_id]


__all__ = ["RECIPE_REGISTRY", "FINAL_PREVIEW", "get_recipe"]
