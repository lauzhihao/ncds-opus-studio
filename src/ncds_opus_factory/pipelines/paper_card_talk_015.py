"""paper_card_talk_015 pipeline 声明（v6 · 10 节点全串行）。

DAG 形态（线性 10 节点）：
    [input] → [asr] → [rw] → [lines] → [storyboard] → [tts] → [image] → [preview] → [render] → [download]

节点说明
--------
- input      : 用户在画布上贴抖音 URL（无后台任务，UI-only）
- asr        : 解析链接 + 下载 + ASR 听写 + 清洗校对 + 输出文章解析 + 精华提取
- rw         : 多模型多风格改写，只产口播稿件 draft.md（不碰画面）
- lines      : 调 opus 把 draft.md 结构化成 beats[]（≤18 字字幕切分），用户在抽屉逐句编辑
- storyboard : 「分镜」—— 独立 director agent（whisper-reel 心理学家+导演人格）读 beats，
               切分子场景 + 产出每个场景的容器图 prompt 与 1-6 幅简笔画设计（scenes{}）。
               必须排在 tts 之前：子场景切分定稿后，tts 才按最终 scene 切音频
- tts        : 按最终 beats[].scene 整段合成配音，落 04_tts/scene-<sid>.mp3 + 字级时间戳
- image      : 按 scenes[].prompt 出容器图 + 遍历 sketches 出白底简笔画（gpt-image-2），落 03_image/
- preview    : iframe 全屏 HTML（edit-mode + Inspector + Tweaks 抽屉），回写 02_rw/episode.json
- render     : render_015 命令 → 1920x1080 MP4
- download   : UI-only 成品下载卡
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
            name="storyboard",
            label="STORYBOARD",
            cmd="",
            deps=("lines",),
            out_dir="03b_storyboard",
            description="导演 agent 分镜：子场景 + 简笔画设计",
            position=NodePosition(0, 560),
        ),
        PipelineNode(
            name="tts",
            label="TTS",
            cmd="tts",
            deps=("storyboard",),
            out_dir="04_tts",
            description="高情感度多音色文字合成语音",
            position=NodePosition(0, 700),
        ),
        PipelineNode(
            name="image",
            label="IMAGE",
            cmd="wst",
            deps=("tts",),
            out_dir="03_image",
            description="GPT-IMAGE-2 文/图生图",
            position=NodePosition(0, 840),
        ),
        PipelineNode(
            name="preview",
            label="PREVIEW",
            cmd="",
            deps=("image",),
            out_dir="05_preview",
            description="预览与微调",
            position=NodePosition(0, 980),
        ),
        PipelineNode(
            name="render",
            label="RENDER",
            cmd="render_015",
            deps=("preview",),
            out_dir="06_render",
            description="64x极速渲染1080P视频文件",
            position=NodePosition(0, 1120),
        ),
        PipelineNode(
            name="download",
            label="DOWNLOAD",
            cmd="",
            deps=("render",),
            out_dir="06_render",
            description="预览成片与下载",
            position=NodePosition(0, 1260),
            kind="output",
        ),
    ),
)
