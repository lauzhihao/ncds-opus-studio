---
id: task-1.3
title: S3：抽 nof-worker 独立进程，8810 只入队+serve
status: To Do
assignee: []
created_date: '2026-06-15 02:39'
labels:
  - worker-split
  - backend
  - process
dependencies:
  - task-1.1
  - task-1.2
parent_task_id: task-1
priority: high
---

## Description

父任务 task-1，依赖 S1(task-1.1 事件源落盘)+S2(task-1.2 cancel 跨进程)。新建 server/worker.py + pyproject script nof-worker。把 TaskRunner worker 消费协程 + subscription/retro/planner/sweeper loop 从 app.py startup 挪进 nof-worker；8810 startup 不再拉 worker，只保留入队(写 task_store)+读状态+SSE(tail events.jsonl)。两进程各建 RUNNER/STORE、共享 NOF_STATE_DIR 下 task_store 与 video-jobs。约定：状态由 worker 单写、HTTP 只入队(文件锁或单写者契约防竞态)。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 nof-worker 独立进程能消费队列、执行 pipeline、写 events.jsonl/pipeline_state.json
- [ ] #2 8810 不执行任务；kill -TERM 重启 8810 时 nof-worker 在跑任务不中断
- [ ] #3 端到端：起 8810+nof-worker、发采集任务、重启 8810、任务继续且 SSE 重连续看进度
- [ ] #4 task_store 并发写无竞态(worker 单写状态/HTTP 入队)；pytest 绿
<!-- AC:END -->

## Implementation Plan

新 server/worker.py 复用 task_runner 的 worker 消费 + app.py 那批 create_task loop；app.py startup 去掉这些；pyproject [project.scripts] 加 nof-worker = ...server.worker:main。RUNNER/STORE 两进程各自构建指向同一磁盘。
