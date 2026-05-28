"""paper_card_talk_015 pipeline 声明（v5 · 9 节点全串行）。

DAG 形态（线性 9 节点）：
    [input] → [asr] → [rw] → [lines] → [tts] → [image] → [preview] → [render] → [download]

节点说明
--------
- input    : 用户在画布上贴抖音 URL（无后台任务，UI-only）
- asr      : 解析链接 + 下载 + ASR 听写 + 清洗校对 + 输出文章解析 + 精华提取
- rw       : paper_card_talk profile 直出完整 episode.json（beats + scenes + overlays）
- lines    : 从 rw 出的 episode.json 抽出 beats 落成人类可读 lines.md（zh/en/scene），
             用户在抽屉里逐句编辑台词；下游 tts/image 都依赖文案敲定
- tts      : 按 beats[].zh 批量调 dashscope-cosyvoice 配音，落 audio/NNNN.mp3
- image    : 按 scenes[].prompt 批量调 wst（gpt-image-2）生图，落 pictures/；
             图片生成慢且贵，所以排在 tts 之后，让用户先确认台词再调 prompt 生图
- preview  : iframe 全屏 014 HTML（自带 edit-mode + Inspector + Tweaks 抽屉），
             用户拖拽/微调直接 fetch ./__save_* 端点回写 02_rw/episode.json
- render   : render_014 命令 → 1920x1080 30fps MP4
- download : UI-only 成品下载卡
"""

from __future__ import annotations

from ncds_opus_factory.pipelines.types import NodePosition, PipelineDef, PipelineNode

PIPELINE = PipelineDef(
    id="paper_card_talk_015",
    name="Paper Card Talk · 015",
    description="抖音爆款 → 暖纸卡片口播 1920x1080 MP4（015 模板，scene 整段配音 + 字级时间戳）",
    nodes=(
        PipelineNode(
            name="input",
            label="START",
            cmd="",
            deps=(),
            out_dir="00_input",
            description="给我参考链接，开始成大事。",
            position=NodePosition(0, 0),
            kind="input",
        ),
        PipelineNode(
            name="asr",
            label="ASR",
            cmd="asr",
            deps=("input",),
            out_dir="01_asr",
            description="高精度语音识别转写",
            position=NodePosition(0, 140),
        ),
        PipelineNode(
            name="rw",
            label="RW",
            cmd="rw",
            deps=("asr",),
            out_dir="02_rw",
            description="多模型多风格改稿",
            position=NodePosition(0, 280),
        ),
        PipelineNode(
            name="lines",
            label="BEATS",
            cmd="",
            deps=("rw",),
            out_dir="03_lines",
            description="智能切分字幕",
            position=NodePosition(0, 420),
        ),
        PipelineNode(
            name="tts",
            label="TTS",
            cmd="tts",
            deps=("lines",),
            out_dir="04_tts",
            description="高情感度多音色文字合成语音",
            position=NodePosition(0, 560),
        ),
        PipelineNode(
            name="image",
            label="IMAGE",
            cmd="wst",
            deps=("tts",),
            out_dir="03_image",
            description="GPT-IMAGE-2 文/图生图",
            position=NodePosition(0, 700),
        ),
        PipelineNode(
            name="preview",
            label="PREVIEW",
            cmd="",
            deps=("image",),
            out_dir="05_preview",
            description="预览与微调",
            position=NodePosition(0, 840),
        ),
        PipelineNode(
            name="render",
            label="RENDER",
            cmd="render_015",
            deps=("preview",),
            out_dir="06_render",
            description="64x极速渲染1080P视频文件",
            position=NodePosition(0, 980),
        ),
        PipelineNode(
            name="download",
            label="DOWNLOAD",
            cmd="",
            deps=("render",),
            out_dir="06_render",
            description="预览成片与下载",
            position=NodePosition(0, 1120),
            kind="output",
        ),
    ),
)
