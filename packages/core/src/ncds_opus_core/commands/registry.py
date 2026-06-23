"""核心 primitive 命令注册表（PRIMITIVE_REGISTRY）。

归属（§9.4）：core 严格 **6 个 primitive** —— wst/tst/vid/tts/render/render_final_preview。
asr 是 **factory** 命令（不在此）。factory 的 ``build_full_registry()`` 把本表
与 AGENT_REGISTRY 合并后喂给 server 的 TaskRunner（口径不变）。

签名约定（所有 command 一致）：
    def run(<参数>..., on_progress: ProgressFn = _noop, ...) -> dict[str, Any]
"""

from __future__ import annotations

from typing import Any, Callable

from ncds_opus_core.commands import render, render_final_preview, tst, tts, vid, wst

RunFn = Callable[..., dict[str, Any]]

PRIMITIVE_REGISTRY: dict[str, RunFn] = {
    "wst": wst.run,
    "tst": tst.run,
    "vid": vid.run,
    "tts": tts.run,
    "render": render.run,
    "render_final_preview": render_final_preview.run,
}


__all__ = ["PRIMITIVE_REGISTRY", "RunFn"]
