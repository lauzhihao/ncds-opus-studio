---
id: task-3.9
title: nof-worker graceful shutdown
status: Done
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
- [x] #1 `_amain` 用 asyncio 注册 SIGTERM/SIGINT handler，set stop event 替代裸 Event().wait()
- [x] #2 触发后 cancel 子 task、等当前节点协作式自停（复用现有 cancel flag 机制）再退出
- [x] #3 实测 launchd restart 时 worker 优雅退出、无孤儿子进程；589 passed
- [x] #4 不给 nof-worker 加 --reload（红线）；起停仍走 bin/install_nof_worker.sh
<!-- AC:END -->

## 完成记录

- 2026-06-22：`server/worker.py` 改为 SIGTERM/SIGINT -> stop event；退出时先 cancel 后台 loop，再调用 `RUNNER.shutdown()`，最后清理 signal handler。
- 2026-06-22：`TaskRunner.shutdown()` 增加停机请求与 grace wait；工作线程里的 `cancel.current()` 会同时感知用户取消和 worker 停机。停机触发的 `TaskCancelled` 不把任务标 failed/cancelled，保持 `running` 交给下次 `recover_and_start()` 复位重投。
- 2026-06-22：补单测覆盖 signal handler、`_amain` 收尾、running task 协作式停机语义。
- launchd 实测：使用临时 label `tech.ncds.opus-factory.nof-worker-codex-test` + 隔离 `NOF_STATE_DIR` 跑 `kickstart -k`，日志确认 `ready -> received SIGTERM -> stopping background loops -> stopping task runner -> stopped -> ready`。真实 `tech.ncds.opus-factory.nof-worker` 未重启，因为当前真实 `state/tasks` 仍有旧 `shenkuo/wst` pending/running，直接 restart 会 recover 重投真实任务。
- 验证：`python3 -m py_compile ...`、`pytest src/ncds_opus_factory/server/worker_test.py tests/server/test_scheduler.py -q`、`git diff --check`、全量 `.venv/bin/python3 -m pytest -q` 均通过；全量结果 `589 passed, 173 warnings in 30.55s`。
