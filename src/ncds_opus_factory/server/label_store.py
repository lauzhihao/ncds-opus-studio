"""案卷库：每条人工决策的紧凑存档，独立于任务目录存活（docs/WOLONG-DESIGN.md §5.1）。

目录结构（base_dir 默认 ncds-opus-studio/state/wolong/labels）：

    state/wolong/labels/{task_id}.json   # 一条决策一份案卷

设计要点：
- 验收 = 替卧龙做的标注。案卷是离线复盘（rubric 学习）的训练数据真源，
  **永不清扫**——清扫协程删任务目录前必须确认案卷已存在（app.py sweep）。
- 改判 → 幂等覆盖，decision 变了标 revised；撤销(DELETE review) → 标 revoked
  （案卷保留，复盘剔除 revoked 样本）。
- artifact_digest 按 cmd 提取产物要点（学习要的是"特征+判断"，不是原始素材）；
  result 缺失（failed/cancelled 任务也可能被打 decision）降级为只存 params。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ncds_opus_factory.server.schemas import Review, TaskMeta
from ncds_opus_factory.server.task_store import _valid_task_id

logger = logging.getLogger(__name__)

# 摘要长度上限：够复盘 LLM 对比通过/拒绝样本，不至于把案卷库吃成素材库
_DIGEST_CHARS = 500
_PARAM_VALUE_CHARS = 200


def _params_digest(params: dict[str, Any]) -> dict[str, Any]:
    """参数瘦身：长字符串截断，其余原样（params 本就来自 JSON，可序列化）。"""
    out: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, str) and len(v) > _PARAM_VALUE_CHARS:
            out[k] = v[:_PARAM_VALUE_CHARS] + "…"
        else:
            out[k] = v
    return out


def _artifact_digest(cmd: str, result: dict[str, Any] | None) -> str | None:
    """按 cmd 提取产物要点。形状不符/提取失败一律走兜底，绝不抛。"""
    if not isinstance(result, dict):
        return None
    try:
        if cmd == "liuyong":
            # {drafts:[{model, path, text}]} → 各稿开头
            parts = [
                f"[{d.get('model', '?')}] {str(d.get('text', ''))[:_DIGEST_CHARS]}"
                for d in result.get("drafts") or []
                if isinstance(d, dict)
            ]
            if parts:
                return "\n---\n".join(parts)
        elif cmd == "guiguzi":
            # {topics:[{title, ...}]} → 选题标题列表
            titles = [
                str(t.get("title") if isinstance(t, dict) else t)[:80]
                for t in (result.get("topics") or [])[:20]
            ]
            if titles:
                return "选题：" + "；".join(titles)
        elif cmd == "shenkuo":
            # {all_posts, collected:[...]} → 采集规模 + 深采条目标题
            collected = result.get("collected") or []
            titles = [
                str(c.get("title") or c.get("desc") or "")[:60]
                for c in collected[:10]
                if isinstance(c, dict)
            ]
            head = f"拉取 {result.get('all_posts', '?')} 条，深采 {len(collected)} 条"
            return head + ("：" + "；".join(t for t in titles if t) if any(titles) else "")
        elif cmd == "wolong":
            # {count, tail} → 编排战果 + 日志尾
            tail = str(result.get("tail", ""))
            return f"本轮产出 {result.get('count', '?')} 条；tail: {tail[-_DIGEST_CHARS:]}"
    except Exception:  # noqa: BLE001 — 摘要失败不能影响标注落库
        logger.warning("[labels] artifact digest failed for cmd=%s", cmd, exc_info=True)
    # 兜底：截断的 result JSON
    try:
        return json.dumps(result, ensure_ascii=False)[:_DIGEST_CHARS]
    except (TypeError, ValueError):
        return None


class LabelStore:
    """按 task_id 持久化决策案卷（一条决策一个 JSON 文件）。"""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def label_path(self, task_id: str) -> Path:
        return self.base_dir / f"{task_id}.json"

    def exists(self, task_id: str) -> bool:
        """有且可解析才算存在——清扫拿这个守门,撕裂的半截文件不能放行 rmtree。"""
        return self.get(task_id) is not None

    def _atomic_write(self, task_id: str, label: dict[str, Any]) -> None:
        # tmp + os.replace:进程被杀/ENOSPC 不能留下截断 JSON——案卷在任务目录
        # 删除后是标签的唯一真源,改判覆盖写也不能有把好文件写坏的窗口
        path = self.label_path(task_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(label, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def get(self, task_id: str) -> dict[str, Any] | None:
        if not _valid_task_id(task_id):
            return None
        path = self.label_path(task_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("[labels] unreadable label file: %s", path)
            return None

    def write(self, meta: TaskMeta, review: Review, result: dict[str, Any] | None) -> dict[str, Any]:
        """写/覆盖案卷。改判（decision 变化）标 revised；重新决策清掉 revoked。"""
        if not _valid_task_id(meta.task_id):
            raise ValueError(f"invalid task_id: {meta.task_id}")
        prev = self.get(meta.task_id)
        # note_origin 缺省推定与 reviewer 绑定:只有 Leader 的备注才推定 user,
        # 卧龙(预筛)的备注是机器推断——不能冒充一手人工语料
        note_origin = review.note_origin
        if note_origin is None and review.note:
            note_origin = "user" if review.reviewer == "user" else "inferred"
        label: dict[str, Any] = {
            "task_id": meta.task_id,
            "cmd": meta.cmd,
            "source": meta.source,
            "parent_task_id": meta.parent_task_id,
            "round_id": meta.round_id,
            "params_digest": _params_digest(meta.params),
            "artifact_digest": _artifact_digest(meta.cmd, result),
            "decision": review.decision,
            "note": review.note,
            "note_origin": note_origin,
            "reviewer": review.reviewer,
            "reviewed_at": review.reviewed_at,
            # 粘滞:一旦改判过永久为 True（"这条判断曾被推翻"本身是复盘要的信号）
            "revised": bool(prev and (prev.get("revised") or prev.get("decision") != review.decision)),
            "revoked": False,
        }
        self._atomic_write(meta.task_id, label)
        return label

    def mark_revoked(self, task_id: str) -> bool:
        """撤销决策：案卷保留、标 revoked（复盘剔除）。返回是否真的标了。"""
        label = self.get(task_id)
        if label is None:
            return False
        label["revoked"] = True
        self._atomic_write(task_id, label)
        return True
