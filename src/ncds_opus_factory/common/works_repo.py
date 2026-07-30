"""作品仓库 —— 按 (platform, aweme_id) 内容寻址的统一产物存储 + manifest。

设计：把"同一作品只采一次"做成全局事实，而不再绑定到某个对标号目录。
任何入口（沈括 author 批采 / adhoc 单采 / works.py 智能解析 / 引擎链路）
落到同一 (platform, aweme_id) 就命中同一份产物，不重复下载/转写/截帧/抠图。

    state/works/{platform}/{aweme_id}/
      manifest.json   # 作品卡(card,works.py 写) + 采集产物清单(products/status,沈括写)
      video.mp4  asr.paraformer.json  asr.timeline.json  asr.txt  asr.clean.txt
      cover.jpg  comments.json  audio/  frames/  cutouts/

manifest 顶层 key 按"产出者"分区、互不重叠：
  - card     : works.py 智能解析的作品卡（标题/话题/四项数据/作者档案）
  - products : 沈括各支线产物的相对路径
  - status   : 沈括各支线状态
两端各写各的分区，merge 时读-改-写保留对方分区（非原子跨进程，低并发够用，
与 server/routes/works.py 原有 per-key 锁同级别保证）。

根目录口径：与 server.state 一致 —— 设了 NOF_STATE_DIR 用其父目录（测试隔离/
部署自定义），否则落仓库根 state/。这里**运行时**解析（不在 import 期定死），
免得测试 setenv 后还要 reload；也刻意不反向 import server.state，保持 common 独立。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]


def _state_root() -> Path:
    env = os.environ.get("NOF_STATE_DIR")
    return Path(env).parent if env else _ROOT / "state"


def works_root() -> Path:
    return _state_root() / "works"


def work_dir(platform: str, aweme_id: str) -> Path:
    """该作品的产物目录（按需创建）。"""
    d = works_root() / platform / aweme_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def manifest_path(platform: str, aweme_id: str) -> Path:
    return works_root() / platform / aweme_id / "manifest.json"


def load_manifest(platform: str, aweme_id: str) -> dict[str, Any] | None:
    """读 manifest；缺文件/坏文件返回 None（回退实拉/实采），不抛。"""
    try:
        return json.loads(manifest_path(platform, aweme_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_manifest(platform: str, aweme_id: str, manifest: dict[str, Any]) -> None:
    work_dir(platform, aweme_id)  # 确保目录在
    path = manifest_path(platform, aweme_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # 原子替换：同进程内 last-writer-wins，分区不重叠故互不丢


def merge(platform: str, aweme_id: str, **patch: Any) -> dict[str, Any]:
    """读-改-写：用 patch 覆盖 manifest 顶层 key，保留未涉及的分区。"""
    m = load_manifest(platform, aweme_id) or {}
    m["platform"] = platform
    m["aweme_id"] = aweme_id
    m.update(patch)
    _write_manifest(platform, aweme_id, m)
    return m


# --- 作品卡分区（works.py 智能解析）------------------------------------------ #
def load_card(platform: str, aweme_id: str) -> dict[str, Any] | None:
    m = load_manifest(platform, aweme_id)
    return m.get("card") if m else None


def save_card(platform: str, aweme_id: str, card: dict[str, Any]) -> None:
    merge(platform, aweme_id, card=card)


# --- 赛道标签分区（domain，作者→作品继承 / 前端 task-2.3 写入）--------------- #
def load_domain(platform: str, aweme_id: str) -> str | None:
    """读作品 manifest 顶层 domain 字段；缺文件/无字段/空串均返回 None。"""
    m = load_manifest(platform, aweme_id)
    if not m:
        return None
    v = m.get("domain")
    return v if isinstance(v, str) and v.strip() else None


def save_domain(platform: str, aweme_id: str, domain: str | None) -> None:
    """把 domain 写进 manifest。空串/None 时不写入，保持 manifest 干净（present-only 原则）。"""
    if not domain or not domain.strip():
        return
    merge(platform, aweme_id, domain=domain.strip())
