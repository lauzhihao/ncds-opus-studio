"""核心 primitive 命令的声明式参数 schema（PRIMITIVE_SCHEMAS）。

给移动端/前端渲染输入表单用，对齐各 commands/<name>.py 的 run() 签名；不做强校验
（``POST /tasks/{cmd}`` 仍原样 spread 给 ``run(**params)``）。

归属（§9.4）：core 严格 **6 个 primitive**（wst/tst/vid/tts/render/render_final_preview）。
asr 虽 UI ``group="primitive"`` 但命令归 **factory**，其 schema 不在此（在
``ncds_opus_factory.server.command_schemas`` 的 AGENT_SCHEMAS 里）。factory 的
AGENT_SCHEMAS 复用本模块的 ``_f`` helper，并由 factory ``get_schema()`` 合并
PRIMITIVE + AGENT 暴露全集给 ``/commands``。

字段 type 词表：
    string   单行文本
    text     多行文本（提示词 / 创作要求等）
    int      整数
    float    小数
    bool     开关
    string[] 字符串数组（逗号分隔或多输入框）
    enum     从 enum 列表里选
"""

from __future__ import annotations

from typing import Any


def _f(
    name: str,
    label: str,
    type: str = "string",
    *,
    required: bool = False,
    default: Any = None,
    enum: list[str] | None = None,
    help: str = "",
) -> dict[str, Any]:
    field: dict[str, Any] = {"name": name, "label": label, "type": type, "required": required}
    if default is not None:
        field["default"] = default
    if enum:
        field["enum"] = enum
    if help:
        field["help"] = help
    return field


# group 字段保留 UI 语义："primitive"(底层命令) —— 与包归属（core/factory）无关。
PRIMITIVE_SCHEMAS: dict[str, dict[str, Any]] = {
    "wst": {
        "label": "文生图", "group": "primitive", "summary": "gpt-image 文生图",
        "fields": [
            _f("prompt", "提示词", "text", required=True),
            _f("size", "尺寸", "string", default="3:4", enum=["3:4", "1:1", "4:3", "9:16", "16:9"]),
            _f("n", "数量", "int", default=4, help="一次生成几张（1-4）"),
        ],
    },
    "tst": {
        "label": "图生图", "group": "primitive", "summary": "gpt-image 参考图编辑",
        "fields": [
            _f("prompt", "提示词", "text", required=True),
            _f("reference_images", "参考图", "string[]", required=True, help="本地路径或 URL"),
            _f("size", "尺寸", "string", default="3:4", enum=["3:4", "1:1", "4:3", "9:16", "16:9"]),
        ],
    },
    "vid": {
        "label": "视频生成", "group": "primitive", "summary": "文/图生视频",
        "fields": [
            _f("prompt", "提示词", "text", required=True),
            _f("ref_image_urls", "参考图 URL", "string[]"),
            _f("duration", "时长(秒)", "int", default=5),
        ],
    },
    "tts": {
        "label": "配音", "group": "primitive", "summary": "CosyVoice 逐 beat 合成人声",
        "fields": [
            _f("beats", "逐句文本", "string[]", help="与 beats_path 二选一"),
            _f("beats_path", "beats 文件", "string", help="与逐句文本二选一"),
            _f("output_dir", "输出目录", "string", default="audio"),
            _f("voice", "音色", "string", default="longtian_v3"),
            _f("rate", "语速", "float", default=1.1),
        ],
    },
    "render": {
        "label": "合成成片", "group": "primitive", "summary": "puppeteer 录屏 + ffmpeg mux",
        "fields": [
            _f("html_url", "页面 URL", "string", required=True),
            _f("audio_dir", "人声目录", "string", required=True),
            _f("output_path", "输出 mp4 路径", "string", required=True),
        ],
    },
    "render_final_preview": {
        "label": "合成成片(final_preview)", "group": "primitive", "summary": "成品预览模板出片",
        "fields": [
            _f("episode_path", "episode.json 路径", "string", required=True),
            _f("audio_dir", "人声目录", "string", required=True),
            _f("output_path", "输出 mp4 路径", "string", required=True),
            _f("picture_dir", "配图目录", "string"),
        ],
    },
}


def get_primitive_schema(cmd: str) -> dict[str, Any] | None:
    """返回某 primitive 命令的参数 schema；未登记返回 None。"""
    return PRIMITIVE_SCHEMAS.get(cmd)
