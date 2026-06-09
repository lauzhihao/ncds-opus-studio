"""产物（artifact）路径 -> HTTP URL 的映射 + 按命令从 result 里提取产物清单。

动机：各 agent 的 run() 在 ``state/`` 或 ``video-jobs/`` 下落产物（脚本 .md、master.mp3、
storyboard.json、采集目录…），返回 dict 里带的是**绝对路径**。移动端要读稿/听音/看片，需要
把这些绝对路径换成可访问的 URL。这里集中做两件事，**不改任何 agent 代码**：

1. ``file_url() / dir_url()``：把仓库内合法路径换成 ``/artifacts/files/...`` / ``/artifacts/dir/...``，
   越界（不在 state/ 或 video-jobs/ 下）返回 None。
2. ``extract_artifacts(cmd, result)``：按每个命令的 result 形态，吐出 [{label, kind, url, path}]。

允许的根目录只有 ``state/`` 和 ``video-jobs/``（其余一律不暴露，防泄露源码/.env）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ALLOWED_ROOTS = ("state", "video-jobs")

# server/artifacts.py -> parents: [0]=server [1]=ncds_opus_factory [2]=src [3]=repo root
_DEFAULT_ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS_ROOT: Path = Path(os.environ.get("NOF_ARTIFACTS_ROOT", _DEFAULT_ROOT))

_KIND_BY_SUFFIX = {
    ".md": "script",
    ".txt": "text",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".mp4": "video",
    ".mov": "video",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".json": "data",
}


def kind_of(path: str | Path) -> str:
    return _KIND_BY_SUFFIX.get(Path(path).suffix.lower(), "file")


def validated_rel(path: str | Path) -> str | None:
    """把路径校验为「在 ARTIFACTS_ROOT 下且首段在白名单根」的仓库相对路径字符串。

    不要求文件存在（mp4 可能尚未渲染）；越界 / 非白名单根返回 None。
    """
    p = Path(path)
    if not p.is_absolute():
        p = ARTIFACTS_ROOT / p
    try:
        rel = p.resolve().relative_to(ARTIFACTS_ROOT.resolve())
    except ValueError:
        return None
    if not rel.parts or rel.parts[0] not in ALLOWED_ROOTS:
        return None
    return "/".join(rel.parts)


def file_url(path: str | Path) -> str | None:
    rel = validated_rel(path)
    return f"/artifacts/files/{rel}" if rel is not None else None


def dir_url(path: str | Path) -> str | None:
    rel = validated_rel(path)
    return f"/artifacts/dir/{rel}" if rel is not None else None


def _file(path: str | Path, label: str, kind: str | None = None) -> dict[str, Any] | None:
    url = file_url(path)
    if url is None:
        return None
    return {"label": label, "kind": kind or kind_of(path), "url": url, "path": str(path)}


def _dir(path: str | Path, label: str) -> dict[str, Any] | None:
    url = dir_url(path)
    if url is None:
        return None
    return {"label": label, "kind": "dir", "url": url, "path": str(path)}


def extract_artifacts(cmd: str, result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """按命令从 result 里提取可审看产物清单（[{label, kind, url, path}]）。

    只产出落在 state/ 或 video-jobs/ 下的产物；越界的静默丢弃。
    """
    if not isinstance(result, dict):
        return []
    out: list[dict[str, Any]] = []

    def add(item: dict[str, Any] | None) -> None:
        if item is not None:
            out.append(item)

    if cmd == "guiguzi":
        if result.get("out"):
            add(_file(result["out"], "选题库 topics.json"))

    elif cmd == "liuyong":
        for d in result.get("drafts") or []:
            p = d.get("path")
            if not p:
                continue
            model = d.get("model", "?")
            add(_file(p, f"脚本[{model}]", "script"))
            add(_file(Path(p).with_suffix(".qc.json"), f"质检[{model}]", "data"))
        if result.get("deliverables_dir"):
            add(_dir(result["deliverables_dir"], "产出目录"))

    elif cmd == "wudaozi":
        if result.get("storyboard_path"):
            add(_file(result["storyboard_path"], "storyboard.json"))
        od = result.get("out_dir")
        if od:
            add(_file(Path(od) / "beats.js", "beats.js"))
            # mp4 要再走 render 命令；这里给出预期 URL，未渲染则访问时 404
            add(_file(Path(od) / "output" / "figure_talk.mp4", "成片 mp4(需先 render)", "video"))
            add(_dir(od, "分镜实例目录"))

    elif cmd == "boya":
        job = result.get("job")
        if job:
            add(_file(Path(job) / "master.mp3", "master.mp3", "audio"))
            add(_file(Path(job) / "audio_plan.json", "audio_plan.json", "data"))

    elif cmd == "shenkuo":
        if result.get("author_dir"):
            add(_dir(result["author_dir"], "采集目录"))

    elif cmd == "wolong":
        if result.get("review_dir"):
            add(_dir(result["review_dir"], "待验收清单"))

    return out
