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

from ncds_opus_factory.commands import asr, render, render_015, rw, tst, tts, vid, wst
# 5 个中国风成片 agent + 卧龙(操盘手)；它们的 run() 同样遵守 run(...on_progress)->dict 契约，
# 接进 registry 即可被 server.task_runner 异步拉起、进度走 SSE（移动端控制全厂的入口）。
from ncds_opus_factory.commands import boya, guiguzi, liuyong, shenkuo, wolong, wudaozi

RunFn = Callable[..., dict[str, Any]]

COMMAND_REGISTRY: dict[str, RunFn] = {
    "wst": wst.run,
    "tst": tst.run,
    "vid": vid.run,
    "asr": asr.run,
    "rw": rw.run,
    "tts": tts.run,
    "render": render.run,
    "render_015": render_015.run,
    # 中国风 agent 层
    "guiguzi": guiguzi.run,  # 选题官
    "liuyong": liuyong.run,  # 编剧 + 质检
    "wudaozi": wudaozi.run,  # 美术/视觉(剪影分镜)
    "boya": boya.run,        # 声音(配音/配乐/音效)
    "shenkuo": shenkuo.run,  # 采集层(对标供料)
    "wolong": wolong.run,    # CEO/操盘手(opus 编排)
}


__all__ = ["COMMAND_REGISTRY", "RunFn"]
