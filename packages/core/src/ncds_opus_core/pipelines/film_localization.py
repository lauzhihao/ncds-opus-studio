"""film_localization pipeline declaration.

The recipe is deliberately limited to localization of an uploaded, authorised
source video.  It contains no watermark removal, DRM handling, or similarity
evasion step.
"""

from __future__ import annotations

from ncds_opus_core.pipelines.types import (
    NodePosition,
    PipelineDef,
    PipelineNode,
)

PIPELINE = PipelineDef(
    id="film_localization",
    name="Film Localization",
    description="Authorised Chinese source video -> English voiceover, bilingual subtitles, vertical MP4",
    nodes=(
        PipelineNode(
            name="input", label="SOURCE", cmd="", deps=(), out_dir="00_source",
            description="Upload one authorised source video.", position=NodePosition(0, 0), kind="input",
        ),
        PipelineNode(
            name="analyze", label="ANALYZE", cmd="film_analyze", deps=("input",), out_dir="01_analysis",
            description="Probe the source and produce a Chinese transcript timeline.", position=NodePosition(0, 150),
        ),
        PipelineNode(
            name="localize", label="LOCALIZE", cmd="film_localize", deps=("analyze",), out_dir="02_localize",
            description="Localize the Chinese transcript into an editable English script and bilingual subtitles.",
            position=NodePosition(0, 300),
        ),
        PipelineNode(
            name="voice", label="VOICE", cmd="film_voice", deps=("localize",), out_dir="03_voice",
            description="Generate English voiceover using the configured TTS provider.", position=NodePosition(0, 450),
        ),
        PipelineNode(
            name="render", label="RENDER", cmd="film_render", deps=("voice",), out_dir="04_render",
            description="Render a 1080x1920 source-video localization with bilingual burned-in subtitles.",
            position=NodePosition(0, 600),
        ),
        PipelineNode(
            name="download", label="DOWNLOAD", cmd="", deps=("render",), out_dir="04_render",
            description="Download the rendered MP4.", position=NodePosition(0, 750), kind="output",
        ),
    ),
)
