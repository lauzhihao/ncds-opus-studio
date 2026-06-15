---
id: task-1.1
title: S1：SSE 事件源落盘化(events.jsonl) + 8810 改 tail 文件广播
status: Done
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
- [x] #1 每个 node_status/progress/outputs 变更落 events.jsonl 一行，字段含 job_id/type/node/payload/ts/单调 seq
- [x] #2 SSE 端点从 events.jsonl tail(byte offset 续读)广播，前端 SSE 行为不变(snapshot/node_status/progress/outputs patch 顺序正确)
- [x] #3 断线重连可从指定 seq/offset 重放历史事件不丢（边界竞态已修：offset 记录挪到 snapshot 之前；重放有 SSE 真测试守护）
- [x] #4 新增 events 写入与 tail 续读单测；现有 pytest 绿（含 SSE handler 真测试 5 个 + torn-write 守护 1 个，445 passed）

> 验收记录(主线程)：AC#1/#2 成立 —— events.jsonl 每行落「event 原样 + ts/seq 信封」(`record = {**event, ts, seq}` + `setdefault('node', None)`)，**不埋 payload 子对象**，前端 `useJobStream` 的 wire 契约(type/node/state)不破(types.ts 按 `{type,job_id,node,state}`)。**全量 pytest 实为 439 passed / 0 failed**(先前误报 829 系损坏输出，已更正)。
>
> **ultrareview 复核(2026-06-15)翻出的缺口 —— 已全部修复并补测(445 passed / 445 collected，主线程独立交叉核对)：**
> 1. **SSE handler `stream_events` 零测试覆盖**：11 个测试只测 `_emit`+手读文件、从没调真 endpoint → 补 httpx/Starlette TestClient 真 SSE 测试(默认无 since_seq 只收增量 / `?since_seq=N` 重放不重不丢 / client close 触发 CancelledError 正常退出)。
> 2. **snapshot/tail 边界丢事件竞态**(真回归)：先 yield snapshot 再记 offset，期间 `_emit` 的事件既不在 snapshot 也不被 tail = 丢 → **修法：把 tail 起始 offset 的记录挪到 yield snapshot 之前**(丢→重，重是幂等无害，对齐原始 bus.subscribe-先订阅-后快照的安全方向)。
> 3. **events.jsonl 末行 torn write**(进程被杀写半截)：seq 恢复对残行 `last_seq+=1` 误算 + 新行拼接到残行 → `_emit` 追加前探测末字节非 `\n` 则先补 `\n`；恢复循环忽略残行、不计 seq。
> 4. AC#3 的 `since_seq` 重放后端已实现但前端不发该参数；修②后重连本就靠全量 snapshot 兜底无损，`since_seq` 留作"省全量重传"的优化、暂不接前端(不动 web/)。
<!-- AC:END -->

## Implementation Plan

pipeline_runner.py：EventBus 发布处同时 append events.jsonl(单写者、O_APPEND 原子整行)；SSE handler(subscribe 处)改 tail 文件 offset。单文件追加即可，轮转暂不做。
