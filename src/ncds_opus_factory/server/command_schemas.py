"""每个 command 的声明式参数 schema —— 给移动端/前端渲染输入表单用。

不做强校验：``POST /tasks/{cmd}`` 仍把 ``params`` 原样 spread 给 ``run(**params)``，
保持 free-form 兼容。这里只是「字段说明书」，让 iPad / 手机 UI 知道每个命令该填什么、
哪些必填、默认值是什么、是不是枚举。字段定义对齐各 commands/*.py 的 run() 签名。

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


# group: "agent"(中国风 agent 层) / "primitive"(底层命令)
COMMAND_SCHEMAS: dict[str, dict[str, Any]] = {
    # ───────────────────────── 中国风 agent 层 ─────────────────────────
    "guiguzi": {
        "label": "鬼谷子 · 选题官",
        "group": "agent",
        "summary": "读对标爆款数据，提炼母题，迁移成本赛道产出新选题",
        "fields": [
            _f("benchmark_path", "对标数据路径", "string", required=True,
               help="all_posts.json 路径（沈括采集产出）"),
            _f("niche", "目标赛道", "string", default="职场/认知/成长"),
            _f("avoid", "避开的已发选题", "string[]", help="防撞题"),
            _f("top_n", "产出选题数", "int", default=10),
            _f("category", "对标分类筛选", "enum", default="growth",
               enum=["growth", "beauty", "other"]),
        ],
    },
    "liuyong": {
        "label": "柳永 · 编剧+质检",
        "group": "agent",
        "summary": "选题 -> 成稿 -> AI 味自检 -> 打回重写 -> opus 他评 rubric",
        "fields": [
            _f("topic", "选题", "string", required=True, help="一句话想法"),
            _f("user_requirements", "附加创作要求", "text"),
            _f("deliverables_dir", "产出目录", "string", help="留空自动建 video-jobs/OGV_*"),
        ],
    },
    "wudaozi": {
        "label": "吴道子 · 美术/视觉",
        "group": "agent",
        "summary": "把柳永脚本分镜成剪影 storyboard（mp4 再走 render 命令）",
        "fields": [
            _f("script_path", "脚本文件路径", "string", help="柳永稿 .md（与脚本文本二选一）"),
            _f("script_text", "脚本文本", "text", help="直接贴脚本（与脚本路径二选一）"),
            _f("library_dir", "剪影库目录", "string", help="留空用默认 assets/figure_lib"),
            _f("out_dir", "输出实例目录", "string", help="留空自动建 state/figure_jobs/WDZ_*"),
        ],
    },
    "boya": {
        "label": "伯牙 · 声音",
        "group": "agent",
        "summary": "配音(TTS)+配乐(BGM)+音效(SFX)混成 master.mp3",
        "fields": [
            _f("job_dir", "成片 job 目录", "string", required=True,
               help="含 audio/ 人声或 beats 的目录"),
            _f("library_dir", "音源库目录", "string", help="留空用默认 assets/audio_lib"),
            _f("scene", "场景", "string", help="留空自动推断"),
            _f("mood", "情绪", "string"),
            _f("bgm_volume_db", "BGM 音量(dB)", "float", default=-18.0),
            _f("voice", "音色", "string", default="longtian_v3"),
            _f("rate", "语速", "float", default=1.1),
            _f("regen_voice", "强制重生人声", "bool", default=False),
        ],
    },
    "shenkuo": {
        "label": "沈括 · 采集层",
        "group": "agent",
        "summary": "按作者拉对标作品 + 转写 + 截帧 + 抠图（最上游供料）",
        "fields": [
            _f("author", "作者 sec_uid", "string", help="按作者批量拉（与单条 aweme 二选一）"),
            _f("aweme", "单条 aweme_id", "string", help="只采单条（验证用）"),
            _f("top", "拉取作品数", "int", default=10),
            _f("max_frames", "每条截帧数", "int", default=8),
            _f("engine", "抠图引擎", "enum", default="threshold",
               enum=["threshold", "rembg"]),
            _f("top_comments", "采集评论数", "int", default=20),
            _f("refresh_only", "只刷新列表不下载", "bool", default=False),
        ],
    },
    "wolong": {
        "label": "卧龙 · 操盘手(CEO)",
        "group": "agent",
        "summary": "opus 自主编排 鬼谷子->柳永->待验收清单（重任务，可能数分钟）",
        "fields": [
            _f("count", "本轮产出条数", "int", default=3),
            _f("benchmark_path", "对标数据路径", "string", help="留空用脚本内置默认"),
            _f("avoid", "已发选题(逗号分隔)", "string", help="留空用脚本内置默认"),
        ],
    },
    # ───────────────────────── 底层命令 ─────────────────────────
    "wst": {
        "label": "文生图", "group": "primitive", "summary": "gpt-image 文生图",
        "fields": [_f("prompt", "提示词", "text", required=True)],
    },
    "tst": {
        "label": "图生图", "group": "primitive", "summary": "gpt-image 参考图编辑",
        "fields": [
            _f("prompt", "提示词", "text", required=True),
            _f("reference_images", "参考图", "string[]", required=True, help="本地路径或 URL"),
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
    "asr": {
        "label": "转写", "group": "primitive", "summary": "多链路转写",
        "fields": [_f("text", "输入", "text", required=True)],
    },
    "rw": {
        "label": "改写", "group": "primitive", "summary": "双模型改写",
        "fields": [_f("docx_url", "文档 URL", "string", required=True)],
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
    "render_015": {
        "label": "合成成片(015)", "group": "primitive", "summary": "015 纸卡模板成片",
        "fields": [
            _f("episode_path", "episode.json 路径", "string", required=True),
            _f("audio_dir", "人声目录", "string", required=True),
            _f("output_path", "输出 mp4 路径", "string", required=True),
            _f("picture_dir", "配图目录", "string"),
        ],
    },
}


def get_schema(cmd: str) -> dict[str, Any] | None:
    """返回某 command 的参数 schema；未登记返回 None。"""
    return COMMAND_SCHEMAS.get(cmd)
