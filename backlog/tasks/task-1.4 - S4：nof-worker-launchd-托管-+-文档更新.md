---
id: task-1.4
title: S4：nof-worker launchd 托管 + 文档更新
status: Done
assignee: []
created_date: '2026-06-15 02:39'
labels:
  - worker-split
  - docs
  - ops
dependencies:
  - task-1.3
parent_task_id: task-1
priority: medium
---

## Description

父任务 task-1，依赖 S3(task-1.3)。让 nof-worker 常驻、与 8810 解耦、开机自启，对标 map_watchdog 的 launchd 方式。写 launchd plist + scripts/install_nof_worker.sh(install/status/logs/restart/uninstall，参考 scripts/install_map_watchdog.sh)；更新文档。

> **决策更新(2026-06-15)**：S3 队列改用 Redis(见 task-1.3)，故 nof-worker 启动**依赖 Redis 已就绪**。S4 需保证启动顺序：launchd 让 Redis 先于 nof-worker(`brew services start redis` 或单独 plist + 文档化依赖)，或 nof-worker 启动时做 Redis 连通性自检+重试。文档的"本地运行"章节除 nof-server(HTTP)+nof-worker(执行)外，新增 Redis 启停说明。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 scripts/install_nof_worker.sh install/status/logs/restart/uninstall 可用、开机自启 —— **脚本已写+验正确;用户授权后(2026-06-15,dev 机)已实际 `install` 注册成功:launchctl `state=running`,worker 日志 "ready. redis OK",RunAtLoad 开机自启生效**
- [x] #2 docs 更新：本地运行章节从单 nof-server 改为 nof-server(HTTP)+nof-worker(执行)含启停；PRODUCTION-ENGINE-DESIGN 标注落地
<!-- AC:END -->

> **S4 交付状态(2026-06-15,委派 haiku×2 + 主线程验收)**：
> - ✅ **写文件部分完成**：① `scripts/install_nof_worker.sh`(复制 install_map_watchdog.sh 改 LABEL/PYTHON_BIN=venv/ProgramArguments=`-m ncds_opus_factory.server.worker`/日志名;保留 WorkingDirectory/RunAtLoad/KeepAlive/PATH;bash -n 过、chmod +x)；② 三文档(CLAUDE.md §9 / docs/README.md / PRODUCTION-ENGINE-DESIGN.md)加三进程(redis+nof-server+nof-worker)启停说明，只加不删；③ 主线程顺带补 `worker.py` 顶部 load `.env`(launchd 不继承 shell env，与 app.py 同款，ordering 敏感故主线程做)。全量 495 passed。
> - ✅ **激活已完成**(2026-06-15，用户授权"开发机随便搞"后主线程执行的有序迁移)：① 清场——3 个种子任务(t_mock_wd_pend01/t_mock_sk_run01/t_demo_running_c)标 cancelled，免 recover 误执行；② `brew services start redis`(常驻，label homebrew.mxcl.redis)；③ 停旧 :8810(pid 37442，6h 旧代码内存队列)+ nohup 重起当前代码 nof-server(纯 producer，pid 57328，startup 仅 ready 无 recover/loop)；④ `install_nof_worker.sh install` 注册 launchd nof-worker(state=running，"ready. redis OK")。**活体冒烟**:真 :8810 POST liuyong → HTTP 201 入队 redis → worker 消费 → completed，链路通。
> - **遗留**：nof-server(57328)是 nohup 起的(脱离会话、存活)，**非 launchd 托管**——若要 nof-server 也开机自启需另写 plist(S4 范围只含 nof-worker，design 决策 G：8810 是 serve、由用户/独立 launchd 管)。`docs/S3-redis-worker-design.md` line~389 陈旧注释("q.get()")仍待随手清。

## Implementation Plan

复制改造 install_map_watchdog.sh；plist 跑 .venv/bin/nof-worker、日志落 state/；文档改 docs/README、PRODUCTION-ENGINE-DESIGN、CLAUDE.md 第9节。

## S3→S4 交接（2026-06-15，S3 步0-10 完成后固化）

S3 已落地：Redis 队列 + 配额下沉 + 取消接线 + `nof-worker`(server/worker.py) + 8810 瘦身成纯 producer + inflight 跨进程 + 降级。全量 **495 passed**。S4 文档/launchd 必须带上以下 S3 期间锁定的口径：

- **Redis 硬依赖 + 启动顺序**：nof-worker 启动先 `ping` 探活，失败 **fail-fast 退出**(不静默吞任务)。故 launchd 必须保证 **redis 先于 nof-worker 就绪**——redis 用 `brew services` 或独立 plist 常驻，nof-worker 的 plist 标注依赖/或靠 launchd KeepAlive 重启兜启动竞速。本机已装 redis-server(homebrew)，S3 期间端到端验证用的是 **ad-hoc 临时进程**(`redis-server --port ...`)，**常驻注册是 S4 的活**。
- **启停三件套**：`redis`(队列信道) + `nof-server`(:8810 HTTP/SSE/入队，`cli_main`) + `nof-worker`(执行，`server.worker:main`)。三者各 import state 各指同一磁盘 + 各连同一 Redis。文档"本地运行"章节要从"单 nof-server"改成这三件。
- **多 worker 禁区**：**同 cmd 只能单个 nof-worker 进程**(出队 CAS 与 wolong concurrency=1 是协程级单进程原子、跨进程不成立)。S4 启动脚本应有 pid 锁防重复起。水平扩展需先把 CAS 换 Redis Lua/分布式锁 + wolong `SET NX`——**本期禁止起多 worker**。
- **降级行为(给运维/文档)**：redis 挂时 → 8810 `POST /tasks` 返 **503 + meta failed**(不留幽灵 pending)；worker 4 个 loop **ping 探活静默空转**(不刷垃圾 failed)；worker `brpop` **指数退避不退出进程**；redis 恢复后 worker `recover_and_start` DEL 旧队列 + 磁盘 pending 全量重投补回。
- **AC#2 收窄口径(scope a)**：S3 只把 **TaskRunner 类 agent 任务**(shenkuo/wolong/liuyong/guiguzi 经 POST /tasks)移到 worker；**web 画布 run_node + final_preview 引擎 run_step 执行仍在 8810**(S3.x 迁，前置=`InstanceRunner.EngineEventBus` 改 events.jsonl 文件 tail)。文档别把"8810 完全不执行任何东西"写绝对。
- **更新记忆 [[nof-server-restart]]**：S3 后"重启 8810 不再打断在跑任务"(执行已在 nof-worker)；但**重启 8810 不影响 worker、重启/改 worker 才会动在跑任务**——重启 worker 前仍要自检无在跑 job。
- 设计文档 `BACKLOG/docs/S3-redis-worker-design.md` 有一处陈旧注释(line ~389 "q.get()")待随手改 brpop（非阻塞，S4 或顺手清）。
