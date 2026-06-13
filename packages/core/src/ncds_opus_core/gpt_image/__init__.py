"""gpt-image-2 网关脚本包（generate / generate_edit + 底层 gpt_image_gen/edit）。

这些脚本以 subprocess 方式被调用（非 import）；调用方用 script_path() 定位包内脚本，
迁包后不再靠 parents[N] 从仓库根拼路径。
"""

from pathlib import Path


def script_path(name: str) -> Path:
    """返回本网关包内某脚本的绝对路径（供 subprocess 调用）。"""
    return Path(__file__).resolve().parent / name
