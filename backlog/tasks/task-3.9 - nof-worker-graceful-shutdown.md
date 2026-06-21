---
id: task-3.9
title: nof-worker graceful shutdown
status: To Do
assignee: []
created_date: '2026-06-19 13:46'
labels:
  - convention
  - robustness
  - worker
dependencies: []
parent_task_id: task-3
priority: low
---

## Description

违反 CLAUDE.md §1「long-running 进程要处理异常和 graceful shutdown」（见 CODE-REVIEW §4.3 S3）。

`server/worker.py:76` 用裸 `await asyncio.Event().wait()` 持续运行，全文件无 SIGTERM/SIGINT handler、
无 try/finally 收尾。`_amain` 起了 4 个后台 task（_discard_sweeper / _round_reconciler /
subscription_loop / planner_loop）+ BRPOP consumer，launchd `restart`/`stop` 发 SIGTERM 时全部硬中断。

部分由 `recover_and_start()`（:58，重启重投 backlog）兜底，故非 P1。但应做优雅收尾，避免 in-flight
task 与 BRPOP consumer 被硬杀。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `_amain` 用 asyncio 注册 SIGTERM/SIGINT handler，set stop event 替代裸 Event().wait()
- [ ] #2 触发后 cancel 子 task、等当前节点协作式自停（复用现有 cancel flag 机制）再退出
- [ ] #3 实测 launchd restart 时 worker 优雅退出、无孤儿子进程；586 passed
- [ ] #4 不给 nof-worker 加 --reload（红线）；起停仍走 bin/install_nof_worker.sh
<!-- AC:END -->
