---
id: task-4.5
title: 产线推进、节点状态与文案对齐
status: To Do
assignee: []
created_date: '2026-06-23'
labels:
  - frontend
  - product
  - pipeline
dependencies:
  - task-4.2
  - task-4.3
  - task-4.4
parent_task_id: task-4
priority: high
---

## Description

把前端画布的"用户理解路径"与当前技术 DAG 做一次明确映射。用户看到的是：

`柳永产稿 -> 吴道子画面 -> 伯牙声音 -> 成片`

当前技术节点应为：

`rw -> lines -> storyboard -> image -> tts -> preview -> render`

第一版已把 backend DAG 与产品路径对齐，因此本任务负责前端状态聚合、按钮启停、下一步文案和自动推进，避免用户被 `lines` 这样的隐藏前置节点困住。

## Mapping Proposal

- 柳永：聚合 `rw`，完成后可进入吴道子。
- 吴道子：聚合 `lines` hidden preflight、`storyboard` 可见工作、`image` 画面资产结果。
- 伯牙：聚合 `tts`。
- 成片：聚合 `preview` / `render` / `download`。

其中 `image` 属于吴道子的画面资产，完成后再推进到伯牙 `tts`；不再保留声音先跑、画面后补的旧流程。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `web/src/config/agents.ts` 的 agent 命名、role、description、children 与第一版职责一致
- [ ] #2 柳永完成后下一步指向吴道子；吴道子视觉方案完成后能生成画面资产；画面资产确认后进入伯牙；伯牙完成后进入成片检查
- [ ] #3 `立即优化`、`重新执行`、`继续生成`、`下载` 等按钮在 running/refining/failed/succeeded 状态下不会互相打架或重复入队
- [ ] #4 页面刷新、SSE 重连、job 历史打开时，聚合状态能从已有节点状态恢复
- [ ] #5 前端文案不再把旧技术残留误导为产品功能，例如不把 `lines` 说成吴道子的核心 tab
<!-- AC:END -->
