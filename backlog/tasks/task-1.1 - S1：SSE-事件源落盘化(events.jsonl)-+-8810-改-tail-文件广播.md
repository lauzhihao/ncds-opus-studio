---
id: task-1.1
title: S1：SSE 事件源落盘化(events.jsonl) + 8810 改 tail 文件广播
status: To Do
assignee: []
created_date: '2026-06-15 02:38'
labels:
  - worker-split
  - sse
  - backend
dependencies: []
parent_task_id: task-1
priority: high
---

## Description

父任务 task-1。现状进度走内存 pub/sub(pipeline_runner 的 EventBus，_push_progress/_push_outputs_patch/node_status 广播给 subscribers 的 asyncio.Queue)，执行与广播同进程耦合。本阶段先把事件源解耦成文件(执行仍可同进程)：每个状态变更除现有内存广播外，再追加一行到 video-jobs/{job_id}/events.jsonl；8810 的 SSE 端点改为 tail 该文件按 offset 续读再广播。为 S3 跨进程铺路。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 每个 node_status/progress/outputs 变更落 events.jsonl 一行，字段含 job_id/type/node/payload/ts/单调 seq
- [ ] #2 SSE 端点从 events.jsonl tail(byte offset 续读)广播，前端 SSE 行为不变(snapshot/node_status/progress/outputs patch 顺序正确)
- [ ] #3 断线重连可从指定 seq/offset 重放历史事件不丢
- [ ] #4 新增 events 写入与 tail 续读单测；现有 pytest 绿
<!-- AC:END -->

## Implementation Plan

pipeline_runner.py：EventBus 发布处同时 append events.jsonl(单写者、O_APPEND 原子整行)；SSE handler(subscribe 处)改 tail 文件 offset。单文件追加即可，轮转暂不做。
