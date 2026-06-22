---
id: task-3.5
title: 横切能力统一（opus_cli / purge_ai_taste）
status: Done
assignee: []
created_date: '2026-06-19 13:46'
labels:
  - refactor
  - dedup
dependencies: []
parent_task_id: task-3
priority: medium
---

## Description

横切能力各写多份且开始漂移（见 CODE-REVIEW §4.2 C2/C3、§6.1-3）：

1. **opus 启动器**：`common/opus_cli.call_opus` 本是统一封装，但 `pipeline_runner._polish_transcript_
   with_opus`（:2553）、`_call_opus_for_rw`（:2746）、`quality_rubric._call_opus_judge`（:147）各自手拼
   `opus launch --no-resume --effort max --model claude-opus-4-8` + 各自解析 NDJSON。改一处易漏。
2. **消 AI 味 purge_ai_taste**：`commands/liuyong.py:44` 与 `pipeline_runner.py:2936` 两份，prompt 正文
   已漂移（liuyong 列具体禁用句式，rw 只给摘要；命中字段拼法也不同）→ 同稿走两路消味效果不同。
3. **rw 质检闸门**：`_apply_rw_qc` 已是 legacy+engine 共用单点，但 `commands/liuyong.py:144-174` 仍内联一份。

## 注意
统一 opus 调用前需先核实 `call_opus` 是否支持 rw 需要的 **system_prompt 通道**（当前可能不支持，
需先补）。这是该任务的前置 blocker。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `call_opus` 补 system_prompt 通道（若缺）
- [x] #2 `_polish_transcript_with_opus` / `_call_opus_for_rw` / `_call_opus_judge` 全改委托 call_opus，删重复手拼
- [x] #3 `purge_ai_taste` 抽到 common/ 单一实现，liuyong 与 pipeline_runner 共用
- [x] #4 回归 586 passed；rw/judge 行为前后一致（用 fake 验等价）
<!-- AC:END -->

## 完成记录

- 2026-06-22：确认 `common.opus_cli.call_opus` 已支持 `system_prompt` / `timeout_seconds` / `env`，补齐 launch 参数、NDJSON result、error、availability fallback 的单测；新增 `is_opus_available()`，统一 PATH 与 `~/.sclaude/bin/opus` 回退判断。
- 2026-06-22：`pipeline_runner._polish_transcript_with_opus` 与 `_call_opus_for_rw` 改为委托 `call_opus`；`quality_rubric` 的 opus judge 已经走 `call_opus`，同步改可用性判断为 common 单点。
- 2026-06-22：新增 `common.ai_taste.build_purge_prompt()` / `purge_ai_taste()`，以柳永更完整的消 AI 味 prompt 为 canonical；`commands/liuyong.py` 与 `pipeline_runner._purge_ai_taste_rw` 共用该实现。
- 验证：focused tests `70 passed`；`py_compile` 与 `git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 通过，`597 passed, 173 warnings in 25.55s`。
