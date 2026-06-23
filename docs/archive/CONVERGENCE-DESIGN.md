# 收敛设计：卧龙 round 复用 015 画布引擎出片 + 闸2 终验

> 状态：**v1 PLAN（2026-06-13）。等用户 "Go" 再实施，本文档只产计划、不含已落地代码。**
> 产出方式：设计工作流（4 路接口查实 + 3 套方案 + 评审合成 + 3 视角对抗）。对抗发现的
> 2 个普适 BLOCKER + 1 个目标级 BLOCKER + 6 个 MAJOR 已折进 §2/§4/§5。三视角总评均为
> `needs-revision`（计划方向成立、需加固），非 `has-blockers`。
> 前置阅读：[WOLONG-DESIGN.md](../WOLONG-DESIGN.md)（round 编排 / 闸门 / 离线学习）、
> [FRONTEND-API.md](../FRONTEND-API.md)（各 agent 脾气 / artifact 渲染）。

---

## 0. 目标与红线

**目标**：卧龙 round 在**闸1（脚本验收）通过后**，复用 015 画布引擎（`pipeline_runner` 的
`lines/storyboard/tts/image` 出素材 + `render_final_preview` 出片）自动产出成片，并补**闸2（终验）**，
从而把此前游离在外的画布世界**纳入卧龙的质检 + 离线学习闭环**。

**不做**：不造新的拟人"出片 agent"；不走 009（吴道子/伯牙/render）那套；本期不物理删 009。

**红线（违反即收敛失败）**：
1. `render_final_preview` 终验 task 的 `source` **必须是字面量 `"wolong"`**，且**绝不**进
   `_UNGATED_ROUND_CMDS`、绝不设 `cron/retro/gate`——否则成片被自动归档，闸2 形同虚设（§2.3）。
2. 出片驱动**绝不**跑在 wolong worker 上（并发硬钳 1）——否则全系统 round 续跑饿死（§2.2）。
3. 闸1 过审的台词**必须原样进片**——否则闸2 审的不是闸1 审的东西，收敛前提不成立（§2.4）。

---

## 1. 背景：为什么要收敛

经勘探（13 agent 对抗核查，全部 CONFIRMED）确认：ncds-opus-factory 是**同一个 FastAPI
进程里两套物理隔离的并行子系统**：

| | web（/studio 画布） | app（iOS） |
|---|---|---|
| 引擎 | `PIPELINE_RUNNER`（逐节点手动跑 015 DAG） | `TaskRunner` + `rounds_gate`（静默执行 + 人工验收 + round 编排） |
| 真相源 | `video-jobs/{job}/pipeline_state.json` | `state/tasks/` + `state/wolong/` |
| 验收/闸门概念 | **完全没有** | 全套（闸1/闸2/案卷/rubric 学习） |

两套 runner 路由层零交叉；**分镜/配音/出图/渲染四步各有 015 与 009 两份不兼容实现**。
卧龙 round 目前只跑到柳永成稿（`stage="scripts"`）就落定，渲染段"留待后续接入"
（[wolong_rounds.py:17](../src/ncds_opus_factory/commands/wolong_rounds.py:17)）。

**最大代价**：画布出的片子不进待验收桶、不产 `reviewer=user` 标签，rubric 永远学不到画布侧
人工判断，两端审美越走越远。收敛即把画布引擎拉回闸门内。

---

## 2. 核心架构决策（已折入对抗修复）

### 2.1 round 主、015 job 从（素材厂）

卧龙 round 是主、015 pipeline job 是从。round 不把出片交给画布的手动流，而是**程序化驱动**
一个 015 job 当"素材厂"用：注入脚本 → 顺序跑 `lines→storyboard→tts→image` 出齐
`episode.json + scene-*.mp3 + 配图` → 再由 `render_final_preview` task 出片。

015 引擎关键事实（查实，[pipeline_runner.py](../src/ncds_opus_factory/server/pipeline_runner.py)）：
- `create_job` 只建状态不启动（:248）；唯一执行入口是逐节点 `run_node`（:384），**无整 job run**。
- `run_node` 有 **dep 闸**：所有 `deps` 节点 `status==done` 否则 `RuntimeError`（:400-405）。
- `run_node` 会**无条件 reset 自身 + BFS reset 全下游**（:408-411）——**绝不能回头 run 上游**。
- 节点间靠**磁盘**传产物，`02_rw/episode.json` 是中枢，`tts/image/render` 全部重读它、
  不读上游 `node.outputs`（:280）——所以**塞一份 episode.json 即可绕过 asr/rw/lines/storyboard**。
- 终态判定看 `render` 节点 `status==done`；`render` 产物固定落 `06_render/output.mp4`（:1688）。

### 2.2 JobDriver 跑在 server event loop，**不占 wolong worker**〔BLOCKER 修复〕

**问题**：wolong 并发被硬钳 1 且 env 不放行（[task_runner.py:124](../src/ncds_opus_factory/server/task_runner.py:124)，
round 文件串行写依赖此）。若把"POST run_node + 轮询 node.status 直到 image done"的阻塞逻辑塞进
续跑段（cmd=wolong, source=gate），单条 015 job 出素材要数分钟～数十分钟，期间**唯一的 wolong
worker 被独占**，同 round 其它产线的用户决策、全系统其它 round 的续跑/收盘全部饿死。

**决策**：新增 **`server/job_driver.py`** 独立模块，其驱动协程**跑在 server event loop 上**
（startup 挂一个常驻协程，仿 `subscription_loop`/`reconciler`），**不占任何 wolong worker**。
续跑段保持 **fire-and-return**：只 `create_job + seed_done + run_node(lines)` 后立即登记 render
intent（`task_id=None`）并返回；后续节点推进由 JobDriver 协程接力，节点终态/渲染终态再走
`handle_terminal` 回桥。JobDriver 三责：①绑定 `round_id↔job_id`（存 `line['job_id']`，pipeline
侧无 round 反查）；②**单向串行推进** `lines→storyboard→tts→image`；③回桥（一律轮询
`pipeline_state.json`，见 §2.5）。

**单向推进硬不变量**〔MAJOR 修复〕：推进每一步前先读 `pipeline_state.json`，**仅当目标
node.status != "done" 才 POST run_node**；已 done 一律跳过、绝不重 POST。否则崩溃重入/对账重投
会把已 done 节点重跑，BFS 连带 reset 下游、清掉进度。

### 2.3 `render_final_preview` 作为 `source="wolong"` 的 task = 闸2 外壳〔BLOCKER 修复〕

**问题**：闸2 全靠 `render_final_preview` 终验 task 的 `decision` 回流成 round 事件，但 `source` 取值有
致命二义。`TaskSource` 是闭集 `Literal[user|wolong|gate|cron|retro]`
（[schemas.py:25](../src/ncds_opus_factory/server/schemas.py:25)），计划原文"续跑段干活同源"字面=`gate`。

**证伪两个错误取值，钉死唯一正确值**：
- `source="gate"` → `handle_decision` 首行 `if ... source=="gate" or cmd=="wolong": return`
  （[rounds_gate.py:90](../src/ncds_opus_factory/server/rounds_gate.py:90)）**直接吞掉 decision** → 闸2 永远收不到终验。
- `source="wolong"` → ①`_maybe_auto_archive` 因 `cmd=render_final_preview≠wolong` 且非 cron/retro 而
  `return` 不归档（[task_runner.py:406](../src/ncds_opus_factory/server/task_runner.py:406)）→ 成片正常进待验收桶 ✓；
  ②`handle_decision` 因 `source≠gate` 且 `cmd≠wolong` 而放行投递 decision 事件 ✓；
  ③`POST /tasks` 的递归闸（source=wolong 不许 cmd=wolong）因 `cmd=render_final_preview` 不被拦 ✓。

**决策**：`render_final_preview` 终验 task **派发时 `source` 写死字面量 `"wolong"`**，并加单测断言
`source=="wolong"` 且 `!= "gate"`。这是同时满足"不被自动归档"与"decision 可回流"的唯一取值。

### 2.4 脚本进链：seed_done + draft.md 注入 + **lines 只切句不改写**〔BLOCKER 修复〕

**柳永产物**：纯口播正文 `.md`（无分句/beats/时间戳），`douyin_cog` profile 直出
（[liuyong.py:144-190](../src/ncds_opus_factory/commands/liuyong.py:144)），与 015 的 `02_rw/{model}/draft.md` 同形。

**注入点**：`create_job("final_preview", inputs={})` 建合法 job（不复用柳永 `OGV_*` 目录，
那里无 `pipeline_state.json`，`_load` 会 KeyError）→ 把 `draft['text']` 经 `PUT /jobs/{id}/files/02_rw/draft.md`
写盘（与 `select_rw_model` 拷出的定稿同形，零格式转换）→ 新增
**`PipelineRunner.seed_done(job_id, upto_node="rw")`** 把 `input/asr/rw` **三个节点全部置
`status=done` 并填占位 outputs**〔MAJOR 修复：真实链是 `input→asr→rw→lines`，漏置 asr 会过不了
dep 闸〕→ 从 `lines` 起跑完整下游。`rw.outputs` 必须填最小自洽形（`selected_model_id` +
单条 `drafts` 指向 seed 的 draft.md），否则画布读路径 `list_jobs`/`rewrite_rw_model`/`select_rw_model`
会 KeyError〔MINOR 修复〕。

**台词一致性硬需求**〔BLOCKER 修复，从原 P3 前移〕：`_execute_lines` 是 opus **"改写/压缩/重组"**
而非切句（[pipeline_runner.py:1520](../src/ncds_opus_factory/server/pipeline_runner.py:1520)，prompt 明文"只能改写、压缩、重组"），
会把闸1 已定稿台词逐字改掉 → **闸2 审的不是闸1 审的脚本**。**必须**给 `_execute_lines` 加
`seed=liuyong` 分支：跳过 opus 改写，改用**确定性切句**（按句末标点/长度切 ≤30 字 beats，
`meta.title` 取 topic）。否则收敛前提不成立。

### 2.5 终态/信号一律轮询 pipeline_state.json，不订阅 SSE

015 的 SSE（`/jobs/{id}/events`）的 `EventBus` 是**进程内内存 pub/sub、无整 job 终止帧、不跨
进程**（[pipeline_runner.py:82-111](../src/ncds_opus_factory/server/pipeline_runner.py:82)）。故跨段信号**一律轮询
`pipeline_state.json`** 的节点 `status`（仿 `reconcile_once` 的扫描模式），SSE 仅供进度观测。

---

## 3. 数据交接链（脚本 → episode → 成片）

```
柳永 draft.md (闸1 approved)
   │  create_job(015) + PUT 02_rw/draft.md + seed_done(input/asr/rw=done)
   ▼
lines  ── seed=liuyong 确定性切句（不改写）──▶ 02_rw/episode.json (beats[])
   ▼
storyboard ── director 切子场景、回填 beats[].scene + scenes{} ──▶ episode.json
   ▼
tts  ── scene 整段合成 + 字级时间戳回写 ──▶ 04_tts/scene-<sid>.mp3 + episode.beats[].audio*
   ▼
image ── 按 scenes[].prompt 出容器图 + 简笔画 ──▶ 03_image/<sid>.webp
   ▼  ＜机器自检 _precheck_episode＞
render_final_preview task (source="wolong", round_id, intent_key="render:{slot}:0")
   │  output_path = video-jobs/{job}/06_render/output.mp4
   ▼
待验收桶（闸2 终验）── 用户 decision ──▶ handle_decision → maybe_resume → round 收盘
```

**渲染前机器自检 `_precheck_episode`**（烧渲染算力前的零成本硬校验，`render_final_preview.run` 只查文件
存在不查内容自洽）：①`episode.json` 可解析且 `beats` 非空；②每条非章节 beat 有 `scene` 字段
（storyboard 真跑过）且 `audioFile/audioStart/audioEnd` 齐全；③对应 `scene-*.mp3` / 配图存在。
不过线 → 自动重派一次再升级给人（对齐 WOLONG-DESIGN §4.6"渲染派发前零成本机器自检"）。

---

## 4. 分期实施（已按对抗修复重排 P1/P2 边界）

### P0 — 桥接钩子 + episode 注入（纯 pipeline 侧验证，不碰卧龙）
- **scope**：给 `PipelineRunner` 加 `seed_done(job_id, upto_node)`：写 `02_rw/draft.md` + 把
  `input/asr/rw` 置 `done` 填占位 outputs（显式置 done，非裸改盘，沿用 `_load` 补节点语义）。
  验证 `create_job → PUT draft.md → seed_done(rw) → run_node(lines..image)` 在手填 job 上能过
  dep 闸、出齐三件渲染素材（不渲染）。
- **files**：`server/pipeline_runner.py`、`server/pipeline_runner_test.py`（新增）。
- **exit**：喂一篇 draft.md → seed_done → 从 lines 起跑出齐素材端到端可复跑；`run_node(lines)`
  不 raise；单测覆盖 seed_done 置位与 `_load` 补节点不冲突、`rw.outputs` 被画布读路径消费不报错。

### P1 — JobDriver + 卧龙 render stage 串行驱动（闸1 → 自动出素材）
- **scope**：新增 `server/job_driver.py`（startup 挂常驻协程，**不占 wolong worker**）：
  `(round_id, slot, draft_text) → create_job → seed_done → 单向 run_node 推进 + 轮询 node.status`，
  带超时；`line['job_id']` 绑定幂等（已有 job 在跑不重建）。`wolong_rounds.py` 加 stage
  `scripts→rendering`；续跑段 approved 分支改为 **fire-and-return**（登记 render intent +
  起 JobDriver 接力，立即返回）。
- **〔MAJOR 修复〕状态机边界**：P1 **不**把 render 就绪的 line 推入 `approved/killed` 终态，
  也**不**让 `_finalize_if_done` 在此期收盘——否则要么 round 提前收盘（闸2 接不上），要么永久
  挂 active（[wolong_rounds.py:592](../src/ncds_opus_factory/commands/wolong_rounds.py:592) 门槛是
  `stage=="scripts"` 且 line∈{approved,killed}）。P1 引入中间态 `rendering`，`_finalize_if_done`
  的"`stage=="rendering"` 才认 `take_done/take_killed` 终态"改造与 P1 **一起做**（不拆到 P2）。
- **files**：`server/job_driver.py`（新增）、`commands/wolong_rounds.py`、`server/job_driver_test.py`（新增）。
- **exit**：闸1 approved 后自动在真实 015 job 上跑出素材并过 `_precheck`；round 推进到
  `rendering` 不卡死；崩溃重入幂等（`line['job_id']` 去重、create_job 与写 line 同锁落盘）；
  中途 kill 重入不重建 job；集成测覆盖**单向推进绝不回触上游**。

### P2 — 闸2 终验 task + 事件回桥 + 收盘
- **scope**：JobDriver 在 `image done + _precheck` 通过后 `transport.submit("render_final_preview", ...,
  source="wolong", round_id, intent_key="render:{slot}:0")`；`artifacts.py` `extract_artifacts`
  加 `render_final_preview` 分支（对 `result.output_path` 标 video kind，落 `video-jobs/` 在 `ALLOWED_ROOTS`
  内）；`_handle_event` 加 render task 的 `decision` 分支（approved→收盘该 line；rejected→重渲，
  计返工）。
- **〔MAJOR 修复〕清场不误杀终验成片**：`_cleanup_round_tasks` 现只跳过 `cmd==wolong`/`source==gate`
  （[rounds_gate.py:203](../src/ncds_opus_factory/server/rounds_gate.py:203)），render task 是
  `cmd=render_final_preview source=wolong` 会被取消。须保证**一条 line 在其 render task 拿到 user decision
  前不算落定**，round 不会 done/terminate 去清场。
- **〔MAJOR 修复〕案卷 digest 有可学特征**：`label_store._artifact_digest`
  （[label_store.py:45-85](../src/ncds_opus_factory/server/label_store.py:45)）无 render_final_preview 分支，
  退片样本会落到兜底"存路径"。加 render_final_preview 分支：据 intent 里的 `job_id` 反读 `episode.json`，
  存 `meta.title + 前若干句口播 + scenes 数 + storyboard prompt 摘要 + 时长/体积` 作内容指纹。
- **〔MAJOR 修复〕孤儿 015 job**：round 终局/超时清场新增一支——遍历 `r['lines']` 取 `job_id`，
  调 `PIPELINE_RUNNER` 取消在途节点并标 job 弃用；render intent 对账须同覆盖"job 卡死"与
  "job 已出片但 render task 未派"两态。
- **files**：`server/artifacts.py`、`commands/wolong_rounds.py`、`server/rounds_gate.py`、
  `server/job_driver.py`、`server/label_store.py`。
- **exit**：成片作为带 round_id 的 render_final_preview task 进待验收桶、iOS 点红灯、端上能拿到成片
  video URL；用户终验 decision 回流推进/收盘 round；闸2 reject→重渲路径通；崩溃重入
  （handle_decision 先于 terminal / job 卡死）对账救活；断言 render_final_preview **不**被 `_maybe_auto_archive`
  归档、**不**被清场误杀。

### P3 — 收敛去重（可选，非打通必需）
- 让卧龙链跳过 pipeline 的 `render` 节点（只跑到 image done），渲染只由 render_final_preview task 跑一次
  落 `06_render/output.mp4`，**消除"渲染跑两次"**；render 双外壳收敛为"task 派发 + 画布只读展示"。
- `video-jobs/` 重型素材 GC 立项（WOLONG-DESIGN §5.1 已知缺口）。
- **files**：`server/job_driver.py`、`server/pipeline_runner.py`、`docs/WOLONG-DESIGN.md`。

---

## 5. 已加固的设计决策（对抗 blocker → 决策）

| # | 对抗发现 | 严重度 | 折入决策 |
|---|---|---|---|
| 1 | `source` 二义致闸2 失效 | BLOCKER×3 | §2.3：钉死 `source="wolong"`，加断言 |
| 2 | 续跑段长轮询独占唯一 wolong worker | BLOCKER×3 | §2.2：JobDriver 跑 event loop，续跑段 fire-and-return |
| 3 | 柳永台词被 lines 二次改写 | BLOCKER | §2.4：`seed=liuyong` 确定性切句前移成打通期硬需求 |
| 4 | P1/P2 状态机拆分死结 | MAJOR×2 | §4 P1：`_finalize_if_done` 改造与 P1 同期，引入 `rendering` 中间态 |
| 5 | 清场误杀待验收成片 | MAJOR | §4 P2：render task 拿到 user decision 前 line 不落定 |
| 6 | 孤儿 015 job / 撞名 / 清场不对称 | MAJOR | §4 P2：清场遍历 job_id 取消在途节点；对账覆盖两态 |
| 7 | 案卷 digest 对成片无可学特征 | MAJOR | §4 P2：digest 加 render_final_preview 分支，反读 episode 取内容指纹 |
| 8 | seed_done 漏置 asr 节点 | MAJOR | §2.4：seed_done 置 input/asr/rw 三节点全 done |
| 9 | JobDriver 重推触发 BFS reset | MAJOR | §2.2：单向推进硬不变量（done 不重 POST） |
| 10 | seed_done rw.outputs 空致画布读路径崩 | MINOR | §2.4：rw.outputs 填最小自洽形 |

---

## 6. 仍开放的决策（需你拍板）

1. **渲染返工配额**：rejected→重渲要重跑整条 015 链，现 `MAX_REWORK=2` 对脚本够、对渲染可能偏
   紧——是否给 render 阶段单独返工配额？
2. **building 段对账强度**：015 节点链阶段不是 task、不在 `r['intents']`，对账协程够不到，卡死
   只靠 JobDriver 接力 + 48h 兜底——是否把这条接力也纳入 `reconcile_once`，还是接受 JobDriver 自兜？
3. **JobDriver 超时参数**：单个慢 015 job 在 `ROUND_TIMEOUT_HOURS=48` 下是否拖住整 round；轮询
   间隔与单 job 超时上限取值。
4. **009 链下线时点**：本方案打通期完全不碰 009，下线是独立 L2 决策（删 >30 行 + CLI/旧 job 仍
   合法），留 P4 单独评估还是更晚？

---

## 7. 测试要点

- env fixture 照抄 `test_labels.py`；LLM 调用做成模块级函数属性供 monkeypatch；E2E 用
  `NOF_MOCK_AGENTS` + 固定输出假函数（卧龙必须真跑 round 逻辑）。
- 必测：seed_done 置位/补节点不冲突；JobDriver 单向推进绝不回触上游 + done 不重 POST；
  render_final_preview `source=="wolong"` 不被归档且 decision 可回流；P1 round 推进 rendering 不收盘/不挂死；
  闸2 reject→重渲；崩溃重入幂等 + 对账救活；清场不误杀待验收成片；孤儿 job 被取消；
  digest 对退片有内容指纹；台词一致性（闸1 稿 vs 成片 beats 抽样逐字一致）。

---

## 8. 收敛后的四步实现归属

| 步骤 | 卧龙链唯一实现（保留并复用） | 009 处置（本期不动） |
|---|---|---|
| 分镜 | 015 `_execute_storyboard` + `storyboard_director` | 吴道子 标 legacy/deprecated-for-wolong |
| 配音 | 015 `_execute_tts`（scene 整段 + 字级时间戳） | 伯牙 标 legacy（数据契约不兼容） |
| 出图 | 015 `_execute_image`（gpt-image-2 真出图） | 009 无独立出图入口 |
| 渲染 | `render_final_preview.run`（带 round_id 的 task 那层外壳） | 画布手点继续用 pipeline render 节点那层；P3 收敛单次化 |
