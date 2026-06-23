---
id: task-4.1
title: 吴道子视觉工作台前端收敛
status: To Do
assignee: []
created_date: '2026-06-23'
labels:
  - frontend
  - product
  - wudaozi
dependencies: []
parent_task_id: task-4
priority: high
---

## Description

把当前吴道子面板从技术节点 tab 收敛成一个面向用户的视觉工作台。用户看到的是"画面方案/分镜/画面资产"的连续工作流，而不是 `lines`、`storyboard`、`image` 这些底层实现节点。

建议实现方向：

- 从 `web/src/config/agents.ts` 开始收敛吴道子的 role、description、children 命名，避免继续把 `lines` 暴露成"台词"主工作。
- 前端组件层复用现有 `StoryboardPanel` 与 `ImageResultPanel` 的有效能力，但不要保留旧的三 tab 结构作为第一屏。
- 工作台可以分为两个区域：上半区为视觉方案/分镜，下半区为画面资产/生成结果。视觉方案完成后应能直接生成画面资产，不应等待伯牙声音。
- `lines` 只允许以只读摘要或隐藏前置状态出现，不提供用户编辑主入口。

## Implementation Notes

- 优先局部改 web 端配置和面板组合，不在本任务改 backend performer。
- 如果现有 tab 组件强耦合，允许新增 `WudaoziPanel` 包装层，再把旧面板能力以子组件方式嵌入。
- 视觉工作台内的按钮状态必须和 `node.state`、`actionBusy`、`refineBusy` 等 busy 状态一致，避免重复执行。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 用户打开吴道子节点时，默认看到视觉工作台，不再看到 `台词 / 分镜 / 画质` 三个主 tab
- [ ] #2 工作台明确呈现吴道子的职责为画面：分镜、视觉提示词、画面资产与结果检查
- [ ] #3 视觉方案完成后，`image` 可作为吴道子的下一步直接执行；界面不出现等待伯牙声音的阻塞态
- [ ] #4 现有分镜查看、编辑、重新生成能力没有退化；已有图片结果仍可查看和下载
- [ ] #5 `npm run build` 通过，主要断点下没有按钮文字溢出或面板内容重叠
<!-- AC:END -->
