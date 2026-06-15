---
id: task-1.2
title: S2：cancel 落盘化(跨进程文件标记)
status: To Do
assignee: []
created_date: '2026-06-15 02:38'
labels:
  - worker-split
  - cancel
  - backend
dependencies: []
parent_task_id: task-1
priority: medium
---

## Description

父任务 task-1。取消现状：部分 spawn 子进程已有文件级取消(common/cancel、shenkuo 的 _run_proc_cancellable)，但节点级取消依赖同进程运行态(pipeline_runner._running_nodes)。worker 拆分后取消由 8810 发起、nof-worker 执行，必须跨进程。统一为文件标记：HTTP 端取消写 video-jobs/{job_id}/cancel/{node}.flag(或 task_store meta)，执行侧在 guard()/轮询点读标记协作式中止(含对子进程 SIGTERM)。与 S1 独立，可并行。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 取消 running 节点 → 执行侧下一个 guard 点中止，spawn 子进程被 SIGTERM，状态置 cancelled
- [ ] #2 跨进程验证：A 进程写标记、B 进程(模拟 worker)读到并中止(双进程脚本或单测)
- [ ] #3 现有 cancel 单测绿 + 新增跨进程取消单测
<!-- AC:END -->

## Implementation Plan

common/cancel.py 统一文件标记 API(写/查/清)；pipeline_runner 各 guard 点 + 子进程轮询读标记；routes cancel handler 改写文件而非操作内存 _running_nodes。
