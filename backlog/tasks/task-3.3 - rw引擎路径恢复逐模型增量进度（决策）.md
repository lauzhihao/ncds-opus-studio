---
id: task-3.3
title: rw 引擎路径恢复逐模型增量进度（决策）
status: Done
assignee: []
created_date: '2026-06-19 13:46'
labels:
  - decision
  - ux
  - engine
dependencies: []
parent_task_id: task-3
priority: medium
---

## Description

修复前：rw 默认走 engine（当时 `pipeline_runner.py` 分发只排除 asr，rw 不排除）。engine `run_rw_step`
（`pipeline_performers_015.py:316,397`）刻意删掉了 `_push_model_progress` / `push_outputs_patch(...,
"drafts")` 增量推送，on_status 给 no-op。

**影响范围核实清楚**：产物**完全不变**——drafts 仍写 `02_rw/{model_id}/draft.md`，节点 done 时一次性
push 全部 drafts，前端 LiuyongPanel 照常渲染两 tab（改写方案 A/D）。**仅丢失** rw 步执行期间的"模型 X
运行中/完成"逐模型实时面板 + 完成一个即可看/选（文本进度仍经 on_progress 透出）。设计注释
`pipeline_runner.py:148` 已明确接受此权衡（"产物不受影响"）。commit 5f1fe3f 恢复双模型后前端"零改动"。

所以这是**已知 UX 退化、非功能 bug**。当前已按 DEC-1 回退 rw 走 legacy，以恢复该 UX。

## 关联 Findings
A5。

## 决策（DEC-1）

要不要把逐模型实时进度面板找回来？

- **默认建议（最佳实践）**：**回退 rw 走 legacy** —— `:1306` 改 `node_name not in ("asr", "rw")`。
  1 行、可逆、纯 UX 优化（owner 明确允许"优化"）；legacy `_execute_rw` 自带增量推送且与 engine 共享
  `MODEL_CANDIDATES`/`_invoke_rw`/`_build_rw_prompt`/`_apply_rw_qc`，**无维护分叉**。
- 正解（更大工作量）：给 `InstanceRunner.run_step` 补 `on_outputs_patch` seam，performer 内恢复增量
  推送回桥 facade SSE。
- **已执行**：按默认建议回退 rw 走 legacy，恢复执行期间逐模型实时进度面板；产物、模型候选、prompt/QC helper
  仍与 engine performer 共用。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 owner 在"回退 legacy"/"补 engine seam"/"维持现状文本进度"三者中拍板
- [x] #2 若回退 legacy：改调度分发 + 回归 586 passed + SSE/JobState 合同层验证柳永抽屉所需逐模型实时数据恢复
- [x] #3 若维持现状：在 :148/:316 注释明确"逐模型实时面板已知缺失，文本进度替代"，关单（本次未选该方案，N/A）
<!-- AC:END -->

## 完成记录

- 2026-06-22：按默认建议将 rw 固定回 legacy 执行路径：默认 `NOF_ENGINE_NODES` 不再包含 `rw`，调度分发也把 `rw` 与 `asr` 一起列为 legacy-only，避免显式 `NOF_ENGINE_NODES=rw` 误绕过逐模型增量输出。
- 2026-06-22：新增 strangler 回归测试，锁定 rw 即使被 engine flag 命中也走 `_execute_rw`；`PipelineRwRun` 继续负责 `_push_model_progress` 与增量 `outputs.drafts` patch，web 柳永抽屉可在单个模型完成后立即显示/选择。
- 2026-06-22：同步更新 README / FRONTEND-API / PRODUCTION-ENGINE-DESIGN 中的当前运行时事实：默认 engine 节点为 `lines/storyboard/tts/image/render`，`asr` 与 `rw` 固定 legacy。
- 验证：`pytest tests/server/test_engine_strangler.py tests/server/test_pipeline_runner.py tests/server/test_pipeline_performers_015.py -q` 50 passed；全量 `.venv/bin/python3 -m pytest -q` 610 passed；严格 warning 模式 `.venv/bin/python3 -m pytest -q -W default` 610 passed。
