"""DEPRECATED 转发 shim（P1.4）：本模块已迁至 ncds_opus_core.commands.render_015。

保留转发以不破坏 ncds_opus_factory.* 的现有 import；P5 清理时删除。
"""

import sys as _sys

from ncds_opus_core.commands import render_015 as _mod

_sys.modules[__name__] = _mod
