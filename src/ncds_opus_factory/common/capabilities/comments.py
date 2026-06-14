"""评论能力：TikHub 取作品高赞评论。

真身在 ``common/tikhub_client``；这里 re-export，让 capabilities 成为统一 import 面。
"""

from __future__ import annotations

from ncds_opus_factory.common.tikhub_client import fetch_top_comments

__all__ = ["fetch_top_comments"]
