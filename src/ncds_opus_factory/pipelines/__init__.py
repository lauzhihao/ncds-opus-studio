"""DEPRECATED 转发 shim（P1.5）：本包已迁至 ncds_opus_core.pipelines。

保留转发以不破坏 ncds_opus_factory.pipelines 的现有 import（package 级
`from ncds_opus_factory.pipelines import ...`）；P5 清理 shim 时删除。
"""

import sys as _sys

from ncds_opus_core import pipelines as _mod

_sys.modules[__name__] = _mod
