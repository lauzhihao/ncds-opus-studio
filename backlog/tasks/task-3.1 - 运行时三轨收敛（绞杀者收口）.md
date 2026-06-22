---
id: task-3.1
title: 运行时三轨收敛（绞杀者收口）
status: In Progress
assignee: []
created_date: '2026-06-19 13:46'
labels:
  - architecture
  - strangler
  - decision
dependencies: []
parent_task_id: task-3
priority: high
---

## Description

现状（见 CODE-REVIEW-2026-06-19 §2）：三套执行运行时并存，engine 没有替代 PipelineRunner 而是
被塞进它当 6/7 步执行内核。

- **web** → `/jobs/*`（`routes/pipelines.py`）→ PipelineRunner facade →命中节点转 engine `run_step`；
  asr 例外永走 legacy（`pipeline_runner.py:1306` 硬编码 `node_name != "asr"`）。
- **app** → `/tasks`（`task_runner.py`）→ 完全未迁移，老轨。
- **`/instances`**（engine HTTP 入口）→ 前端零调用，仅测试覆盖。
- 6 个 legacy `_execute_*` 作为 `NOF_ENGINE_NODES=legacy` 回退冷藏（`pipeline_runner.py:1457-2440`）。

目标：确立 engine 为唯一执行真源，逐步退役 legacy 双轨；但保留可控回退期。

## 关联 Findings
A1 / A2（legacy↔engine 已漂移：lines/storyboard 的 JSON 重试 + domain_image_style 只在 engine 有）/ A6 / A7。

## ⚠️ 决策（DEC-2，等 owner）
是否删 6 个 legacy `_execute_*` 冷藏路径？**默认建议：暂不删**，留作回退护城河，直到 engine 路径
再跑通几个生产周期且确认运维不依赖 `NOF_ENGINE_NODES=legacy`。删除属 >30 行不可逆操作。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 产出"哪条路径是 web/app 当前活路径"的事实地图，注释进 routes/instances.py 顶部（澄清 engine 经 facade 间接驱动、非前端直连）
- [x] #2 修复 A2 漂移：legacy 与 engine 的 lines/storyboard 行为对齐（要么 legacy 补 JSON 重试+domain_image_style，要么确认 legacy 仅回退用并标注）
- [x] #3 给出 app `/tasks`(TaskRunner) → engine 的迁移分期草案（依赖 EngineEventBus 改文件 tail，见 backlog/docs/S3-redis-worker-design.md S3.x）
- [ ] #4（决策后）若删 legacy 执行体：回归 586 passed，且 web 全 7 步 UI 端到端不退化
<!-- AC:END -->

## Implementation Plan

先做无风险的 AC#1（注释/事实地图）+ AC#2（漂移对齐，纯收敛不删）。AC#3 出方案。AC#4 待 DEC-2。

## 完成记录

- 2026-06-22：`routes/instances.py` 顶部补当前入口事实地图：web 活路径仍是 `/jobs` facade，app 活路径仍是 `/tasks`/`nof-worker`，`/instances` 是 engine driver API 与迁移入口，不是前端直连主路径。
- 2026-06-22：按 DEC-2 默认策略确认 legacy 仅作冷回退，不补新主链逻辑；`pipeline_runner.py` 标注非 asr legacy `_execute_*` 仅在 `NOF_ENGINE_NODES=legacy/off/none` 或显式排除节点时使用，并记录 A2 已知漂移（legacy lines/storyboard 缺 JSON retry，storyboard 缺 domain_image_style 注入）。
- 2026-06-22：`docs/PRODUCTION-ENGINE-DESIGN.md` 增补 app `/tasks` → engine 迁移草案：先做 engine events.jsonl tail/merge，再做 task/instance 兼容层，随后迁卧龙 driver、app 视图，最后清理生产链 TaskRunner 化石命令。
- 待决策：AC#4 取决于 DEC-2 是否删除 legacy 执行体；当前默认不删，保留回退护城河。
