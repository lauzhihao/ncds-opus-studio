"""paper_card_talk_011 pipeline 声明。

DAG 形态：
    [URL 输入] → [asr] → [rw] → ┬→ [wst] ─┐
                                └→ [tts] ─┴→ [render] → [MP4 输出]

节点说明
--------
- input  : 用户在画布上贴抖音 URL（无后台任务，UI-only）
- asr    : 解析链接 + 下载 + ASR 听写 + 清洗校对 + 输出文章解析 + 精华提取
           （副作用：飞书通知。当前 command 还无静音开关，先接受）
- rw     : paper_card_talk profile 直出完整 episode.json（beats + scenes + overlays）
- wst    : 按 scenes[].prompt 批量生图，落 pictures/
- tts    : 按 beats[].zh 批量 TTS，落 audio/NNNN.mp3
- render : render_011 命令 → 1920x1080 30fps MP4
- output : 终态预览/下载卡（UI-only）
"""

from __future__ import annotations

from ncds_opus_factory.pipelines.types import NodePosition, PipelineDef, PipelineNode

PIPELINE = PipelineDef(
    id="paper_card_talk_011",
    name="Paper Card Talk · 011",
    description="抖音爆款 → 暖纸卡片口播 1920x1080 MP4",
    nodes=(
        PipelineNode(
            name="input",
            label="抖音链接",
            cmd="",
            deps=(),
            out_dir="00_input",
            description="贴一条抖音视频链接作为起点",
            position=NodePosition(0, 200),
            kind="input",
        ),
        PipelineNode(
            name="asr",
            label="听写 + 精华提取",
            cmd="asr",
            deps=("input",),
            out_dir="01_asr",
            description="解析链接 → 下载 → ASR 听写 → 清洗校对 → 输出文章解析 + 精华提取",
            position=NodePosition(280, 200),
        ),
        PipelineNode(
            name="rw",
            label="改写 + 拆场景",
            cmd="rw",
            deps=("asr",),
            out_dir="02_rw",
            description="paper_card_talk profile：直出含 beats + scenes + overlays 的完整 episode.json",
            position=NodePosition(560, 200),
        ),
        PipelineNode(
            name="wst",
            label="批量生图",
            cmd="wst",
            deps=("rw",),
            out_dir="03_wst",
            description="按 scenes[].prompt 逐条调用 gpt-image-2，落 pictures/",
            position=NodePosition(840, 80),
        ),
        PipelineNode(
            name="tts",
            label="批量 TTS",
            cmd="tts",
            deps=("rw",),
            out_dir="04_tts",
            description="按 beats[].zh 调 dashscope-cosyvoice，落 audio/NNNN.mp3",
            position=NodePosition(840, 320),
        ),
        PipelineNode(
            name="render",
            label="渲染 MP4",
            cmd="render_011",
            deps=("wst", "tts"),
            out_dir="05_render",
            description="puppeteer headless chrome 录屏 + ffmpeg 合音 → 011.mp4",
            position=NodePosition(1120, 200),
        ),
        PipelineNode(
            name="output",
            label="成品",
            cmd="",
            deps=("render",),
            out_dir="05_render",
            description="预览 + 下载 MP4",
            position=NodePosition(1400, 200),
            kind="output",
        ),
    ),
)
