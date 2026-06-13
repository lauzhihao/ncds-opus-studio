"""DEPRECATED 转发 shim（P1）：本模块已迁至 ncds_opus_core.common.public_upload。

保留转发以不破坏 ncds_opus_factory.* 的现有 import；P5 清理 shim 时删除。
"""

import sys as _sys

from ncds_opus_core.common import public_upload as _mod

_sys.modules[__name__] = _mod
