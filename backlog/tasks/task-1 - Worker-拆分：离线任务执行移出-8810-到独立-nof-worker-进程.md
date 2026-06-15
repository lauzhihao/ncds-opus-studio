---
id: task-1
title: Worker 拆分：离线任务执行移出 8810 到独立 nof-worker 进程
status: Done
assignee: []
created_date: '2026-06-15 02:38'
labels:
  - worker-split
  - architecture
  - backend
dependencies: []
priority: high
---

## Description

背景：8810 当前既是 FastAPI HTTP server 又是任务执行器——TaskRunner worker 协程 + subscription/retro/planner/sweeper loop 全在 app.py startup 用 asyncio.create_task 拉起，离线任务(asr/collect/rw/render)经 asyncio.to_thread 跑在进程内，队列是进程内存(重启即蒸发、靠 startup 恢复)。后果：改后端要重启、重启就打断在跑任务。目标：8810 退化为纯 HTTP(入队写 task_store + serve 状态/SSE)；新增 nof-worker 长驻进程执行任务，两进程共享磁盘 task_store + video-jobs；重启 8810 不碰在跑任务。已知：状态真相源已文件级(video-jobs 下 pipeline_state.json)，但 SSE 走内存 pub/sub(pipeline_runner EventBus + asyncio.Queue)是主解耦缺口。权威设计：docs/PRODUCTION-ENGINE-DESIGN.md E3 贵步骤后台派发、WOLONG-DESIGN 第2节(当初为可见/可取消/SSE 才收进 in-server，别退回 fire-and-forget CLI 子进程，见 WOLONG-DESIGN 第168行)。迁移分 S1-S4 子任务，每阶段可独立跑/验证，先做 S1。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 子任务 S1-S4 全部 Done（S1 SSE 落盘 / S2 cancel 跨进程 / S3 Redis 队列+worker 拆分 / S4 launchd 托管+文档+激活）
- [x] #2 端到端：起 8810 + nof-worker，发采集任务，重启 8810，任务继续且 SSE 重连可续看进度（S3 步8 真 redis 三进程 e2e 实证：kill -TERM 8810 后任务 2.4s 才完成、worker 不中断；重启 8810 GET 状态 completed + SSE since_seq 重放）
- [x] #3 全程 pytest 绿（495 passed 两路核对）；现有 web /jobs 的 SSE 契约不变（wire `{type,job_id,node,state}` 由 S1 保留，S2-S9 未碰，前端不改）
<!-- AC:END -->
