"""仓库根定位 + 路径工具（monorepo 拆分的统一改法）。

迁包后文件深度变化，**不再靠各文件数 `parents[N]`**：定位仓库根统一用 `repo_root()`
向上找标记；运行时产物根（state/ video-jobs/）一律以 `NOF_*_DIR` env 为准，import 期
default 仅作本地裸跑兜底。包内资源（templates 等）改用 `importlib.resources`，不从仓库根重拼。

用于替代散落的 parents[N] 路径推导。
"""

from __future__ import annotations

from pathlib import Path

# 仓库根标记：.git（开发态）或带 [tool.uv.workspace] 的根 pyproject.toml
_WORKSPACE_MARKER = "[tool.uv.workspace]"


def repo_root(start: Path | str | None = None) -> Path:
    """从 ``start``（默认本文件）向上找 monorepo 仓库根。

    判据：目录含 ``.git``，或含带 ``[tool.uv.workspace]`` 的根 ``pyproject.toml``。
    找不到（如装在无源码环境）时返回最顶层父目录兜底——**生产环境应改用 NOF_*_DIR
    等 env 定位产物根，不依赖本函数**（见模块 docstring）。
    """
    here = Path(start).resolve() if start is not None else Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / ".git").exists():
            return p
        pyproject = p / "pyproject.toml"
        if pyproject.is_file():
            try:
                if _WORKSPACE_MARKER in pyproject.read_text(encoding="utf-8"):
                    return p
            except OSError:
                pass
    return here.parents[-1]
