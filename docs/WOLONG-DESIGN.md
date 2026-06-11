# 卧龙实装设计：调度、闸门与离线学习

> 状态：设计稿 v2.1（2026-06-11）。尚未实施。
> v2 修订：经三视角对抗核查（后端代码论断 / iOS 副作用 / 设计漏洞），修复 v1 的 4 个 blocker——
> ①防递归规则与续跑机制自相矛盾 ②无闸阶段与失败任务无事件推动 round（永久挂起）
> ③cron/系统任务淹没 iOS 待验收桶并把首页灯打成永久红闪 ④柳永打回自动重投与卧龙返工双重派单。
> v2.1 修订（业务方裁定）：打回理由必填（语音）是有意设计，保留——Leader 直接意见是最高质量标签，
> 省掉卧龙重载上下文推断的成本与误差；卧龙推断降级为兜底。仅补听写稿轻量编辑。
> 前置阅读：FRONTEND-API.md（各 agent 脾气）、scripts/wolong_sop.md（卧龙现行 SOP）。

## 0. 设计原则（业务约束，高于一切技术取舍）

1. **这是有闸门的流水线，不是全自动工厂。** 产能提升来自"废品死得早"，不是"机器跑得多"。生产链成本递增：选题（鬼谷子，秒级）→ 成稿（柳永，分钟级）→ 美术/声音/渲染（贵）。闸门卡在便宜环节，挡住废品向贵环节扩散。
2. **组织结构：用户(AI Leader) → 卧龙(CEO) → 干活 agents。** 用户的直接下游只有卧龙。卧龙负责派发、验收、汇报；干活的 agents（沈括/鬼谷子/柳永/吴道子/伯牙）只对卧龙负责。
3. **用户的验收点击 = 替卧龙做的标注。** 文字内容质量高度主观，验收判断从卧龙身上拆出来外包给人。每条 `decision(+note)` 都是一条训练样本，卧龙必须离线学习它们，逐步把用户的隐性标准内化成自己的质检能力。
4. **自动化边界划在"入选题库"为止。** 选题以上（订阅、采集、选题）废品代价趋近零，可以全自动；成稿以下必须过闸门。**推论：自动化环节绝不能给用户制造验收工作**——凡是机器自己能闭环的任务，不准进待验收桶、不准点红灯。
5. **任务 agent 多实例执行**，调度机制五段式：接收、入队、执行、投递、反馈。"投递/反馈"必须同时覆盖**人的反馈**（decision）和**机器的反馈**（completed/failed/cancelled）——只接人的那一半，round 会在无闸阶段和失败路径上永久挂死。

## 1. 总体架构

```
                    ┌─────────────────────────────────────────┐
                    │              用户 (AI Leader)             │
                    │   iOS App：下任务 / 验收(=标注) / 看战报    │
                    └────────────┬───────────────▲─────────────┘
                          decision+note      待验收物/战报
                    ┌────────────▼───────────────┴─────────────┐
                    │              卧龙 (CEO)                    │
                    │  派单 · 检查点暂停 · 事件驱动续跑 · 复盘学习  │
                    │  rubric(版本化审美备忘录) ←─ 离线复盘标注史   │
                    └──┬────────────────────────────▲──────────┘
            POST /tasks(source=wolong)     decision事件 + 任务终态事件
                    ┌──▼────────────────────────────┴──────────┐
                    │        调度器 (TaskRunner 改造)             │
                    │ 接收→入队(per-cmd额度)→执行→投递→反馈+对账协程│
                    └──┬────────────────────────────────────────┘
       ┌───────────┬───┴───────┬───────────┬───────────┐
    沈括(采集)  鬼谷子(选题)  柳永(成稿+质检) 吴道子(美术)  伯牙(声音)
       ▲
   cron 订阅传感器 (source=cron, refresh_only, 完成即自动归档)
```

五段式与组件对应：

| 环节 | 现状 | 改造 |
|------|------|------|
| 接收 | `POST /tasks {cmd,params}`（routes/tasks.py） | 加 `source` / `parent_task_id` / `round_id` 可选字段 |
| 入队 | 无队列，`submit()` fire-and-forget；`requeue()` 同样直起协程 | per-cmd 并发额度 + 等待队列，**submit 与 requeue 同走队列**（§2） |
| 执行 | `asyncio.to_thread` 跑 `run()` | 出队时 CAS（pending→running）防双跑（§2） |
| 投递 | 产物落盘 + iOS 收件箱 | 任务终态事件（completed/failed/cancelled）通知 round（§4.2） |
| 反馈 | `decision` 写入 review.json 后无流程/学习消费者（唯一消费者是清扫协程的破坏性删除） | 双消费者：流程信号（§4）+ 标注样本（§5）；对账协程兜底（§4.5） |

## 2. 调度器：队列与额度（task_runner.py 改造）

现状 `TaskRunner.submit()` 来一个起一个线程，无上限；`requeue()`（restore 路径，task_runner.py:48）同样绕过一切管控。多实例 + agent 派单后必须收口：

```python
# configs/ 或环境变量，per-cmd 并发上限（同时 running 数）
CONCURRENCY = {
    "shenkuo": 2,    # TikHub 限流
    "wolong": 1,     # sclaude 账号池，且 round 状态文件需要串行写
    "liuyong": 3,    # scodex 子进程
    "guiguzi": 4,
    "_default": 4,
}
# 每日配额（防失控），超额的"派单类"任务 failed 并注明原因；
# "续跑/复盘类"(source=gate/retro) 超额改为排队等第二天——闸门任务不能因配额死掉(§4.5)
DAILY_QUOTA = {"wolong": 8, "shenkuo": 40, "_default": 100}
# 续跑段与复盘段独立记账，不与派单段抢 8 格配额
```

- **实现**：`submit()`/`requeue()` 创建/重置 meta 后统一改投 per-cmd 的 `asyncio.Queue`；每个 cmd 一组 worker 协程按额度消费。`status=pending` 即排队中，不新增状态值。worker 协程在 **app.py 的 startup 钩子**里拉起（RUNNER 在 state.py import 期构建，无 event loop 可用）。
- **出队 CAS**：worker 取出条目后先做 `pending→running` 的原子状态置换，状态不是 pending（已被取消、已被 restore 重置后由新条目接管）就丢弃该条目。否则"排队中 cancel→restore"会双重执行。
- **启动恢复**：startup 钩子扫描 task store，所有 `status=pending`（以及 running 但属上次进程残留的）任务按 created_at 重新入队。队列是进程内存，不做这一步，重启后排队任务全部变成收件箱里的永久僵尸。
- **防递归（v2 修正）**：v1 的"source=wolong 不允许 cmd=wolong"会把续跑任务自己拦死。改为：
  - `source=wolong`（卧龙派单段派出的干活任务）不允许 `cmd=wolong`；
  - `source=gate`（decision/终态事件触发的续跑段，§4.2）豁免，但同一 round 同时只允许一个 pending/running 的续跑段（§4.4 合并）；
  - 深度按**业务派生**计而非任务记录计：同 round 的所有卧龙段视为同一层，干活任务记为 round 下一层，链深 ≤2 封顶。
- 额度即预算闸门：这是"不设边界必然浪费"的机制化兜底，与 SOP 里"绝不超额"的口头约束互为冗余。
- iOS 注意项：列表页已有"未开始/排队中"桶，零适配；但两个详情页把 pending 显示为"工作中…/创作中…"，排队拉长后会误导，文案随 P5 一起调。

## 3. 溯源：任务从哪来（schemas.py / task_store.py / iOS）

`TaskCreateRequest` 与 `TaskMeta` 增加可选字段：

```python
source: Literal["user", "wolong", "gate", "cron", "retro"] | None = None  # 缺省=user
parent_task_id: str | None = None    # 派生自哪个任务
round_id: str | None = None          # 属于卧龙哪一轮编排（§4）
```

- 改动点：schemas.py（两个 model）、task_store.py `create()` 落盘、routes/tasks.py 透传。
- **iOS 硬约束**：三个字段一律建模为 `String?`，**不准建成 Swift 封闭 enum**——后端将来加新 source 值会让整个 /tasks 列表解码爆炸（listTasks 是严格的 `[TaskMeta].self` 整体解码）。端上 `nil ≡ user`。
- iOS 顺手加固（建议与 P1 同期）：listTasks 改逐条容错解码（元素级 `try?` 丢弃坏条目）。写 /tasks 的来源从 1 个变成 4+ 个后，单条脏数据让收件箱整页报"连不上服务器"、首页六灯全灭的爆炸半径不可接受。至少把"解析失败"与"连不上"的文案分开。
- 收件箱任务卡按 `source` 显示来源角标（"卧龙派发"/"自动订阅"），让用户知道自己在替卧龙验收什么。
- **iOS"重新发起"的语义修正**：现状 retry 只回填 params，新任务缺省 source=user——会斩断溯源链，round 等不到原检查点、孤儿任务又变红卡。带 `round_id` 的失败任务**隐藏重试按钮**，失败处置交还卧龙续跑段（§4.2 机器反馈路径）。

## 4. 验收闸门 = 流程控制信号

### 4.1 卧龙从"一次性命令"变成"分段编排"

现状卧龙 fire-and-forget：`run_wolong.sh` 拉起 headless opus 跑完一轮即结束。改造为**无状态分段 + 文件衔接**（不采用常驻会话：贵、占 sclaude 额度、进程挂了状态就丢）：

- 每轮编排有 `round_id`，状态文件 `state/wolong/rounds/{round_id}.json`：当前阶段、已派任务、待决检查点、已消费事件、预算消耗、返工计数。
- 卧龙每段：读 round 状态 → **先写派发意向（intent：本段计划派发的 stage+slot）→ 再派发**（HTTP `POST /tasks`，幂等键 = round_id+stage+slot，调度器查重拒绝重复）→ 更新 round → 进程结束。先意向后派发：段在派发后崩溃，下一段能从 intent 对出"已派未记"，不重复派柳永。
- **round 文件一律 tmp+rename 原子写**。卧龙段超时是 `proc.kill()` 硬杀，直接写会留下撕裂的 JSON。
- **卧龙段必须可取消**（v1 遗漏）：现状 wolong.run() 只有 deadline 没有协作式取消（对比 shenkuo 的 `_run_proc_cancellable`），用户取消后 opus 子进程继续跑满 30 分钟、继续派单，产生无人认领的幽灵 round。改造：轮询取消标记 SIGTERM 子进程，取消时把 round 状态标记终止。
- `cmd=wolong` 的任务**禁用 restore**（或 restore 前检查该 round 无在途段）——恢复的段会与队列里的段并发读改写同一个 round 文件。
- **`commands/wolong.py` 的 `run()` 签名要扩展**（v1 遗漏改动点）：现签名 `run(count, benchmark_path, avoid, timeout_seconds, on_progress)`，调度器是 `run_fn(**params)` 直接展开，发 `{round_id, resume:true}` 或 `{mode:"retro"}` 会直接 TypeError。新增 `round_id/resume/mode` 参数并同步 command_schemas。

### 4.2 事件接线：人的反馈 + 机器的反馈（v2 重做）

v1 只接了 decision 一个事件源，**无闸阶段（吴道子/伯牙）完成后没有任何信号推动 round，失败/取消的检查点永远不产生 decision——round 必挂**。v2 两个事件源都接：

1. **人的反馈**：`review_task` 写完 review 后，若任务带 `round_id` → 向 round 文件追加事件并触发续跑（§4.4 合并）。
2. **机器的反馈**：`TaskRunner` 终态分支（completed/failed/cancelled）+ `cancel_task` 路径，若任务带 `round_id` → 同样追加事件并触发续跑。无闸阶段完成 → 续跑段解锁下一阶段；失败/取消 → 视为该检查点弃单，续跑段决定重派（计入返工 ≤2）或止损。

续跑任务形如 `POST /tasks {cmd:"wolong", params:{round_id, resume:true}, source:"gate"}`。续跑段读 round 状态 + 积压事件，一次性消费全部：

- **approved** → 解锁下一阶段（脚本过审 → 派吴道子/伯牙/render）。
- **rejected** → 读 `note`，返工（带理由重派柳永，round 内 ≤2 次）或止损（终止该条产线，入战报）。
- 全部产线出结果 → 战报（产出/淘汰/预算消耗/标签摘要/预筛准确率）。

### 4.3 decision 的定案语义（改判/撤销，v1 未定义）

review 是幂等覆盖（改判）且有 DELETE（撤销），照 v1 每次改判都会再触发一段卧龙：

- **定案**：decision 一旦被续跑段消费即定案。之后的改判只影响案卷标签（最终判定），**不回卷流程**——已派出的下游不取消、返工计数不重算。iOS 对已定案任务的改判给出提示（v1 还遗漏：iOS 现状 review!=nil 就隐藏决策区，改判入口本身是 P5 要补的 UI）。
- **撤销**（DELETE review）：案卷打 `revoked` 标记（复盘剔除），流程同样不回卷。v1 流程级反悔（取消在途下游）明确**不支持**，留给以后。
- round 事件按 `(task_id, decision)` 去重，续跑段开头查 round 状态跳过已处理检查点——续跑天然幂等。

### 4.4 续跑合并（防风暴）

用户连续验收 N 条 → 不能触发 N 段冗余的 opus 全量拉起（分钟级、烧 sclaude、吃配额）。round 文件加 `resume_pending` 标志：已有 pending/running 续跑段时，新事件只追加不入队；续跑段启动时一次性消费积压。配额上续跑段独立记账（§2）。

### 4.5 对账协程（reconciler，闭环的兜底）

decision→续跑是一次性 POST，三种现实场景会让 round 无声卡死：配额耗尽续跑 failed、POST 时服务重启、排队中的续跑随内存队列蒸发。加一个周期对账协程：扫描 `state/wolong/rounds/`，凡"存在未消费事件、且无 pending/running 的该 round 续跑段"就补投一次。一个 reconciler 同时治好三种病，外加 round 级超时强制收尾出战报。

### 4.6 检查点划分（哪里设闸）

| 阶段 | 闸门 | 理由 |
|------|------|------|
| 订阅/采集/选题入库 | 无闸，全自动 + **完成即自动归档**（§4.7） | 废品代价≈0 |
| 柳永成稿 | **闸 1：脚本验收**（最重要） | 文字主观性最强；挡住废稿就挡住下游全部成本 |
| 吴道子/伯牙 | 无人工闸，但渲染派发前做**零成本机器自检**：产物存在、尺寸/时长合规、图文对应度按 rubric 快扫；不过线自动重派一次再升级给人 | "出问题再开闸"是事后哲学，机器自检是事前的，且不让用户文职化 |
| 渲染成片 | **闸 2：终验** | 发布前最后一道，现有验收 UI 复用 |

### 4.7 系统任务不准打扰 Leader（v2 新增，blocker 修复）

iOS 的归类是写死的：completed 且无 decision = 待验收桶 + 首页红灯（AgentTaskListView/AgentHome）。卧龙时代有三股**永远不会有人写 decision** 的新流量：cron 刷新任务、无闸阶段任务（吴道子/伯牙）、续跑段自身。不处理的话：吴道子/伯牙/沈括的灯永久红闪、待验收桶每天被几十条垃圾淹没——而用户为清空收件箱的顺手点击会变成垃圾标签毒化 rubric，**这是对原则 3 和 4 的双重违背**。

机制：**Review 模型加 `reviewer: Literal["user","wolong","system"]` 字段**（§5 同款），凡机器可自闭环的任务，后端在终态时自动写 `reviewer=system, decision=approved` 的 review → iOS 现有逻辑自动归档、不点灯、零改动。复盘只学 `reviewer=user` 的样本，系统 review 不污染案卷。适用：`source=cron` 的 refresh、round 内无闸阶段任务、续跑段（续跑段卡片在 P5 折叠进战报视图；当前 titleGuess 对 `{round_id,resume}` 会显示乱码，schemas 给续跑任务回填 title="卧龙·续跑段"）。该豁免**必须与制造流量的期数同期落地**（cron 豁免在 P2，round 豁免在 P3），不能等 P5。

### 4.8 卧龙 SOP 改造（scripts/wolong_sop.md）

- 派活方式从 CLI 子进程改为 `curl POST /tasks`（带 source/round_id/幂等键）：派生任务进统一 task store / SSE / 验收闭环，iOS 收件箱可见；CLI 子进程对任务系统不可见、不可取消。
- 教它沈括的用法（采集派单），现状 SOP 零处提沈括。
- 注入 rubric（§5.3）。
- 启动器修缮：`run_wolong.sh:14` 默认对标路径指向不存在的 `all_posts.json`（全仓没有任何一份，按默认参数跑必失败）——改为自动发现最新 `author_*/all_posts.json` 或清晰报错。（REVIEW_DIR 与 SOP 互相一致，不是问题；round 战报机制统一时一并迁移即可。）

## 5. 离线学习循环——卧龙实装的灵魂

### 5.1 案卷库：标签必须独立于任务目录存活

**现状冲突（紧迫）**：app.py 清扫协程把超期 7 天的 rejected 沈括任务整目录 `rmtree`——review.json（标签）、meta.json（特征）、result.json 一起销毁。"验收=标注"原则下这是在删负样本。

新增 `state/wolong/labels/`：每条 decision 由 `write_review` 同步生成案卷（幂等覆盖支持改判）：

```json
{
  "task_id": "...", "cmd": "liuyong", "round_id": "...",
  "params_digest": {"topic": "...", "user_requirements": "..."},
  "artifact_digest": "脚本前 500 字 / 选题列表 / 采集摘要（按 cmd 提取；result 缺失时降级只存 params_digest）",
  "decision": "rejected", "note": "开头太绕，钩子不行",
  "reviewer": "user",
  "note_origin": "user | machine | inferred",
  "reviewed_at": "...", "revised": false, "revoked": false
}
```

- 清扫协程改造：rmtree 之前确认案卷已存在；案卷永不清扫。（v1"重型产物照删不误"说法有误：清扫只删任务记录 JSON，视频/截帧/抠图在 state/benchmark/、state/figure_collected/ 共享目录，现状无任何机制回收——重型素材 GC 是另一件事，需另立机制，不在本设计范围。）
- `reviewer` 字段是整个学习系统的防火墙：复盘**只把 reviewer=user 的当标注样本**；reviewer=wolong 的是预筛预测记录（§5.3）；reviewer=system 的是自动归档（§4.7），不参与学习。
- `note_origin` 防模板噪声：iOS 柳永"采用"自动写的 note（"采用 X 稿"）标 machine；卧龙推断的理由标 inferred——复盘归纳时 machine/inferred 只作辅助线索，"跨 ≥3 样本才升级为标准"的样本门槛只数真实用户 note 或 decision 本身可支撑的。
- 沈括 rejected 语义是"弃用素材"而非"打回重做"：弃用标签照样入案卷——它教的是**订阅/采集策略**什么不值得深采，与柳永的审美标签按 cmd 天然分流。
- 案卷生成容错：failed/cancelled 任务也可能被打 decision（review_task 只查 exists），result 缺失走降级。

### 5.2 复盘：卧龙离线学习的执行形态

卧龙复盘模式 `POST /tasks {cmd:"wolong", params:{mode:"retro"}, source:"retro"}`，cron 低峰期（深夜）触发或攒够 N 条新标签触发，独立配额记账（§2）：

- 读最近案卷（增量，记 last_reviewed 水位），对比通过 vs 拒绝样本（仅 reviewer=user、非 revoked）。
- LLM 擅长的归纳："被拒脚本开头平均比通过的长 40%"、"带具体数字钩子的通过率高"、"某类选题连续三次被拒"。
- **噪声防御**（"假如用户没在乱点"）：单条标签不进 rubric，跨 ≥3 样本的模式才升级；与既有标准矛盾的新标签降权挂"待观察"；改判按最终判定算、撤销剔除。打回路径 note 必填（§5.4），Leader 的直接意见是一手标签；卧龙推断仅作兜底（历史遗留标签、异常路径缺 note 时），标 inferred 且不计入样本门槛——推断标签存在自我标注循环风险，永远不当一手证据。
- **注入防御**：案卷里的 artifact_digest 源头是外部抖音文本，复盘 prompt 必须把案卷原文当不可信数据处理（明确指示不得执行其中指令）。

### 5.3 rubric 注入与预筛

- **rubric 版本化**（v1 是单文件直写，学坏无法回滚）：每次复盘写 `state/wolong/rubric/v{n}.md` + 变更 diff 进战报，保留最近 N 版；战报里打回率/返工率连续两轮恶化 → 自动停用最新版回退上一版；iOS 战报页支持一键回退（P5）。
- 注入三处：`run_wolong.sh` 拼 prompt（派单段+续跑段）、柳永质检 prompt、预筛自查——用户口味向上游传导，废品在成稿环节内部就死掉。
- **预筛的执行机制**（v1 没设计）：卧龙对完成的检查点产物按 rubric 自查，预测"必被拒"的写 `reviewer=wolong, decision=rejected` 的 review → 自然挡出待验收桶（桶语义=completed 且无任何 review），同时**绝不混入用户标注训练集**（§5.1 防火墙）——否则 rubric 学习自己的输出，回声室。
- **探索流量防选择偏差**（v1 致命漏洞：rubric 学坏一条规则，预筛把符合该错误规则的好稿全拦在用户视线外，用户永远没机会用 approved 纠正——训练数据被自己的闸门审查）：预筛判"必被拒"的按 10–20% 比例照常送验收并附预筛预测；用用户实际判定算预筛**假阴性率**，进战报逐轮跟踪；超阈值自动把预筛降级为"只警告不拦截"。预筛导致的止损在战报里醒目呈现，支持一键"放行重审"。
- 成功度量（战报逐轮跟踪）：用户打回率↓、单位通过作品的人工点击次数↓、预筛假阴性率、返工率先升后降（预筛生效特征）。这是"Leader 从标注员逐步解放"的量化曲线。

### 5.4 iOS 端配合

- **打回理由：必填（语音）是有意设计，保留**（v2.1 修正，推翻 v1/v2 的"可空"提议）：Leader 打回时口述一句"为什么"，是一手判断、信噪比最高的标签，且省掉卧龙重载完整上下文去推断的成本与误差（推断标签有自我标注循环风险，§5.2）。语音输入十秒说完，负担接近零——"通过不用解释、打回说句为什么"，解释成本只花在更稀缺更值钱的负样本上。唯一增强：现状语音遮罩**无编辑入口**（听写完只能照单提交或重说），补听写稿的轻量编辑（语音输入→简单修改→提交）；可选叠加常见理由一键标签作为加速器（追加进 note，不替代口述）。
- **柳永打回的双重派单修复**（blocker）：现状柳永详情页打回会立刻用打回意见自动重投一个新 liuyong 任务（source 缺省 user、无 round_id），叠加卧龙续跑返工 = 双倍成稿成本且绕过返工配额。改为：带 `round_id` 的任务打回**只写 review 不自动重投**，返工权交还卧龙；同时该路径里 `try?` 写 review 改为失败即中止——现状 review 失败时新任务照发，标签静默丢失。
- 卧龙详情页战报面板扩展：round 进度（阶段/检查点/预算）+ rubric 学习摘要（"本轮新学到：…"——让 Leader 看见 CEO 在成长）+ 预筛假阴性率 + 改判入口（§4.3）。
- agent 卡片墙灯态目前只在 onAppear 刷新一次：卧龙自主派单后任务在后台生灭，灯是快照不是状态——加轮询或订阅任务事件（P5）。
- （已排除的雷：锁屏 Live Activity 由状态灯中继驱动，与 Factory 任务系统完全解耦，不受影响。）

## 6. 两个自动化源头

### 6.1 订阅传感器（自动化之一：对标账号新作品）

- 地基已有：`shenkuo --refresh-only`（shenkuo.py:552）只拉列表+写指标层 SQLite 时间序列、跳过深采，注释明写"给高频 cron 用"。
- 新增 `state/shenkuo/subscriptions.json`：订阅作者列表（sec_uid + 备注 + 启停）。
- launchd/cron 周期任务（如每 2h）：逐订阅作者 `POST /tasks {cmd:"shenkuo", params:{author, refresh_only:true}, source:"cron"}`——走任务系统受额度管控；完成即自动归档（§4.7），**不进待验收、不点灯**；加独立 TTL 清扫（现清扫只认 rejected 沈括）。
- **信号检测要新增查询**（v1 低估了：`record_refresh` 只返回计数，且指标无变化不落快照行、相邻快照间隔不固定）：
  - `benchmark_store.new_posts(conn, sec_uid, ts)`：`posts.first_seen == ts` 判新作 → 事件 `new_post`；
  - 归一化增速查询：`(latest - prev) / (ts_latest - ts_prev)`（按 Δ指标/Δ时间，不能拿相邻两条快照当等距周期——沉寂多日才动的作品会被误判单周期暴涨）→ 事件 `spike`（"爆了"，比"有新作"更值钱）。
- 事件落 `state/shenkuo/events.jsonl`。**事件语义**：生成端按 `(aweme_id, 事件类型, 日期)` 去重，spike 加 24h 冷却窗口；消费端（排产）持久化 offset，防重启重复消费白烧配额。

### 6.2 排产策略（自动化之二："有目标的选题"）

与 6.1 **不是同一件事，但共享一半**：传感器是感知，排产是计划。两种触发形态：

- **事件驱动（追热点）**：消费 6.1 的 `spike`/`new_post` → 自动深采该条（shenkuo 单条模式）→ 派鬼谷子迁移分析 → 入选题库。
- **库存驱动（补货）**：选题库可用条数低于水位 N → 自动派一轮鬼谷子。与事件无关，独立存在。
- **硬前置——选题库改造**（v1 遗漏，现状走不通）：鬼谷子每次**整体覆盖写** topics.json，且全链路没有任何消费标记的写入方——"未消费条数"无从计算，补货还会把没用完的存货清掉。先把选题库改成带 id 的 append/merge 存储：`topic_id + status(fresh/consumed/expired) + created_at`，鬼谷子合并写入，卧龙挑题写回 consumed（挂 P3）。
- 实现形态：先用**纯规则**（轻量排产协程），不上 LLM——规则够用且可预期；复杂了再升级为卧龙排产模式。
- 边界：排产自动化**止于入选题库**。选题→成稿的扣扳机权在卧龙（用户下达 round 目标后），不归排产。
- 预留（将来第三形态）：自有账号发布数据回流 → 效果反馈驱动选题加权。现状系统无自有账号数据概念，只在案卷/选题库 schema 留 `performance` 可选字段，不实施。

## 7. 实施顺序（v2 重排范围，期序不变）

| 期 | 内容 | 说明 |
|----|------|------|
| **P1** | 案卷库（含 reviewer/note_origin 字段）+ 清扫修正 + 溯源字段；iOS：TaskMeta 三个 `String?` 字段 + listTasks 逐条容错解码 | **标签有时间价值，晚一天少一天数据**。纯增量，零风险 |
| **P2** | 调度器（队列/额度/出队 CAS/启动恢复/requeue 收口）+ 订阅传感器（含 benchmark_store 新查询、事件去重）+ **cron 任务自动归档豁免** + 续跑 carve-out 的 source 设计 | 多实例收口是一切派单的前提；豁免必须与传感器同期，否则收件箱从上线第一天开始被污染 |
| **P3** | 卧龙分段编排（run() 签名/可取消/round 原子写/intent 幂等键/禁 restore）+ **双事件源接线**（decision + 任务终态）+ 对账协程 + 续跑合并 + 定案语义 + round 任务自动归档豁免 + 选题库 consumed 写回；iOS：柳永打回不自动重投（round 任务）+ 隐藏 round 任务重试按钮 | 核心闭环：派单→检查点→(人+机)反馈→续跑→战报。机器反馈和 reconciler 不是可选项——缺了 round 成批挂死 |
| **P4** | 复盘（retro 独立配额、低峰 cron、注入防御）+ rubric 版本化/自动回退 + 预筛 + **探索流量与假阴性率跟踪** | 需要 P1 攒的标签和 P3 的 round 机制 |
| **P5** | 排产策略（含选题库完整改造）+ iOS 增强（角标/打回听写稿可编辑+快捷理由标签/战报页/改判入口/续跑卡折叠/灯态轮询/pending 文案） | 锦上添花，前面跑顺再上 |

P1 改动面：后端 schemas.py、task_store.py、routes/tasks.py、app.py 四个文件；iOS NofModels.swift、NofClient.swift 两个文件。当天可上线。
