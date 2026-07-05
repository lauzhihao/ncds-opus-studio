"""采集底层能力（capabilities）—— 可复用原语，供沈括等上层「自由组合 + 缓存」。

把"困在沈括 agent 里"的媒体处理能力抽出来，配上 tikhub_client 的下载/评论原语，
形成一个统一的「这是哪个能力该用哪个实现」的 import 面：

    from ncds_opus_factory.common import capabilities
    txt = capabilities.transcribe(video)          # 转写(Bcut 首选，听悟/Paraformer fallback)
    capabilities.separate_audio(video, out_dir)   # 抽原声 + Demucs 分离
    capabilities.extract_frames(video, out_dir)   # 静止帧检测
    capabilities.cutout(frames, out_dir)          # 裁舞台区 + 阈值抠图
    capabilities.fetch_and_download               # 下载(DouK sidecar -> yt-dlp -> TikHub 兜底)
    capabilities.fetch_video_url / download_video / download_cover  # 下载原语(真身 tikhub_client)
    capabilities.fetch_top_comments               # 评论(真身 tikhub_client)

边界说明：URL→文章管线（skills/video-pipeline + asr_service）是**另一条**链路（产出清洗成文章，
喂老画布 /jobs 与 final_preview 引擎链），不是沈括这套采集能力的实现，两者不要混用。
"""

from __future__ import annotations

from ._base import ProgressFn, noop
from .audio import separate_audio
from .comments import fetch_top_comments
from .cutout import crop_stage, cutout, rembg_cutout, threshold_cutout
from .download import download_cover, download_video, fetch_and_download, fetch_video_url
from .frames import extract_frames
from .transcribe import clean_transcript, read_dashscope_key, transcribe

__all__ = [
    "ProgressFn",
    "noop",
    "transcribe",
    "clean_transcript",
    "read_dashscope_key",
    "separate_audio",
    "extract_frames",
    "cutout",
    "crop_stage",
    "threshold_cutout",
    "rembg_cutout",
    "fetch_and_download",
    "fetch_video_url",
    "download_video",
    "download_cover",
    "fetch_top_comments",
]
