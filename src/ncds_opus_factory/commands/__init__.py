"""命令注册表。

server.task_runner 通过 COMMAND_REGISTRY 反射调用每个 command 的 run 函数。
新加命令时只需在这里追加一行（同时实现 commands/<name>.py 里的 run）。

签名约定（所有 command 一致）：
    def run(<参数>..., on_progress: ProgressFn = _noop, ...) -> dict[str, Any]

tts / render 占位：Phase 1 后续步骤会补上对应的 commands/tts.py 与
commands/render.py（重构自 templates/paper_card_talk/tts_gen.py 与 render.mjs）。
"""

from __future__ import annotations

from typing import Any, Callable

from ncds_opus_factory.commands import asr, rw, tst, vid, wst

RunFn = Callable[..., dict[str, Any]]

COMMAND_REGISTRY: dict[str, RunFn] = {
    "wst": wst.run,
    "tst": tst.run,
    "vid": vid.run,
    "asr": asr.run,
    "rw": rw.run,
}


__all__ = ["COMMAND_REGISTRY", "RunFn"]
