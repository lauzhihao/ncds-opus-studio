"""rewrite 引擎 runner 包（content_rewrite / video_rewrite / rewrite_profiles / douyin_cog_kernel）。

这些 .mjs runner 以 subprocess spawn 或跨包 ESM import 被消费；调用方用 runner_path() 定位包内脚本，
迁包后不再靠 parents[N] 从仓库根拼路径（仿 gpt_image/__init__.py:script_path）。

消费者（均在 factory，皆单向依赖 core）：
- `commands/liuyong.py`：spawn-by-path `content_rewrite_runner.mjs`（用本定位器）。
注：studio 的 rw 节点已改走内联 4 模型并行，不再 spawn 独立 rewrite runner。
"""

from pathlib import Path


def runner_path(name: str) -> Path:
    """返回本 runner 包内某 .mjs 脚本的绝对路径（供 subprocess spawn / 跨包定位）。"""
    return Path(__file__).resolve().parent / name
