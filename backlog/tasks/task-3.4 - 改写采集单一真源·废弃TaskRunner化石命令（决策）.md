---
id: task-3.4
title: 改写/采集单一真源·废弃 TaskRunner 化石命令（决策）
status: To Do
assignee: []
created_date: '2026-06-19 13:46'
labels:
  - decision
  - logic-divergence
  - cleanup
dependencies: []
parent_task_id: task-3
priority: high
---

## Description

同一能力多份实现已漂移（见 CODE-REVIEW §4.1 A3/A4、§6）：

**改写 4 套**：
- `commands/rw.py`：输入=飞书 docx URL，profile=`douyin`，spawn `rewrite_command_runner.mjs`，**零质检**，写飞书。docstring/argparse 还写"gpt-5.5+gemini"（已漂移）。
- `commands/liuyong.py`：输入=一句话选题，profile=`douyin_cog`，spawn `content_rewrite_runner.mjs`，自带 QC。
- `_execute_rw`（legacy）/ `run_rw_step`（engine）：输入=asr 清洗稿，直调 opus+deepseek（不走 .mjs），QC=`_apply_rw_qc`。engine import legacy = 这两者已是单一真源。

**采集 3 套**：collect_one 快采（legacy 活路径）/ engine `run_asr_step`（video_pipeline，**死代码**，被 `:1306` 排除）/ commands/shenkuo.collect_one 全量。

`commands/liuyong.py` + `commands/rw.py` 是旧 TaskRunner 时代化石，被 `task_runner`/`command_schemas`/
`label_store`/`mock_agents`/`artifacts` 注册引用。web `/jobs` 链**不**用它们。

## ⚠️ 决策（DEC-4，等 owner）

废弃 `commands/liuyong.py` + `commands/rw.py`？
- **默认建议**：先核实 app `/tasks` 与 CLI 是否仍在实际派 liuyong/rw；**若否**则废弃并从
  command_schemas/label_store/mock_agents/artifacts 清理残留；**若是**则先把 web 链的 `_apply_rw_qc`
  统一口径反哺，再决定。
- **为何没替你执行**：仍被 registry/CLI 引用，删可能断 app 派单 + CLI，需确认使用面。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 核实 commands/liuyong.py、commands/rw.py 是否仍被 app /tasks 或 CLI 真实使用（给证据）
- [ ] #2 死代码 engine `run_asr_step`：删除或让其内部也调 collect_one 统一口径（二选一，消除"注册了跑不到"）
- [ ] #3 rw 源文本口径固定为 collect_one 的 entry.text（§6.4）
- [ ] #4 修文档漂移：`pipeline_runner.py:15` rw 注释、`commands/rw.py` docstring/argparse 的"gpt-5.5+gemini"
- [ ] #5（决策后）废弃化石命令则注册表残留全清，回归 586 passed
<!-- AC:END -->
