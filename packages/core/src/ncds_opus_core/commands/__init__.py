"""ncds-opus-core primitive 命令（文生图 / 图生图 / 视频 / 转写 / 改写 / 配音 / 两套渲染）。

这些是两端复用的底层能力；agent 编排留在 ncds_opus_factory.commands。
`PRIMITIVE_REGISTRY`（6 个：wst/tst/vid/tts/render/render_final_preview）在 registry.py，由生产引擎
经 `build_full_registry()` 晚绑定派发（见 docs/PRODUCTION-ENGINE-DESIGN.md §1）。
"""
