---
id: task-4.2
title: lines 隐藏前置与自动准备
status: To Do
assignee: []
created_date: '2026-06-23'
labels:
  - frontend
  - pipeline
  - wudaozi
dependencies:
  - task-4.1
parent_task_id: task-4
priority: high
---

## Description

`lines` 是当前 `storyboard` 前置所需的结构化台词数据，但产品语义上不应成为吴道子的可见职责。第一版把它做成吴道子入口的隐藏 preflight：用户进入吴道子时，如果 `lines` 缺失、失败或与最新柳永稿件不一致，系统自动准备；准备完成后进入视觉工作台。

建议实现方向：

- 在吴道子面板入口检查当前 job 的 `lines` 节点状态、输出版本和错误状态。
- `lines` 未 ready 时，自动触发现有节点执行 API，展示"正在准备台词结构"之类的短状态。
- 自动执行要防重入：同一 job 同一状态下不能连续点开面板触发多个 `lines` run。
- `lines` 失败时，面板给出重试按钮和错误摘要；重试仍调用现有节点 API，不引入新 backend endpoint。

## Edge Cases

- 柳永稿件重新优化后，旧 `lines` 需要失效或重新执行；如果当前系统已有节点 dirty/invalid 状态，直接复用。
- 用户在 `lines` 运行中切换节点再回来，应看到同一个运行状态，而不是重新发起。
- nof-worker 或 SSE 断开时，页面刷新后仍能从 job 状态恢复。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `lines` 缺失时，进入吴道子会自动准备台词结构，无需用户手动点击"台词"tab 或理解 `lines`
- [ ] #2 `lines` running/succeeded/failed 三种状态在吴道子面板中都有稳定 UI 表达
- [ ] #3 自动准备过程不会重复入队同一个节点；重复打开面板、刷新页面、SSE 重连都不会造成多次执行
- [ ] #4 柳永重新优化或重新执行后，吴道子入口能识别旧 `lines` 需要重新准备
- [ ] #5 失败重试走现有节点执行链路，日志和 SSE 事件格式不变
<!-- AC:END -->
