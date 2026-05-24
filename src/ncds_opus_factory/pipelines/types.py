"""Pipeline 数据模型：节点 / DAG / 状态机。

设计原则
--------
- pipeline 是「固定步骤」的线性 DAG（允许局部并行分叉），不是用户可改的工作流图
- 每个节点对应一个 commands/* 里的 command（cmd 字段引用 COMMAND_REGISTRY 的 key）
- 节点之间通过文件落盘传值：每个节点把产物写到 job_dir/<out_dir>/，
  下游节点从那里读
- UI 上节点可拖拽位置/缩放，但**步骤本身（顺序、节点数）不可改**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

NodeStatus = Literal["idle", "queued", "running", "done", "failed"]
"""节点状态机：idle → queued → running → done | failed。
重跑某节点会把它及下游全部 reset 回 idle。"""


@dataclass(frozen=True)
class NodePosition:
    """画布上节点的默认布局位置。前端可让用户拖动后持久化到 job 状态。"""
    x: float
    y: float


@dataclass(frozen=True)
class PipelineNode:
    name: str                          # 节点 ID（唯一）；如 "asr" / "rw" / "wst" / "tts" / "render"
    label: str                         # 显示名
    cmd: str                           # 对应 COMMAND_REGISTRY 里的 key
    deps: tuple[str, ...] = ()         # 上游节点 names
    out_dir: str = ""                  # 产物落盘子目录，相对 job_dir，如 "01_asr"
    description: str = ""              # 节点的简介，前端 tooltip 用
    position: NodePosition = field(default_factory=lambda: NodePosition(0, 0))
    # 节点类别，给前端选不同的展开 UI 模板：
    #   "input"  — 用户输入卡（URL 输入等），无 cmd 实际执行
    #   "command" — 调 cmd 跑后台任务
    #   "output" — 终态产物展示卡
    kind: Literal["input", "command", "output"] = "command"


@dataclass(frozen=True)
class PipelineDef:
    """一条 pipeline 的完整声明。"""
    id: str
    name: str
    description: str
    nodes: tuple[PipelineNode, ...]

    def node(self, name: str) -> PipelineNode:
        for n in self.nodes:
            if n.name == name:
                return n
        raise KeyError(f"node not found: {name} in pipeline {self.id}")

    def downstream_of(self, name: str) -> list[str]:
        """node 的下游节点 names（递归，BFS）。"""
        result: list[str] = []
        frontier = [name]
        seen = {name}
        while frontier:
            cur = frontier.pop()
            for n in self.nodes:
                if cur in n.deps and n.name not in seen:
                    seen.add(n.name)
                    result.append(n.name)
                    frontier.append(n.name)
        return result

    def topological_order(self) -> list[str]:
        """返回拓扑序的节点 names。"""
        order: list[str] = []
        seen: set[str] = set()
        nodes_by_name = {n.name: n for n in self.nodes}

        def visit(name: str) -> None:
            if name in seen:
                return
            for dep in nodes_by_name[name].deps:
                visit(dep)
            seen.add(name)
            order.append(name)

        for n in self.nodes:
            visit(n.name)
        return order
