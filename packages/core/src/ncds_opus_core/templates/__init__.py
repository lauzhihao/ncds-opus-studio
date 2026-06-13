"""ncds-opus-core 自带模板。

当前仅 ``paper_card_talk_015``——被 render_015 复制到 workdir 渲染，并由 studio 侧
（pipeline_runner / preview / templates 路由）引用。其它模板（009 / figure_talk /
stickman / reading_confidence）留在 ncds_opus_factory.templates。

定位用 template_dir()/templates_root()，迁包后不再靠调用方 parents[N] 拼路径。
"""

from pathlib import Path


def templates_root() -> Path:
    """core 模板根目录（含 paper_card_talk_015 等）。"""
    return Path(__file__).resolve().parent


def template_dir(name: str) -> Path:
    """返回 core 内某模板目录的绝对路径。"""
    return templates_root() / name
