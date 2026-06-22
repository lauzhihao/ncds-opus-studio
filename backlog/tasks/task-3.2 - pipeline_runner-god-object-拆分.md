---
id: task-3.2
title: pipeline_runner god-object 拆分
status: In Progress
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
- [x] #1 agent 后台任务（enrich/refresh/guiguzi）抽出独立模块，pipeline_runner 行数显著下降，586 passed
- [x] #2 C6 复制抽成 helper（`_assert_known_model` + mock 短路），行为不变
- [ ] #3 每步拆分后回归 586 passed、web 全链路 UI 不退化
- [x] #4 拆分边界先对齐再动手（L2，prefer 小步多次）
<!-- AC:END -->

## 完成记录

- 2026-06-22：第一刀先做低风险局部抽取：`rewrite_rw_model` / `refine_rw_model` 共用 `_assert_known_model()` 与 `_rw_mock_short_circuit()`，保留原有错误语义和 mock015 短路行为。
- 验证：`pytest tests/server/test_pipeline_runner.py src/ncds_opus_factory/server/pipeline_runner_events_test.py src/ncds_opus_factory/server/pipelines_sse_test.py -q` 24 passed；`py_compile pipeline_runner.py` 与 `git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 598 passed, 173 warnings。
- 2026-06-22：第二刀以 mixin 机械搬移 agent 后台任务，新增 `server/pipeline_agent_tasks.py`，把 ASR enrich、沈括 refresh、鬼谷子 analyze/generate 后台任务从 `pipeline_runner.py` 拆出；`PipelineRunner` 继承 `PipelineAgentTasksMixin`，public methods 保持不变。
- 行数变化：`pipeline_runner.py` 从 3143 行降到 2779 行；新模块 `pipeline_agent_tasks.py` 393 行。
- 验证：`py_compile pipeline_runner.py pipeline_agent_tasks.py` 通过；focused tests `46 passed`；单独降级用例通过；全量 `.venv/bin/python3 -m pytest -q` 598 passed, 173 warnings。
- 2026-06-22：第三刀新增 `server/pipeline_engine_bridge.py` / `PipelineEngineBridgeMixin`，把 `attach_engine` / `_execute_via_engine` / `_engine_step_inputs` 从 `pipeline_runner.py` 拆出；`PipelineRunner` 继承 bridge + agent mixins，engine strangler 的 public 行为保持不变。
- 行数变化：`pipeline_runner.py` 从 2779 行降到 2707 行；新模块 `pipeline_engine_bridge.py` 88 行。
- 验证：`py_compile pipeline_runner.py pipeline_engine_bridge.py` 通过；focused tests `70 passed`；全量 `.venv/bin/python3 -m pytest -q` 598 passed, 173 warnings。
- 2026-06-22：第四刀处理 C5 的 `commands/shenkuo.collect_one` 超长函数，新增 `_CollectPaths` / `_CollectRun` / manifest helper，把路径规划、legacy 采纳、五条采集分支、manifest 写回拆出；`collect_one` 从约 235 行降到 47 行，public API 与并发编排顺序保持不变。
- 验证：`py_compile shenkuo.py` 通过；沈括 focused tests `23 passed`；pipeline/cancel focused tests `48 passed`；`git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 598 passed, 173 warnings。
- 2026-06-22：第五刀处理 C5 的 `PipelineRunner._execute_rw` 超长函数，新增 `_RwRun` 多模型运行上下文，把模型级状态推送、draft 增量写盘、单模型 run/QC、draft 排序汇总从 `_execute_rw` 拆出；`_execute_rw` 从约 120 行降到 45 行，public 行为和增量推送契约保持不变。
- 验证：`py_compile pipeline_runner.py` 通过；RW focused tests `64 passed`；pipeline/server focused tests `70 passed`；`git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 598 passed, 173 warnings。
