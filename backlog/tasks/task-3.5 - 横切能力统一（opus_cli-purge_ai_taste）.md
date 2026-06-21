---
id: task-3.5
title: 横切能力统一（opus_cli / purge_ai_taste）
status: To Do
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
- [ ] #1 `call_opus` 补 system_prompt 通道（若缺）
- [ ] #2 `_polish_transcript_with_opus` / `_call_opus_for_rw` / `_call_opus_judge` 全改委托 call_opus，删重复手拼
- [ ] #3 `purge_ai_taste` 抽到 common/ 单一实现，liuyong 与 pipeline_runner 共用
- [ ] #4 回归 586 passed；rw/judge 行为前后一致（用 fake 验等价）
<!-- AC:END -->
