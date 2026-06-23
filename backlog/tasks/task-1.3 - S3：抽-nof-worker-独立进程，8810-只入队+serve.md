---
id: task-1.3
title: S3：抽 nof-worker 独立进程，8810 只入队+serve
status: Done
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

父任务 task-1，依赖 S1(task-1.1 事件源落盘)+S2(task-1.2 cancel 跨进程)。新建 server/worker.py + pyproject script nof-worker。把 TaskRunner worker 消费协程 + subscription/retro/planner/sweeper loop 从 app.py startup 挪进 nof-worker；8810 startup 不再拉 worker，只保留入队(写 task_store)+读状态+SSE(tail events.jsonl)。两进程各建 RUNNER/STORE、共享 NOF_STATE_DIR 下 task_store 与 video-jobs。约定：状态由 worker 单写、HTTP 只入队。

> **决策更新(2026-06-15)**：跨进程**任务队列改用 Redis**(用户拍板：仅 S3 队列引入 Redis；S1 事件 events.jsonl / S2 取消 flag 保持文件不变)。即"入队/出队"这条 IPC 走 Redis —— HTTP 端 LPUSH 派单、nof-worker 端 BRPOP 阻塞消费、防重领靠 Redis 原子语义，取代原计划的"写 task_store + 文件锁/单写者轮询"。本机已装 redis-server(homebrew)但当前未常驻——见 S4。
>
> **安全修订(2026-06-23)**：上一段中的旧口径“状态真相源仍是文件、Redis 只承载待办队列”已作废。最新实现以 Redis 作为任务中间件与执行协调中心：server 登记 `nof:task:{task_id}` 并 LPUSH，worker 用 Redis claim 原子领取，completed/failed/cancelled 均回写 Redis；TaskStore/events.jsonl 保留为 UI/SSE 持久视图。worker 启动不再 DEL Redis 队列，Redis 不重启时任务调度状态应连续安全。
>
> **完整设计(决策 A-G + 6 个必修 blocker + 11 步有序迁移 + 测试计划)见 [BACKLOG/docs/S3-redis-worker-design.md](../docs/S3-redis-worker-design.md)**。范围 **scope (a)**(用户拍板 2026-06-15)：只搬 TaskRunner 类任务，画布/引擎(run_node/run_step)留 8810、S3.x 迁(前置=EngineEventBus 改文件 tail)。AC#5 cancel 接线本期落 8810(执行所在进程，flag 文件信号 S3.x 迁 worker 不返工)。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 nof-worker 独立进程能消费队列、执行 pipeline、写 events.jsonl/pipeline_state.json
- [x] #2 **TaskRunner 类任务**不在 8810 执行(画布/引擎 run_node/run_step 暂留 8810、S3.x 迁，scope a)；kill -TERM 重启 8810 时 nof-worker 在跑的 agent 任务不中断
- [x] #3 端到端：起 8810+nof-worker、发采集任务、重启 8810、任务继续且 SSE 重连续看进度
- [x] #4 task_store 并发写无竞态(worker 单写状态/HTTP 入队)；pytest 绿
- [x] #5 (从 S2 移交)取消 running 节点时 spawn 子进程被 SIGTERM：worker 在节点执行的 to_thread 边界 install 读 `is_flagged(cancel_flag)` 的 checker，覆盖 engine + legacy(`_execute_asr`/`_collect_one`) + enrich；`tts_gen`/`video_pipeline` 的 Popen 循环轮询 flag 并 terminate；`cancel_node` 命中时连带 cancel `_enrich_tasks[job_id]`
<!-- AC:END -->

## Implementation Plan

新 server/worker.py 复用 task_runner 的 worker 消费 + app.py 那批 create_task loop；app.py startup 去掉这些；pyproject [project.scripts] 加 nof-worker = ...server.worker:main。RUNNER/STORE 两进程各自构建指向同一磁盘。

## 步4 验收（AC#5，2026-06-15，主线程亲验）

**已交付**：AC#5 取消接线全部落地（委派 sonnet 执行 + 主线程读码验收）。8 个接线点：
- `_run_in_thread_cancellable`（collect/enrich 装 checker）、`_execute` 加 `except TaskCancelled`、`cancel_node` Option A（不 task.cancel 真实节点 + 连带 cancel enrich）、watcher 宽限期、collect/enrich per-item 不吞 TaskCancelled、引擎 `_invoke/run_step` cancel_check + `_run_step` 取消回 idle 不 DOA、`tts_gen`/`video_pipeline` 的 `start_new_session`+killpg 进程组、`_execute_real` 重跑前清残留 flag。
- 全量 pytest **478 passed**（464 基线 + sonnet 13 + 主线程 1），collect/passed 两路一致。

**决策：flag 生命周期 = Option A「协作式为主」**（用户拍板）。cancel_node 只 set_flag（真实节点不 task.cancel）；watcher 命中 flag 后给宽限期（`_CANCEL_GRACE_SEC=15s`）等工作线程协作式自停（killpg/checkpoint 驱动，flag 存活到此刻），超时才 fallback inner.cancel()；flag 在 `_execute` 的 `except TaskCancelled` 分支（线程已停）才 clear。

**验收抓到 1 个真 bug（已修）**：sonnet 版 watcher 在宽限期逻辑前残留 `for t in pending: t.cancel()`，flag 命中时会抢先 cancel inner（抛弃工作线程）→ 宽限期成死代码 → 子进程仍变孤儿（Option A 失效）。这是「测试全绿但 AC 没满足」——原测试用无 checkpoint 的 fake 测不出。主线程修复（按"谁先完成"分别处理，flag 命中分支绝不 cancel inner）+ 补 `test_watcher_propagates_cooperative_task_cancelled`（用 to_thread 模拟真实线程，经"buggy 必 FAIL / 修复必 PASS"双向验证鉴别力）。

**收尾（2026-06-15，3 项已收掉）**：
1. ✅ legacy `_execute_asr` 已包 `_run_in_thread_cancellable`（video_pipeline 的 killpg 轮询变 live）+ polish/per-item 两处 `except` 前加 `except TaskCancelled: raise` + 直测 `test_execute_asr_legacy_raises_task_cancelled`。
2. ✅ `cancel_node` 幽灵路径顺序已统一（先 `_reset_node` 再覆写 error/finished_at）。
3. ✅ 补真孙进程树 killpg 测试 `test_terminate_proc_group_kills_grandchild`（sh 组长→sleep 孙进程，证明 killpg 整组杀孙进程，规避 terminate 只杀直接子进程的缺陷）。**仅剩** enrich 的真 Demucs 端到端终止留步8-9（需真子进程，单测已用机制组合覆盖）。

全量回归 **480 passed**（两路核对）。
