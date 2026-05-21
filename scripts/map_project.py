#!/usr/bin/env python3
"""ncds-opus-factory 项目结构地图生成器。

产出 `.project_map`（项目根），供 agent 在进入会话时快速了解仓库结构。
被 `scripts/map_project_watchdog.py` 周期性调用，文件变化时自动重生成。

地图包含四块：
1. Commands —— 5 个命令的入口模块路径
2. Runtime —— scripts/*.mjs（Node runner/worker/adapter）和 pipelines/gpt_image 的 Python 入口
3. Skills —— skills/*/SKILL.md 的 name + description
4. Tree —— 折叠 node_modules/state/video-jobs/__pycache__ 等噪音目录后的目录树
"""

from __future__ import annotations

import datetime as _dt
import fnmatch
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = PROJECT_ROOT / ".project_map"

# 完全忽略的文件名 / 通配符
IGNORE_PATTERNS = [
    ".git", ".idea", ".vscode", ".DS_Store", ".worktrees",
    "node_modules", "__pycache__", ".pytest_cache", ".venv",
    ".egg-info", "dist", "build",
    "*.pyc", "*.pyo", "*.png", "*.jpg", "*.jpeg", "*.gif",
    "*.svg", "*.ico", "*.woff", "*.woff2", "*.ttf",
    "*.mp4", "*.mp3", "*.wav", "*.m4a",
    "*.sqlite", "*.xlsx", "*.log",
    "*.bak", "*.bak.*",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    ".project_map",
]

# 仅展示数量、不展开内部文件的目录（相对路径前缀匹配）
FORCE_COLLAPSE_PATHS = [
    "state",            # 任务产物（gitignored）
    "video-jobs",       # 视频任务数据（gitignored）
    "node_modules",
    "src/ncds_opus_factory.egg-info",
]

# 关心的文件扩展（其他扩展默认不显示在 tree 里，避免噪音）
TRACKED_EXTENSIONS = {
    ".py", ".mjs", ".js", ".ts", ".sh", ".bash",
    ".json", ".toml", ".yaml", ".yml",
    ".md", ".txt", ".plist", ".env.example",
}


def load_extra_ignores() -> list[str]:
    """读取 .gitignore / .mapignore，作为补充忽略规则。"""
    patterns: list[str] = []
    for fname in (".gitignore", ".mapignore"):
        p = PROJECT_ROOT / fname
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line.rstrip("/"))
        except OSError:
            pass
    return patterns


def is_ignored(path: Path, extra_patterns: list[str]) -> bool:
    name = path.name
    rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    for pat in IGNORE_PATTERNS:
        if fnmatch.fnmatch(name, pat):
            return True
        if pat in rel.split("/"):
            return True
    for pat in extra_patterns:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
            return True
    return False


def should_collapse(path: Path) -> bool:
    rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    for blocked in FORCE_COLLAPSE_PATHS:
        if rel == blocked or rel.startswith(blocked + "/"):
            return True
    return False


def _count_files(path: Path) -> int:
    total = 0
    try:
        for _ in path.rglob("*"):
            if _.is_file():
                total += 1
    except OSError:
        pass
    return total


def generate_tree(dir_path: Path, prefix: str, extra: list[str]) -> list[str]:
    out: list[str] = []
    try:
        items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except (PermissionError, OSError):
        return out

    visible: list[Path] = [p for p in items if not is_ignored(p, extra)]

    valid: list[tuple[Path, bool, int, list[str]]] = []
    for item in visible:
        if item.is_dir():
            if should_collapse(item):
                cnt = _count_files(item)
                valid.append((item, True, cnt, []))
            else:
                child = generate_tree(item, prefix + "    ", extra)
                if child or any(c.is_file() for c in item.iterdir() if not is_ignored(c, extra)):
                    valid.append((item, False, 0, child))
        else:
            if item.suffix in TRACKED_EXTENSIONS or item.name in {"Makefile", "Dockerfile"}:
                valid.append((item, False, 0, []))

    n = len(valid)
    for i, (item, collapsed, cnt, child) in enumerate(valid):
        last = i == n - 1
        connector = "`-- " if last else "|-- "
        if item.is_dir():
            if collapsed:
                out.append(f"{prefix}{connector}[D] {item.name}/  [collapsed: {cnt} files]")
            else:
                out.append(f"{prefix}{connector}[D] {item.name}/")
                ext = "    " if last else "|   "
                out.extend(generate_tree(item, prefix + ext, extra))
        else:
            out.append(f"{prefix}{connector}[F] {item.name}")
    return out


# ----- 摘要提取 -----

COMMANDS = [
    ("/wst", "文生图（gpt-image-2）", "src/ncds_opus_factory/commands/wst.py"),
    ("/tst", "图生图（gpt-image-2 edit）", "src/ncds_opus_factory/commands/tst.py"),
    ("/vid", "视频生成（DashScope HappyHorse）", "src/ncds_opus_factory/commands/vid.py"),
    ("/asr", "多链路并行转写 + 爆款精华分析", "src/ncds_opus_factory/commands/asr.py"),
    ("/rw",  "gpt-5.5 + gemini 双模型改写", "src/ncds_opus_factory/commands/rw.py"),
]


def extract_commands_summary() -> list[str]:
    lines = ["| 命令 | 用途 | 入口 | 状态 |", "|---|---|---|---|"]
    for cmd, desc, rel in COMMANDS:
        p = PROJECT_ROOT / rel
        status = "ok" if p.exists() else "MISSING"
        lines.append(f"| `{cmd}` | {desc} | `{rel}` | {status} |")
    return lines


def extract_runtime_summary() -> list[str]:
    """Node runner / worker / adapter + Python pipeline / gpt_image 网关。"""
    lines: list[str] = []

    scripts_dir = PROJECT_ROOT / "scripts"
    if scripts_dir.is_dir():
        roles: dict[str, list[str]] = {
            "runner": [], "worker": [], "adapter": [], "lib": [], "tool": [],
        }
        for f in sorted(scripts_dir.iterdir()):
            if not f.is_file():
                continue
            name = f.name
            if ".test." in name or name.startswith("."):
                continue
            if f.suffix not in {".mjs", ".js", ".py", ".sh"}:
                continue
            if "runner" in name:
                roles["runner"].append(name)
            elif "worker" in name:
                roles["worker"].append(name)
            elif "adapter" in name:
                roles["adapter"].append(name)
            elif "map_project" in name:
                roles["tool"].append(name)
            else:
                roles["lib"].append(name)
        labels = {
            "runner": "Runners (spawned by Python commands)",
            "worker": "Workers (long-running job processors)",
            "adapter": "Adapters (SDK / CLI wrappers)",
            "lib": "Shared libs",
            "tool": "Tooling",
        }
        for key, label in labels.items():
            if roles[key]:
                lines.append(f"- {label}: {', '.join(roles[key])}")

    pipelines_dir = PROJECT_ROOT / "pipelines"
    if pipelines_dir.is_dir():
        py_files = sorted(
            str(p.relative_to(PROJECT_ROOT))
            for p in pipelines_dir.rglob("*.py")
            if "__pycache__" not in p.parts
        )
        if py_files:
            lines.append("- Pipelines (Python): " + ", ".join(py_files))

    gpt_dir = PROJECT_ROOT / "gpt_image"
    if gpt_dir.is_dir():
        py_files = sorted(p.name for p in gpt_dir.glob("*.py"))
        if py_files:
            lines.append("- gpt-image gateway: " + ", ".join(py_files))

    common_dir = PROJECT_ROOT / "src" / "ncds_opus_factory" / "common"
    if common_dir.is_dir():
        py_files = sorted(p.name for p in common_dir.glob("*.py") if p.name != "__init__.py")
        if py_files:
            lines.append("- Common (Python): " + ", ".join(py_files))

    return lines or ["(no runtime entries found)"]


def extract_skills_summary() -> list[str]:
    skills_dir = PROJECT_ROOT / "skills"
    if not skills_dir.is_dir():
        return ["(skills/ directory not found)"]
    lines: list[str] = []
    for entry in sorted(skills_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".skill":
            lines.append(f"- `{entry.stem}` (legacy .skill file)")
            continue
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            lines.append(f"- `{entry.name}`: (read error)")
            continue
        m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        name = entry.name
        desc = ""
        if m:
            for line in m.group(1).splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip('"\'')
                elif line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip().strip('"\'')
        lines.append(f"- `{name}`: {desc}" if desc else f"- `{name}`")
    return lines or ["(no SKILL.md files found)"]


def extract_docs_summary() -> list[str]:
    docs_dir = PROJECT_ROOT / "docs"
    if not docs_dir.is_dir():
        return []
    docs = sorted(p.name for p in docs_dir.glob("*.md"))
    if not docs:
        return []
    return [f"- `docs/{name}`" for name in docs]


def main() -> int:
    print(f"[map] generating {OUTPUT_FILE.relative_to(PROJECT_ROOT)}...", file=sys.stderr)
    extra = load_extra_ignores()
    tree_lines = generate_tree(PROJECT_ROOT, "", extra)

    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections: list[str] = [
        "# ncds-opus-factory Project Map",
        f"> Generated: {now}",
        "> Auto-generated by scripts/map_project.py (do not edit by hand).",
        "",
        "## Commands (5 entry points)",
        "",
        *extract_commands_summary(),
        "",
        "## Runtime",
        "",
        *extract_runtime_summary(),
        "",
        "## Skills (skills/*/SKILL.md)",
        "",
        *extract_skills_summary(),
    ]

    docs_lines = extract_docs_summary()
    if docs_lines:
        sections.extend(["", "## Docs", ""])
        sections.extend(docs_lines)

    sections.extend(["", "## Directory Tree", "```text"])
    sections.extend(tree_lines)
    sections.append("```")
    sections.append("")

    OUTPUT_FILE.write_text("\n".join(sections), encoding="utf-8")
    print(f"[map] wrote {OUTPUT_FILE.relative_to(PROJECT_ROOT)} ({len(sections)} lines)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
