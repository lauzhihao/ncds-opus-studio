---
id: task-3.2
title: pipeline_runner god-object 拆分
status: To Do
assignee: []
created_date: '2026-06-19 13:46'
labels:
  - refactor
  - maintainability
dependencies:
  - task-3.1
parent_task_id: task-3
priority: high
---

## Description

`server/pipeline_runner.py` 3172 行、单类 `PipelineRunner` 60+ 方法，混杂：job 状态机（_save/_load/
_emit）、SSE EventBus、跨进程 cancel 文件标记、6 步执行体（_execute_*）、engine 桥接（_execute_via_
engine/_engine_step_inputs）、3 类 agent 后台（enrich/refresh/guiguzi）、rw 模型重生+质检、mock 旁路。
是全仓最大债源（见 CODE-REVIEW §4.2 C1）。

## 关联 Findings
C1（god-object）/ C5（collect_one 235 行 + 5 个超长函数）/ C6（rewrite/refine 复制 5 次）/ A7（/jobs
端点散在 pipelines.py）/ C9（app models.dart 978 行、web HomePage.tsx 896 行，低优）。

## Implementation Plan（分期，不要求一次到位；blast radius 大，属 L2）

1. 抽 3 类 agent 后台任务（enrich/refresh/guiguzi）成独立模块——它们与 015 step 执行正交，最先抽、最安全。
2. engine 桥接（_execute_via_engine/_engine_step_inputs）收进 engine 侧 facade adapter。
3. 随 task-3.1 漂移收敛删 legacy `_execute_*`（待 DEC-2）。
4. 低风险局部：抽 `_assert_known_model` / `_rw_mock_short_circuit` 消 C6 复制（纯抽取，行为不变）。
5. C5：collect_one 等超长函数按子步骤拆 helper。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 agent 后台任务（enrich/refresh/guiguzi）抽出独立模块，pipeline_runner 行数显著下降，586 passed
- [ ] #2 C6 复制抽成 helper（`_assert_known_model` + mock 短路），行为不变
- [ ] #3 每步拆分后回归 586 passed、web 全链路 UI 不退化
- [ ] #4 拆分边界先对齐再动手（L2，prefer 小步多次）
<!-- AC:END -->
