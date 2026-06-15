---
id: task-1.2
title: S2：cancel 落盘化(跨进程文件标记)
status: Done
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
- [x] #1 取消 running 节点：asyncio 层 cancel(watcher 轮询 flag)+ 写跨进程 flag + 状态置 cancelled（**spawn 子进程 SIGTERM 部分移交 S3，见下决策**）
- [x] #2 跨进程验证：A 进程写标记、B 进程读到(multiprocessing spawn 子进程实测)
- [x] #3 现有 cancel 单测绿 + 新增跨进程取消单测(cancel_flag_test.py + cancel_node_test.py)
<!-- AC:END -->

> **验收 + 决策(2026-06-15, ultrareview 复核后)**：S2 交付并验收 ——「跨进程 flag 原语(core/cancel.py `set_flag`/`is_flagged`/`clear_flag`)+ `cancel_node` 写 flag + watcher 异步层取消(`_execute_real_with_flag_watcher`，并发正确无 task 泄漏)+ 跨进程可见性测试(multiprocessing spawn)」。
> **AC#1 的『spawn 子进程(Demucs/ffmpeg)被 SIGTERM』显式移交 S3**(用户拍板)：当前 flag 写了但无 checker 读 flag 去 SIGTERM 子进程，且 `cancel_node` 未连带 cancel 后台 enrich task。缺口覆盖 engine 路径 + legacy(`_execute_asr`/`_collect_one`) + enrich(真 spawn Demucs) + `tts_gen`/`video_pipeline` 多处 to_thread/Popen —— 这些接线统一在 S3 worker 重构时一次接全(to_thread 边界 install 读 `is_flagged` 的 checker + Popen 轮询 + cancel 连带 enrich)，避免 S2 接一半 S3 重做。详见 task-1.3 AC#5。

## Implementation Plan

common/cancel.py 统一文件标记 API(写/查/清)；pipeline_runner 各 guard 点 + 子进程轮询读标记；routes cancel handler 改写文件而非操作内存 _running_nodes。
