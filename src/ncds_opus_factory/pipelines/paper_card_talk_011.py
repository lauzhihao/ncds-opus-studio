"""paper_card_talk_011 pipeline 声明（v3 · 8 节点全串行）。

DAG 形态（线性 8 节点）：
    [input] → [asr] → [rw] → [image] → [tts] → [preview] → [render] → [download]

节点说明
--------
- input    : 用户在画布上贴抖音 URL（无后台任务，UI-only）
- asr      : 解析链接 + 下载 + ASR 听写 + 清洗校对 + 输出文章解析 + 精华提取
- rw       : paper_card_talk profile 直出完整 episode.json（beats + scenes + overlays）
- image    : 按 scenes[].prompt 批量调 wst（gpt-image-2）生图，落 pictures/
- tts      : 按 beats[].zh 批量调 dashscope-cosyvoice 配音，落 audio/NNNN.mp3
- preview  : iframe 预览 011 HTML 渲染 + 微调 episode.json（用户审核入口）
- render   : render_011 命令 → 1920x1080 30fps MP4
- download : UI-only 成品下载卡
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
            description="贴一条抖音视频链接作为起点。",
            position=NodePosition(0, 0),
            kind="input",
        ),
        PipelineNode(
            name="asr",
            label="ASR",
            cmd="asr",
            deps=("input",),
            out_dir="01_asr",
            description="解析链接 → 下载 → ASR 听写 → 清洗校对 → 输出文章解析 + 精华提取",
            position=NodePosition(0, 140),
        ),
        PipelineNode(
            name="rw",
            label="RW",
            cmd="rw",
            deps=("asr",),
            out_dir="02_rw",
            description="paper_card_talk profile：直出含 beats + scenes + overlays 的完整 episode.json",
            position=NodePosition(0, 280),
        ),
        PipelineNode(
            name="image",
            label="IMAGE",
            cmd="wst",
            deps=("rw",),
            out_dir="03_image",
            description="按 scenes[].prompt 批量调用 gpt-image-2 生图，落 pictures/",
            position=NodePosition(0, 420),
        ),
        PipelineNode(
            name="tts",
            label="TTS",
            cmd="tts",
            deps=("image",),
            out_dir="04_tts",
            description="按 beats[].zh 批量调 dashscope-cosyvoice 配音，落 audio/NNNN.mp3",
            position=NodePosition(0, 560),
        ),
        PipelineNode(
            name="preview",
            label="PREVIEW",
            cmd="",
            deps=("tts",),
            out_dir="05_preview",
            description="iframe 实时预览 011 模板渲染效果 + 微调 episode（字幕/字体/动效/插槽）",
            position=NodePosition(0, 700),
        ),
        PipelineNode(
            name="render",
            label="RENDER",
            cmd="render_011",
            deps=("preview",),
            out_dir="06_render",
            description="puppeteer headless chrome 录屏 + ffmpeg 合音 → 011.mp4",
            position=NodePosition(0, 840),
        ),
        PipelineNode(
            name="download",
            label="DOWNLOAD",
            cmd="",
            deps=("render",),
            out_dir="06_render",
            description="预览成品 + 下载 MP4",
            position=NodePosition(0, 980),
            kind="output",
        ),
    ),
)
