"""命令注册表（factory 侧）。

二分（§9.4）：
- **core**：``PRIMITIVE_REGISTRY`` = wst/tst/vid/tts/render/render_015（纯能力，复用两端）。
- **factory**：``AGENT_REGISTRY`` = 6 中国风 agent + asr + rw（含飞书/COS/skills 编排，
  asr/rw 命令归 factory）。
- ``build_full_registry()`` 合并两者，喂给 server.task_runner 反射拉起每个 run（口径不变）。

签名约定（所有 command 一致）：
    def run(<参数>..., on_progress: ProgressFn = _noop, ...) -> dict[str, Any]
"""

from __future__ import annotations

from ncds_opus_core.commands.registry import PRIMITIVE_REGISTRY, RunFn

# asr/rw：factory 专属命令（studio 刻意绕开它们直接 spawn 底层引擎；这两个命令只被 cli +
# 任务系统用 = factory 关切，含飞书/COS/skills 编排）。
from ncds_opus_factory.commands import asr, rw
# 5 个中国风成片 agent + 卧龙(操盘手)；run() 同样遵守 run(...on_progress)->dict 契约。
from ncds_opus_factory.commands import boya, guiguzi, liuyong, shenkuo, wolong, wudaozi

# factory 专属命令表（asr/rw + agent 层）
AGENT_REGISTRY: dict[str, RunFn] = {
    "asr": asr.run,
    "rw": rw.run,
    "guiguzi": guiguzi.run,  # 选题官
    "liuyong": liuyong.run,  # 编剧 + 质检
    "wudaozi": wudaozi.run,  # 美术/视觉(剪影分镜)
    "boya": boya.run,        # 声音(配音/配乐/音效)
    "shenkuo": shenkuo.run,  # 采集层(对标供料)
    "wolong": wolong.run,    # CEO/操盘手(opus 编排)
}


def build_full_registry() -> dict[str, RunFn]:
    """core PRIMITIVE + factory AGENT 的合并全集，喂给 server.task_runner（口径不变）。"""
    return {**PRIMITIVE_REGISTRY, **AGENT_REGISTRY}


# 向后兼容别名（P5 清 shim 时删）：旧消费者仍可 import COMMAND_REGISTRY。
COMMAND_REGISTRY: dict[str, RunFn] = build_full_registry()


__all__ = [
    "AGENT_REGISTRY",
    "PRIMITIVE_REGISTRY",
    "build_full_registry",
    "COMMAND_REGISTRY",
    "RunFn",
]
