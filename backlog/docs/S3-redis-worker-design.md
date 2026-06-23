# S3 设计：Redis 队列 + worker 拆分（权威实现依据）

> 来源：2026-06-15 设计工作流(3 独立方案 → 对抗压测 7 项 → 综合)。父任务 task-1，子任务 task-1.3。
> **2026-06-23 修订**：旧口径“Redis 只做队列信道、磁盘是真相源、worker 启动先 DEL 队列再 disk-scan”已废弃。最新实现以 Redis 作为任务中间件与执行协调中心：`nof-server` 登记 Redis task hash 并 LPUSH，`nof-worker` 通过 Redis claim 原子领取，成功/失败/取消都回写 Redis；TaskStore/events.jsonl 继续作为 UI/SSE 的持久视图。

## 范围（用户拍板 2026-06-15）= scope (a)
**只把 TaskRunner 类任务**(shenkuo/wolong/guiguzi/liuyong 经 POST /tasks)移到 nof-worker。
**PipelineRunner.run_node / InstanceRunner.run_step(web 画布 + 015 引擎)执行暂留 8810**，迁移单列 S3.x。
- AC#2 收窄为：**「TaskRunner 类任务不在 8810 执行」**(画布/引擎仍在 8810，是 scope a 的已知代价)。
- 连带：AC#5 cancel 接线落在「执行所在进程」=画布/引擎在 8810，故 cancel 接线本期在 8810 的 pipeline_runner（flag 是文件信号、S3.x 迁 worker 时天然可达，不返工）。
- S3.x 前置硬骨头：迁 engine 执行前必须先把 `InstanceRunner.EngineEventBus`(instance_runner.py:56 进程内 asyncio.Queue)改成 events.jsonl 文件 tail，否则 8810 收不到 worker 的 _emit、/instances/*/events 全空。本期不做、标注待办。

## 决策 A–G

| 分叉 | 决策 |
|---|---|
| **A. Redis 结构** | per-cmd **List**：key=`nof:q:{cmd}`，value=task_id 裸串。**LPUSH 入 + BRPOP 出 = FIFO**(旧的先跑)。另有 task hash：key=`nof:task:{task_id}`，记录 `cmd/status/updated_at` 等协调状态；`nof:tasks` 作为恢复扫描 index。严禁 LPUSH+BLPOP(LIFO)。 |
| **B. 配额** | 真相源换 **Redis 原子计数**，判定收口到 worker 出队侧单点。submit 删配额闸门段(task_runner.py:182-192)+内存 `_quota_used`/`_quota_take`；`_run` 出队 CAS 后、`update_status('running')` 前：`incr(nof:quota:{date}:{bucket})`+首次 `expire(48h)`，>limit 则 `decr` 回退+failed+auto_archive。`quota_remaining` 改读 Redis(降级为软预查)。删 recover 的配额重建段(237-241)。 |
| **C. recover** | 整体留 worker(8810 不调)。worker 启动**不 DEL Redis 队列**：先补登记旧磁盘任务到 Redis task hash；再扫描 Redis task index，把 `pending/running` 未终态任务补投队列，其中 `running` 视为上个 worker 孤儿，复位为 `pending`。List 里残留/重复 task_id 允许存在，由 Redis claim 保证只执行一次。 |
| **D. 降级** | 8810 入队失败 → meta 置 failed + **POST 返 503+task_id**(不返 201 撒谎、不留幽灵 pending)。worker 侧 loop(subscription/planner/retro/gate)的 submit **先 `redis.ping()` 探活，失败则本 tick 直接 return**(对齐 planner「静默空转」，不 create meta、不刷垃圾 failed)。worker BRPOP 带 timeout + ConnectionError 指数退避(0.5→5s)不退出 + CancelledError 时 aclose 归还连接。 |
| **E. 幂等** | 4 层叠加：①submit 头 (round_id,intent_key) 去重扫 store；②wolong `round_<task_id>` 确定化；③Redis `claim_task` 做 `pending→running` 原子领取；④recover 只补投不清队列，重复条目由 claim 跳过。顺序约束：`dedup 扫 store(同步)→ store.create(同步)→ Redis register_task → lpush`，严禁把 await 插到 create 之前。 |
| **F. cancel(AC#5)** | 见下「必修 blockers」。统一开关 `cancel.install(读 is_flagged)`，覆盖 engine+legacy+enrich + killpg 进程组 + TaskCancelled 异常分类修复 + cancel_node 连带 enrich。本期接线在 8810(执行所在进程)。 |
| **G. 进程** | 8810=纯 producer+serve(删 recover_and_start + 5 个 loop create_task + on_terminal 赋值；保留路由/SSE 文件 tail/POST submit+503)。新建 `server/worker.py`(`nof-worker`)=唯一 consumer + 全部 loop + on_terminal。两进程各 import state.py 各建一套指同一磁盘+各连 Redis。 |

## 必修 blockers（对抗压测判定，落地必须处理）
1. **多 worker 仍是业务禁区**：任务领取 CAS 已换 Redis claim，单 task 不会双跑；但 wolong/round 文件串行写、命令级并发预算和外部账号池仍按单 `nof-worker` 运行假设设计。S3 硬钳「同 cmd 单 worker 进程」(启动脚本 pid 锁)，水平扩展需另做 round 分布式锁与命令级资源隔离。
2. **submit create-before-lpush 顺序**(E 的死约束)：插错 await 顺序 → wolong 续跑双投。8810(review/cancel→handle_decision→maybe_resume→submit) 与 worker(on_terminal→maybe_resume) **两侧都是续跑生产者**，顺序约束两侧都要成立。
3. **TaskCancelled 异常分类**(最隐蔽)：`TaskCancelled` 继承 `RuntimeError`，被 `pipeline_runner._execute` 的 `except Exception` 误判 failed 且不 clear_flag → 残留 flag 让节点重跑被 watcher 秒取消 → **永久 DOA**。必修：`except Exception` 之前先 `except TaskCancelled` → 按 cancelled(reset+clear_flag)。engine `_run_step` 同理。
4. **子进程进程组 SIGTERM**：`_run_tts_gen`(2172)/`_run_video_pipeline`(2221) 的 Popen 加 `start_new_session=True`，readline 循环轮询 `cancel.current()` 命中则 `os.killpg(os.getpgid(proc.pid), SIGTERM)`→wait(5)→killpg(SIGKILL)。video_pipeline 内部 spawn yt-dlp/ffmpeg/whisper/Demucs 孙进程，`proc.terminate()` 只杀直接子进程会留孤儿烧 1h。
5. **engine 不可取消**：`instance_runner._invoke`(48) 无 cancel_check 形参——015 链完全不可取消。加形参 + run_step 调用处传 `lambda: is_flagged(flag)`。
6. **is_inflight 跨进程**：worker `_run` 进出 SADD/SREM `nof:inflight`，`is_inflight` 改 SISMEMBER。否决「8810 不判 inflight」(否则 restore 对仍在 worker 执行中的任务永远放行 → 双线程踩同一任务)。

## 实施进度（滚动更新）
- ✅ **步0** 依赖+配置(redis>=5/fakeredis，REDIS_URL 入 .env.example) —— 全量绿，零行为变更
- ✅ **步1** `server/queue.py`(async Redis 封装：lpush/brpop/delete/incr_quota/get_quota/ping)+ 14 测试 —— 主线程验收(修了 brpop 退避死代码 → 真指数)
- ✅ **步2** 配额下沉 Redis incr(判定挪到 `_run` 出队 CAS 之后)+ `quota_remaining` async(6 处 await)+ conftest fakeredis 注入 + 3 测试 —— 验收(incr 在 CAS 之后，不误扣)。delta：restore 现在每次执行扣配额(原 submit 时扣)，更合理、wolong restore 本禁。
- ✅ **步3** 队列切 Redis List(`_enqueue`→lpush / `_worker`→brpop / `_queues` 删)+ `recover_and_start` async(先 DEL 旧 List + 磁盘 pending 全量重投，app.py caller 加 await)+ 2 防双投测试 —— 验收(两 blocker 守护：submit create-before-await-lpush、DEL-before-reinvest 有 llen 直接断言)。当前单进程仍可跑(workers 在 recover 内起)，进程拆分见步5-6。
- ✅ **步4** AC#5 取消接线（Option A 协作式为主）：`_run_in_thread_cancellable` 装 checker + `_execute`/`_run_step` 加 `except TaskCancelled` + `cancel_node` 不 task.cancel 真实节点&连带 cancel enrich + watcher 宽限期 + collect/enrich per-item 不吞 TaskCancelled + 引擎 `_invoke/run_step` cancel_check + `tts_gen/video_pipeline` killpg 进程组 + 重跑前清残留 flag —— 主线程验收（抓到并修复 watcher 残留 `for t in pending: t.cancel()` 抢杀 inner 致孤儿的真 bug，补协作式透传强测试）。全量 **480 passed**。决策 flag 生命周期=Option A（详见 task-1.3 步4 验收注）。收尾已收 3 项（legacy `_execute_asr` 装 checker、cancel_node 幽灵路径顺序、真孙进程树 killpg 测试）；仅剩 enrich 真 Demucs 端到端终止留步8-9。
- ✅ **步5** `server/worker.py`(nof-worker 入口) + `server/maintenance.py`(抽 `_discard_sweeper`/`_round_reconciler`/sweep helpers + 常量，app.py 改 import、startup create_task 不动；worker 不必 import FastAPI app）+ pyproject `nof-worker` script。main()=ping redis(fail-fast)→on_terminal→recover_and_start(内部起 BRPOP consumer)→5 loop→`Event().wait()`。主线程验收：全量 **484 passed**(两路) + **真 redis 端到端**(起 redis+nof-worker 不起 8810→`RUNNER.submit("liuyong")` LPUSH→worker ping OK→recover→BRPOP 消费→mock 执行→磁盘 completed、队列 llen=0)。delta：maintenance 用延迟 `_store()`/`_labels()`(test_labels reload state 但不 reload maintenance，顶层 import 会持旧单例)，已核实必要且正确；app.py re-export sweep/backfill 给 test_labels。⚠️ 步5→6 切换窗口别同时跑 8810+worker(双 consumer 踩 CAS)。
- ✅ **步6** 8810 瘦身(切换点)：app.py `_startup_log` 删 `on_terminal`/`recover_and_start`/5 个 `create_task`，只留 "[nof-server] ready" log + 清 8 个 unused import(保留 maintenance re-export 给 test_labels)。8810 退成纯 producer+serve(入队+SSE+状态)，不消费/不起 loop。做完 8810 与 nof-worker 可同跑(8810 唯一 producer，worker 唯一 consumer)。代码审查确证(纯删除，无残留消费路径)+ 484 passed；行为级 e2e 并入步8。
- ✅ **步7** inflight 跨进程(blocker#6)：queue.py 加 `add_inflight`/`remove_inflight`/`is_inflight`/`clear_inflight`(key=`nof:inflight`，SADD/SREM/SISMEMBER/DEL)；task_runner 删内存 `_inflight` set，`_run` 进出改 Redis、`is_inflight` 改 async、`recover_and_start` 加 `clear_inflight`(清崩溃残留防永久 409)；routes/tasks.py restore 调用处 + test_scheduler `await` 化。主线程验收：484 passed(两路) + **真 redis 跨进程证明**(进程 A `add_inflight`→redis-cli 第三客户端 SISMEMBER=1 + 进程 B `is_inflight`=True、remove 后 False)。关键核查：全仓 is_inflight 调用方都已 await(无漏 await 致协程恒真永久 409 的坑)。
- ✅ **步8** 端到端 AC#1-3(真 redis+8810+nof-worker 三进程,委派 sonnet 跑 e2e、主线程读磁盘独立核验)：起 ad-hoc redis(6399)+8810(测试端口 8899)+worker→POST /tasks 入队→worker 消费 running→**`kill -TERM` 8810(running 期间)→worker pid 仍存活→任务在 8810 退出后 2.4s 才 completed**(磁盘 meta finished=17:25:27 vs 8810 退出 17:25:24,铁证 AC#2 不中断)→重启 8810→GET /tasks/{id}=completed + SSE 端点回放(AC#3)。主线程独立核验:直读保留的 tmp 磁盘 meta(status=completed/started/finished 时间线)+events.jsonl 8 条,与 sonnet 自报一致。
  - ⚠️ **步8 发现真 bug(归步9 修)**：`queue.py brpop` 的 `except (RedisConnectionError, RedisError)` 把**正常 brpop 超时(socket Timeout 空轮询)**也当连接故障→idle 每 ~5s 刷 WARNING + 累加退避(钉到 5s),致 idle 后新任务最多多等 ~5s 取件。不影响正确性。修法:`except RedisTimeoutError` 单独分支当正常空轮询(重置退避/不告警),只有真 ConnectionError 才退避+告警。与步9 降级(真断线退避)同源,一起修+测。
- ✅ **步9** 降级(委派 sonnet + 主线程验收)：① **POST 503**——`submit`/`requeue` 的 `await _enqueue(lpush)` 套 try，连不上→`update_status(failed)`+`raise EnqueueUnavailable(task_id)`；`routes/tasks.py create_task` 捕获→`response.status_code=503`+返 `TaskCreateResponse(task_id, "failed")`(不撒谎 201、不留幽灵 pending)。② **loop ping 探活**——subscription/planner/retro/`_round_reconciler` 4 个 loop while 顶部 `if not await get_default_queue().ping(): sleep+continue`(redis down 静默空转、不刷垃圾 failed、不退出)。③ **修步8 brpop bug**——`except RedisTimeoutError`(正常空轮询:重置退避+return None+不告警)放 broad `except (ConnectionError,RedisError)` 之前(后者才退避+WARNING)。验收:495 passed 两路 + 真 redis 降级 e2e(停 redis→POST **503**+磁盘 meta **failed**；重启→201/pending)。socket_timeout 发现:RedisQueue 未显式设，但 redis-py asyncio 池在某些版本隐式读超时致 brpop 抛 RedisTimeoutError(步8 现象根因)，已被新分支精确截获。
- ✅ **步10** 全量回归 **495 passed**(两路)+ web `/jobs` SSE 契约确认不变(路由 pipelines.py:393 从 events.jsonl tail + `since_seq` 重放；types.ts:263-265 wire `{type,job_id,node,state}`，S1 已按此保留，S2-S9 未碰 SSE 格式 → 前端不改，task-1 AC#3 满足)+ AC#4 确认(TaskStore meta `tmp+os.replace` 原子写、worker 单写 status/8810 只 create，无竞态)+ S4 交接固化(见 task-1.4「S3→S4 交接」段)。**S3(步0-10)完成,task-1.3 标 Done。** 注：line 389 一处陈旧注释("q.get()")待随手改 brpop(非阻塞，留 S4 顺手)。
- ✅ **S3.1 运行时安全修订（2026-06-23）**：Redis 从“队列信道”升级为“队列 + task hash + claim + terminal 状态”协调中心；`recover_and_start` 不再 DEL Redis List，也不再 `clear_inflight` 全局清场；server `submit/requeue/cancel`、pipeline cancel mirror、round cleanup cancel 均写 Redis 状态；worker `_run` 通过 Redis claim 领取任务并在 completed/failed/cancelled 回写 Redis。focused 验收：`queue_test.py`、`task_runner_quota_test.py`、`tests/server/test_scheduler.py`、`degradation_test.py` 共 48 passed。

---
**S3 完结小结**：8810 退成纯 producer+serve(入队+SSE)，`nof-worker` 独立进程唯一消费+执行+单写状态，跨进程 Redis 队列/配额/inflight，协作式取消(killpg 进程组)，降级(503/ping 空转/退避不退出)。重启 8810 不再打断在跑任务(执行已在 worker，磁盘时间戳实证 kill 8810 后 2.4s 任务才完成)。**剩 S4(task-1.4)=launchd 常驻托管，涉及注册持久服务，须先问用户。**

## 有序迁移步（每步独立可验证）
- **步0 依赖+配置**：`uv pip install --python .venv/bin/python3 redis fakeredis`；pyproject deps 加 `redis>=5`、test 加 fakeredis；.env/.env.example 加 `REDIS_URL=redis://127.0.0.1:6379`。验：import redis + ping True；现有 pytest 全绿(零行为变更)。
- **步1 server/queue.py(纯加法)**：async redis 封装 get_client/lpush/brpop(带重连退避)/delete/incr_quota/get_quota/ping/aclose；连接异常显式抛。queue_test.py(fakeredis)：往返/配额原子 incr+expire+越界 decr/delete/ConnectionError 抛。
- **步2 配额下沉(行为等价)**：submit 删配额闸门+内存计数；`_run` 出队 CAS 后加 Redis incr 判定+decr 回退+failed+auto_archive；quota_remaining 改读 Redis。改 task_runner_test：超额仍 failed(时机从 submit 变 _run)、跨两实例 quota 一致。
- **步3 _enqueue 切 Redis + recover 防双投**：`_enqueue` 改 `await queue.lpush`(submit 顺序钉死 create→await lpush)；`_worker` 的 q.get 改 brpop(带重连)；recover 第一步 DEL 所有 List + 删配额重建段 + pending/running 全量 reversed LPUSH。更新 planner/rounds_gate 注释。单测：孤儿重投一次、List 残留+disk-scan 每 task_id 副作用恰好 1。
- **步4 AC#5 取消接线(与队列解耦)**：抽 `_run_in_thread_cancellable(fn,flag_path,*a)` 包 `_execute_via_engine`/`_execute_asr_collect`(1534)/legacy `_execute_asr`(1605)/`_enrich_asr_collected`(1489)；`_run_tts_gen`/`_run_video_pipeline` 加 start_new_session+killpg 轮询；`_execute` except 链加 `except TaskCancelled`(在 except Exception 前)；`instance_runner._invoke` 加 cancel_check + run_step 传 lambda；`cancel_node`(1102) 连带 cancel `_enrich_tasks[job_id]`。测：mock 带孙进程 Popen→set flag→killpg 整组、节点回 idle、enrich 停、flag clear、重跑不 DOA。
- **步5 server/worker.py(nof-worker 入口)**：main() asyncio.run——import state 单例+连 Redis+ping、on_terminal=rounds_gate.handle_terminal、recover_and_start、create_task 五 loop、`await asyncio.Event().wait()`。pyproject [project.scripts] 加 nof-worker。验：起 redis+nof-worker(不起 8810)，手动 LPUSH 预置 pending task_id→worker 接手执行+写 events/state+auto_archive。
- **步6 8810 瘦身(切换点)**：app.py startup 删 recover_and_start(298)+五个 create_task(301-317)+on_terminal(296)；loop 定义体搬 worker.py 或留 app 由 worker import；loop submit 前加 ping 探活。验：单起 8810→POST /tasks 返 meta pending+Redis 有 task_id 但不执行；GET events 仍能 tail(SSE 纯文件)。
- **步7 restore/inflight 跨进程**：worker _run SADD/SREM nof:inflight；is_inflight 改 SISMEMBER。验：取消 running 后立即 restore 返 409(worker 在途)，收尾后成功。
- **步8 端到端 AC#1-3**：redis+8810+nof-worker 全起→POST 采集→worker 执行+8810 SSE 看进度→`kill -TERM` 8810→worker 不中断→重启 8810→SSE 重连续看到 done。
- **步9 降级验证**：停 redis→POST 返 503+meta failed、worker loop 静默空转不刷 failed、BRPOP 退避不退出；启 redis→recover DEL 全量重投补回继续跑。
- **步10 全量回归 + 交接 S4**：pytest 全绿；web /jobs 与 /tasks SSE 契约不变(前端不改)。S4：launchd 托管 nof-worker + redis-server 常驻(启动顺序)+文档(Redis 硬依赖/降级/多 worker 禁区/启停/AC#2 收窄口径)+更新 nof-server-restart 记忆。

## 测试计划（关键场景）
- 跨进程配额：两个共享 fakeredis 的 RUNNER，A 提交到桶满 B 再提交，总执行=limit(不翻倍)；并发 _run 同桶 incr 原子无超发。
- 崩溃在途：孤儿 running→recover 重新执行恰好一次(副作用计数器)；BRPOP 弹出后置 running 前 kill→recover 全量重投救活；List 残留+disk-scan→每 task_id 恰好一次(CAS 兜底)。
- 降级：停 redis→POST 503+meta failed；worker loop ping 失败不 create meta；BRPOP 退避不退出。
- cancel AC#5：set flag→to_thread checkpoint 抛 TaskCancelled→节点 idle+flag clear；带孙进程 Popen→killpg 整组(ps 无残留)；TaskCancelled 走专门分支(不 DOA)；cancel_node 连带 enrich；engine _invoke 可取消。
- wolong 不双投：并发同 round maybe_resume(handle_terminal 与 reconcile 撞车)→同 round gate 在途≤1；验证 create 在 await lpush 之前。
- restore/inflight 跨进程：取消后 restore 返 409，收尾后成功。
- AC#4 并发写：8810 store.create 新 task 与 worker update_status 旧 task 同时→无半写/串档。

## 风险/禁区（写进 S4 文档）
1. 多 worker 禁区(CAS/wolong 协程级)。2. submit create-before-lpush(8810/worker 两侧)。3. engine SSE 跨进程(S3.x 前置=EngineEventBus 改文件 tail)。4. Redis 硬依赖+启动顺序(S4 launchd)。5. 子进程组 killpg 彻底性(ps 验证无孤儿)。6. redis.asyncio 连接在 CancelledError 时 aclose 防泄漏。
