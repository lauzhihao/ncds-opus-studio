"""下载能力：TikHub 取播放地址 + 流式下载 + 封面。

真身在 ``common/tikhub_client``（TikHub API 客户端就是下载原语层）；这里 re-export，
让 capabilities 成为「采集底层能力」的统一 import 面。沈括等上层可直接调 tikhub_client，
也可走这里——两者是同一个函数对象。
"""

from __future__ import annotations

from ncds_opus_factory.common.tikhub_client import (
    download_cover,
    download_video,
    fetch_video_url,
)

__all__ = ["fetch_video_url", "download_video", "download_cover"]
