"""factory 命令的声明式参数 schema（AGENT_SCHEMAS）+ 全集 get_schema。

不做强校验：``POST /tasks/{cmd}`` 仍把 ``params`` 原样 spread 给 ``run(**params)``，
保持 free-form 兼容。这里只是「字段说明书」，让 iPad / 手机 UI 知道每个命令该填什么。

二分（§9.4）：
- core ``PRIMITIVE_SCHEMAS`` = wst/tst/vid/tts/render/render_final_preview（在 ncds_opus_core）。
- factory ``AGENT_SCHEMAS`` = 6 中国风 agent + asr（本文件）。asr UI ``group``
  仍是 "primitive"，但命令归 factory，故 schema 在这边。
- ``get_schema()`` 合并 AGENT + PRIMITIVE 暴露全集（``/commands`` 不丢字段）。

字段 type 词表：
    string / text / int / float / bool / string[] / enum
（``_f`` helper 由 core 提供，单点维护。）
"""

from __future__ import annotations

from typing import Any

from ncds_opus_core.commands.schemas import PRIMITIVE_SCHEMAS, _f

# factory 专属命令 schema（agent 层 + asr）
AGENT_SCHEMAS: dict[str, dict[str, Any]] = {
    # ───────────────────────── 中国风 agent 层 ─────────────────────────
    "guiguzi": {
        "label": "鬼谷子 · 选题官",
        "group": "agent",
        "summary": "提取文案+评论为种子，双模型先反推爆款原因、再逐条锚定出选题（两步流）",
        "fields": [
            _f("items", "选题种子", "string[]", required=True,
               help="[{text:提取文案, comment:评论}, ...]（≤5，每条评论各出 1 选题）"),
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
    # task-3.7: wudaozi/boya 仍可通过 /tasks 派单,但当前只是旧 figure_talk 冷链入口；
    # web/engine 主链不经它们。后续重写下游时直接替换,不为旧实现加兼容层。
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
            _f("author", "作者 ID", "string", help="按作者批量拉（抖音 sec_uid / TK handle / 油管 channel；与单条 aweme 二选一）"),
            _f("aweme", "单条作品 ID/链接", "string", help="只采单条（支持抖音/TK/油管）"),
            _f("platform", "平台", "enum", default="douyin", enum=["douyin", "tiktok", "youtube"]),
            _f("source_url", "原始作品 URL", "string", help="单条模式可选；TK 建议带原始链接"),
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
        "summary": "分段编排一轮生产:鬼谷子选题->柳永成稿->你验收->战报（验收驱动,自动推进）",
        "fields": [
            _f("count", "本轮产出条数", "int", default=3),
            _f("benchmark_path", "对标数据路径", "string", help="留空用脚本内置默认"),
            _f("avoid", "已发选题(逗号分隔)", "string", help="留空用脚本内置默认"),
        ],
    },
    # ───────────────────────── factory primitive(asr) ─────────────────────────
    "asr": {
        "label": "转写", "group": "primitive", "summary": "本地媒体下载 + ASR 转写",
        "fields": [_f("text", "输入", "text", required=True)],
    },
}


# 向后兼容别名（P5 清）：全集 = factory AGENT + core PRIMITIVE。
COMMAND_SCHEMAS: dict[str, dict[str, Any]] = {**PRIMITIVE_SCHEMAS, **AGENT_SCHEMAS}


def get_schema(cmd: str) -> dict[str, Any] | None:
    """返回某 command 的参数 schema（全集：先 factory AGENT，后 core PRIMITIVE）；未登记 None。"""
    return AGENT_SCHEMAS.get(cmd) or PRIMITIVE_SCHEMAS.get(cmd)
