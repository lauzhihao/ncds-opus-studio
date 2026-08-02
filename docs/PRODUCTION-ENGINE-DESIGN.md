# 统一生产引擎设计（core → 生产引擎 → 两视图）

> 状态：**v1 DESIGN（2026-06-13），实现快照补充到 2026-06-22。用户已拍板方向、范围、首步路径，本文档是权威设计。**
> **取代**早期三包拆分方向——三包对等拆分作废；**P1 抽 core 的成果全部保留**。
> 产出方式：grounded 研究工作流（4 路逐块分类 web/app 现状 + 合成）。历史 passed 数只作考古线索；执行任务时以当天 `pytest --collect-only` / `pytest` 真实结果为准。

## 当前实现快照（2026-06-22）

本节描述**现在代码实际怎么跑**；下面各节保留的是目标设计和迁移路线。

- web 内容生产主路径仍是 `/jobs/*` → `PipelineRunner` facade → engine nodes。`JobState` / `video-jobs/` 仍是 web UI 契约真相源。
- `NOF_ENGINE_NODES` 未设置时，`PipelineRunner` 会把可执行节点集合初始化为 engine-safe 节点；实际默认是 `lines/storyboard/tts/image/render` 走 engine，`asr` 固定走 legacy `_execute_asr_collect`，`rw` 固定走 legacy `_execute_rw`。
- `NOF_ENGINE_NODES=""|"none"|"off"|"legacy"` 会让 web 画布全走旧 `_execute_*` 路径；逗号列表只让列出的非 `asr`/`rw` 节点走 engine。
- Flutter app 决策视角当前仍走 `/tasks` → `TaskRunner` / `nof-worker`；尚未切到 `/instances`。
- `/instances` 后端 driver API 与测试已存在，但不是 web/app 前端主路径。
- `RECIPE_REGISTRY` 当前只注册 `final_preview`；`figure_talk` 仍是未来 recipe / cold chain，不是当前主路径。

---

## 0. 决策回顾（用户已拍板）

**起因**：server 端长成两套并行运行时（web 的 `PipelineRunner` vs app 的 `TaskRunner`），且 web 把生产步骤
复刻了一份，bridge 把两套塞进一个进程——"快失控"。三包对等拆分只解 bridge、反而把这个 fork 永久固化
（P2 撞同名包冲突就是症状）。

**洞察（用户提出，研究证实）**：两端底层是**同一件事**——一个"工作流实例"产出一个作品（视频）。
`任务ID` 与 `作品ID` 是同一个东西的两个名字。web=作品/内容视角，app=agents/决策视角。

**已定（2026-06-13）**：

| # | 决策 | 选择 |
|---|---|---|
| D1 | 方向 | ✅ 放弃三包对等拆分；改 **core 能力 → 一个生产实例引擎 → 两视图 + app 外挂子系统** |
| D2 | 发起 + 租户 | ✅ 用户发起（给链接/对标账号→卧龙派人）；**按用户隔离**（现无鉴权，schema 预留 `owner_id`，不挡死） |
| D3 | 配方 | ✅ **目标多配方**：final_preview / figure_talk 剪影各是一条 recipe；当前只注册 final_preview，figure_talk 仍待 E3 入册 |
| D4 | 介入模型 | ✅ **每步 = agent 先出草稿 + 人可选介入**（质量闸门，尤其挡贵步骤；人反馈回喂卧龙自学习）。web 介入=改内容，app 介入=批决策 |
| D5 | **范围** | ✅ **全范围**：两端合一 **+ 补完 agent→render 闭环 + retro 自学习**（这条链历史上设计过 CONVERGENCE 但从没建完，见 §9） |
| D6 | **首步路径** | ✅ **C：web 整条 pipeline 一次迁上引擎**（最快见效，风险最高；护城河=web 旧画布可跑副本在 `main`，本 branch 不并 main 就毁不掉） |

---

## 1. 目标架构

```
┌───────────────────────────────────────────────────────────────────────┐
│ core (ncds_opus_core, 已抽出)  纯能力 + PRIMITIVE_REGISTRY(6)            │
│   wst/tst/vid/tts/render/render_final_preview + gpt_image + 模板015 + 中性 common   │
└───────────────────────────────────────────────────────────────────────┘
                     ▲ 单向依赖                  ▲ 单向依赖
┌──────────────────────────────────┐  ┌────────────────────────────────────┐
│ factory agents (ncds_opus_factory)│  │ ★ 生产引擎 production engine ★       │
│  卧龙 = 编排 driver，麾下五人：       │  │  InstanceStore + InstanceRunner      │
│   沈括(采集)→鬼谷子(选题)→柳永(编剧)  │◄─┤  + Recipe 注册表 + 步骤生命周期       │
│   →吴道子(美术)→伯牙(声音)→render    │  │  + 介入点 + 分层 SSE                  │
│  + AGENT_REGISTRY(8)              │  │  靠 build_full_registry() 晚绑定派发  │
│  + 自治神经层(订阅/排产/学习/接线/案卷)│  │                                      │
└──────────────────────────────────┘  └────────────────────────────────────┘
        ▲ 卧龙 driver 驱动                         ▲ web 手动 driver 驱动
        │                                          │
┌───────┴──────────┐                    ┌──────────┴───────────┐
│ app 视图(决策)     │  同一批生产实例      │ web 视图(内容)         │
│ 小屏·批 go/no-go  │ ◄────────────────► │ 大屏·改每步内容         │
└──────────────────┘                    └──────────────────────┘
```

**包拓扑（务实，不再硬拆三包）**：物理上只有 **core 一个独立包**（P1 成果）；**生产引擎 + agents + 两视图路由
都是根包 `ncds_opus_factory` 内的逻辑分层**（`server/engine/`、`commands/`、`server/routes/`）。
不重新引入 P2 那种同名包物理拆分的痛。"分层"是模块边界，不是包边界。

**关键机制——晚绑定派发（解掉"recipe 放哪会污染 core"）**：引擎不直接 import 任何 agent。Recipe 里每个步骤
只写**字符串 key**（`cmd` / `agent` id），引擎通过 **`build_full_registry()`（P1.x 已建：6 core + 8 factory）**
查表派发。因此引擎对 agents 零静态依赖，core 保持纯净，新增 agent/步骤只是往 registry 加一行。

**Domain strategy 派发（2026-07-31 已实现）**：所有有执行体的语义生产节点在 facade 与
`InstanceRunner` 中统一按 `(node, domain)` 从 `DomainStrategyRegistry` 解析；未知 domain 命中
`*` default，保持既有生产行为。`film` 的沈括 strategy 复用下载与 ASR sidecar，但以
2fps 视频抽帧 + 底部字幕 RapidOCR 为真源，在 collector 内完成中文校正与校正后时序去重，直接产出
`film_script_source.v2`。`raw_ocr` immutable，`clean_script` 提供可审核的 zh-CN txt/srt/json；
默认 film 不进入鬼谷子或柳永。旧 artifact 不兼容，必须从沈括重跑。新增 domain
只需实现并注册 strategy/processor，不改节点核心分发代码；source/quality 等没有 domain
strategy 的 technical step 安全回退 recipe performer，无 performer 的 input/output/人工 preview
闸门仍按 recipe 状态机处理。该派发不再只限于 `final_preview`，film recipe 的
storyboard/tts/render 同样走 `(node, film)` strategy。

---

## 2. 核心抽象

一个**生产实例（ProductionInstance）= 一条 recipe 的一次执行**，由有序步骤构成，每步可挂草稿 + 人工介入点。
`task` 与 `job` 收敛成 `instance`；`task_id`/`job_id` 收敛成 `instance_id`。

```python
# server/engine/types.py（新）

class StepState(BaseModel):
    step_id: str                  # "asr"/"rw"/"liuyong"/"wudaozi"/"tts"/"image"/"render"...
    status: StepStatus            # 见 §4 状态机
    task_id: str | None = None    # 该步在 InstanceRunner 里的执行体句柄
    started_at: float | None = None
    finished_at: float | None = None
    progress: str = ""            # 最新进度文本（来自执行体 on_progress）
    draft: dict | None = None     # agent 出的中间产物（review 前；web 可编辑）
    draft_source: Literal["agent", "user"] = "agent"
    decision: Literal["approved", "rejected", "pending"] | None = None
    review: Review | None = None  # 人工审看记录（喂 label_store / retro）
    outputs: dict = {}            # 定稿产物（review/edit 后）
    config: dict = {}             # 该步运行参数（profile/quality/选中的 model 等）
    error: str | None = None

class InstanceMeta(BaseModel):
    instance_id: str              # i_<ms>_<hex8>；兼容读旧 task_id/job_id
    owner_id: str | None = None   # 多租户预留（演示=None / "demo"，生产由鉴权中间件填）
    recipe_id: str                # "final_preview" / "figure_talk" ...
    recipe_version: str = "latest"
    status: InstanceStatus        # pending/running/paused/completed/failed/cancelled
    title: str = ""
    created_at: str; updated_at: str
    # 溯源（沿用 TaskMeta 语义，更通用）
    source: TaskSource | None = None      # user/wolong/gate/cron/retro
    driver: Literal["manual", "wolong"] = "manual"   # 谁在驱动这条实例
    round_id: str | None = None           # 卧龙 driver 时关联 round
    parent_instance_id: str | None = None
    intent_key: str | None = None         # 派发幂等键

class InstanceState(BaseModel):
    meta: InstanceMeta
    inputs: dict = {}             # input 节点初始数据（链接/对标账号/选题）
    steps: dict[str, StepState]   # key=step_id
    canvas_state: dict = {}       # web 画布布局（纯视图，不影响执行）
    selected_choices: dict = {}   # step_id -> choice（如 rw 选了哪个 model）
```

Recipe（DAG 骨架，纯数据 + 字符串 key）：

```python
class RecipeStep(BaseModel):
    step_id: str
    label: str
    cmd: str | None = None        # 命中 build_full_registry() 的 key（render_final_preview/tts/...）
    agent: str | None = None      # agent 步（liuyong/wudaozi/guiguzi/boya）；与 cmd 二选一或同源
    deps: list[str] = []          # 前置步骤（拓扑约束，如 tts 依赖 storyboard 定稿）
    expensive: bool = False       # 标"贵步骤"，driver 据此决定是否在它前面强制闸门
    intervention: Literal["content_edit", "decision_only", None] = None
    material_source: Literal["generated", "collected", None] = None  # figure_talk: collected

class Recipe(BaseModel):
    recipe_id: str
    name: str; description: str
    steps: list[RecipeStep]       # 有序
    template_renderer: str        # "final_preview"/"figure_talk"/"stickman"

RECIPE_REGISTRY: dict[str, Recipe]   # final_preview + film rebuild/highlight；figure_talk 等后续入册
```

---

## 3. 引擎 vs app 子系统 边界（研究共识）

| 组件 | 裁定 | 说明 |
|---|---|---|
| web `_execute_{asr,rw,lines,storyboard,tts,image,render}` | **进引擎** | 全是生产步骤；render 早已共用 `render_final_preview.run`。改成统一 step + 经 registry 派发 |
| **沈括(采集)** / 鬼谷子(语义清洗或选题) / 柳永/吴道子/伯牙 | **进引擎（作为步骤执行者，全在卧龙麾下）** | 卧龙指挥的五个 agent 就是生产链的步骤 performer；recipe 按 id 晚绑定。沈括统一采购外部原料；film 作品链接由沈括确定性 OCR，鬼谷子再清洗成解说稿，其他 domain 保持文案/评论采集与选题行为 |
| `TaskStore` / `TaskRunner` / `EventBus`(SSE) | **进引擎** | 是统一 store/调度/事件的底层，扩展而非替换 |
| `PipelineRunner` + `JobState` + `video-jobs/` | **退役** | final_preview DAG 是一条 recipe 的特例；被 InstanceStore 的 recipe/steps 包容 |
| **卧龙** | **保留为可插拔 driver** | 掌编排机械（派沈括→鬼谷子→柳永→…→render、状态推进/事件消费），**决策权归人**（走 review 路由→rounds_gate）；与 web 手动 driver 并列 |
| **自治神经层**：`subscriptions`(传感器) / `planner`(排产) / `retro_trigger`+`wolong_retro`(学习) / `rounds_gate`(接线) / `label_store`(案卷) | **app 子系统（在卧龙之外）** | **触发/支撑**卧龙、但不是他指挥的 agent：订阅决定"何时该让沈括刷新对标号"、排产决定"何时开工/补选题库"、学习闭环喂 rubric、接线/案卷做事件与训练数据。独立协程 + 独立配额桶 |
| 介入点机制（content_edit / decision_only） | **引擎提供原语，driver 用** | 引擎给状态机+草稿+审看字段；闸门**逻辑**在 driver（卧龙 round / web 手动） |

**纪律**：卧龙只掌编排，绝不内化决策；人决策一律经 review 路由进 `rounds_gate`。这条保证了"两个 driver"
共用一套引擎而不互相渗透。

---

## 4. 步骤生命周期 + 介入点

扩展状态机（统一 web 节点 + app 任务）：

```
idle → queued → running → draft_ready
                              │
              ┌───────────────┴───────────────┐
        intervention=None              intervention!=None
              │                               │
            done                       awaiting_review
                                              │
                        ┌─────────────────────┼──────────────────────┐
                   approved                 edited(web)            rejected
                   (app 批)              (改 draft→user 源)        (打回)
                        │                     │                      │
                       done ◄────────── rerun/finalize          rework/skip
```

- **每步先出草稿**（`draft`），`intervention=None` 的步骤直接 `done`；有介入点的进 `awaiting_review`。
- **web 介入 = `content_edit`**：改 `draft` 内容（beats / 文稿 / prompt），`draft_source="user"`，可触发该步重跑或直接定稿。
- **app 介入 = `decision_only`**：`approved` / `rejected` + note，写 `review`，落 `label_store` 案卷（`reviewer=user` 才进训练集）。
- **闸门不止柳永——每个 agent 产出都能挂闸**（D4 本意）：柳永(成稿)、吴道子(分镜)、伯牙(声音)的产出都需可被人验收/打回，
  尤其作为"挡在烧钱步骤前"的**强制闸**——生图（吴道子下游）、tts（伯牙）、render 是贵步骤，闸门插在它们**之前**，
  把低质内容挡在烧钱之前。哪些步带 `intervention` / 哪些 `expensive` 由 **recipe 声明**，闸门**逻辑**由 driver 执行
  （不同 recipe/round 可不同）。典型 final_preview 链的闸门点：柳永后、吴道子后(生图前)、伯牙后(tts 后)、render 后。
- **反馈回喂**：每个闸门的 `review` + 定稿差异 → `label_store` → `retro` 学 rubric → 注入下一轮 `liuyong` brief / `prescreen`（§9）。

---

## 5. 多配方

- Recipe = 有序 `RecipeStep`，每步绑 `cmd`/`agent`（字符串）、`expensive`、`intervention`、`material_source`。
- 当前已注册 **final_preview**、**film_highlight_v1**；
  **figure_talk 剪影**仍是 E3 / cold chain 范围。
- **素材来源**是步骤内策略：`material_source="generated"`（gpt-image 生图，final_preview）vs `"collected"`（沈括切素材，figure_talk）——不冲突、按 recipe/step 配。
- 新风格 = 新 recipe + 新 `template_renderer`，不动引擎。

### 5.1 film frame-first rebuild v1

`film_highlight_v1`：

```text
input -> source -> highlight_plan -> storyboard -> edl_review
      -> tts -> voice_review -> render -> quality -> download
```

节点边界：

- `source` 只注册调用方依法提供的对标视频和 clean master，做真实 ffprobe/hash；系统不搜索或下载电影原片。
  多音轨 master 由可选 `master_audio_stream` 选择 audio ordinal，codec/language 与 ordinal 一起固化进 artifact。
- 沈括的 `film_script_source.v2` 可作为独立清洁中文脚本输入；highlight 不依赖鬼谷子、柳永或翻译。
- `storyboard` v1 不做自动视觉匹配，只消费调用方/人工确认的 ms EDL 或 frame EDL，统一输出
  `film_frame_edl.v1` 半开 frame ranges 与 backward/overlap/low-confidence review 诊断。
- `tts` v1 不调用在线 TTS/voice cloning，只把已生成 voice 规范化成 48 kHz stereo PCM stem，
  并可注册 narration ASS/SRT。
- `render` 始终按 clean master global frames 直接 trim；原片 bed 也按相同 frame time 裁切，只有
  真正不连续的 source cut 添加 10-15 ms fade，然后做 sidechain duck/mix。禁止从 reference
  音轨取 bed，禁止 `tpad=clone` 和无意义 `atempo=1`。ASS/SRT 在 FFmpeg 有 libass filter 时烧录；
  缺少 filter 时默认保留 artifact + warning，严格交付可设 `require_burned_subtitles=true` 硬失败。
  输出剥离 clean master 的 metadata/chapter/data/subtitle streams；CFR 参数兼容新旧 FFmpeg，且不以
  `-frames:v` 提前终止 mux，避免旧版 AAC encoder 的尾包未 flush。
- `quality` 做真实 ffprobe + full ffmpeg decode，硬校验 expected frame count、CFR/fps、目标时长
  一帧误差、音轨存在与时长。

highlight 仍直接引用 clean master frame EDL，不能从已渲染 MP4 二次裁切。

### 5.2 film artifact lineage

film v1 每步 manifest 固定落 `job_dir/film_rebuild/{source,storyboard,voice,render,quality}/`。
artifact ref 至少有 `artifact_id/kind/schema_version/uri/sha256/size_bytes/producer_step/`
`producer_version/input_artifact_ids/metadata`；`uri` 相对 `job_dir`，下游只通过 manifest 解析，
不靠约定绝对路径猜文件。render manifest 顶层再次钉住 master/EDL/voice/subtitle 的 artifact IDs；
QA report 反向钉住 render artifact ID，形成可复现 lineage。

---

## 6. 统一 store / id / 多租户

布局（扩展现有 `state/tasks` 结构，不另起炉灶）：

```
state/instances/{instance_id}/
├── meta.json                 # InstanceMeta
├── inputs.json               # 全局输入
├── canvas_state.json         # web 画布（纯视图）
├── instance_events.jsonl     # 实例级事件
└── steps/{step_id}/
    ├── state.json            # StepState
    ├── events.jsonl          # 步级事件（progress/draft/decision）
    ├── draft.json            # 草稿（web 可改）
    ├── outputs/              # 该步产物（替代 video-jobs/{job}/NN_*）
    └── reviews/{review_id}.json
state/wolong/labels/          # 案卷库（label_store，独立生命周期）
state/recipes/                # recipe 定义（或内置代码）
```

- **instance_id** = `i_<ms>_<hex8>`。目标态会兼容读旧 `task_id`(`t_*`)/`job_id`(12-hex)，让旧 URI（`GET /tasks/{id}`、`/jobs/{id}`）映射到 instance；当前 web/app 还分别以 `/jobs`、`/tasks` 为主路径。
- **多租户**：`owner_id` 入 schema（演示 `None`）；隔离由**路由中间件**校验（`auth_user != instance.owner_id → 403`）+ `list_instances(owner_filter)`，**不在 store 里硬编码鉴权**。生产接入鉴权后中间件填真 `owner_id`。

---

## 7. 分层 SSE / 两视图

一条统一事件流，按 `level` 分层订阅：

```
event levels: meta(实例状态) | step(步骤状态/决策) | detail(progress/draft 微更新)
app  订 ?level=meta,step          → 只看决策级，不被内容微更新刷屏
web  订 ?level=meta,step,detail   → 看到逐字进度 + 草稿变更，支撑画布实时
```

- **web 目标视图**：画布 + 每步抽屉（rw 4 模型选稿 / lines beats 编辑 / storyboard 场景网格 / image·tts 预览重生 / preview iframe 原地改 episode.json）。当前仍经 `/jobs` facade 间接复用 engine。
- **app 目标视图**：实例列表（作品=agents 产出物的另一种视角）+ 决策卡（approve/reject + note）。当前仍经 `/tasks` / TaskRunner 展示 agent 任务。
- 目标态下同一批 instance，两视图看同一套 `steps` 状态；人的反馈都汇聚到 `rounds_gate`。

---

## 8. 两个 driver（编排策略可插拔）

引擎只负责"执行一个步骤 + 维护实例/步骤状态 + 推事件"。**谁决定下一步跑什么 = driver**：

- **manual driver（web）目标态**：人在画布上点"跑这步 / 改这步 / 重生这步"，逐节点推进。`meta.driver="manual"`。当前 web 仍通过 `/jobs` facade 调用。
- **wolong driver（app）目标态**：卧龙派单→沿 **沈括(采集)→鬼谷子(选题)→柳永(编剧)→吴道子(美术)→伯牙(声音)→render**
  续跑循环消费 `rounds_gate` 事件自动推进，人只在闸门处 `decision_only`。`meta.driver="wolong"`，`round_id` 关联。

两个 driver 调同一套 `InstanceRunner.run_step(instance_id, step_id, inputs)`。这正是"一套 runtime + 可插拔编排策略"。

**沈括 = 统一的"外部原料采购"边界**：凡是"从外面拿原料"都走沈括，他对下游屏蔽来源差异、只交付统一"原料"
（转写稿/文章/字幕/对标数据），鬼谷子/柳永们不关心怎么拿到的。沈括按输入选**采集模式**：

| 用户给的输入 | 沈括的模式 | 之后 |
|---|---|---|
| **对标账号** | 爬取采集（下载+转写+截帧抠图，落共享池） | →鬼谷子(选题)→柳永→… |
| **普通作品链接** | 单链 ASR（只转写这一条） | →柳永(改写/编剧)→… |
| **film 原片/作品链接** | 下载/缓存 + 2fps 底部字幕 OCR；ASR timeline 仅作辅助 | 沈括内完成中文校正与时序去重，直接交付 clean script |
| **选题/一句话想法** | 无（没有外部原料可采，跳过沈括） | 直接 →柳永(或先鬼谷子提炼) |

> 即"链接→asr"这个裸节点不再独立存在，它是**沈括的单链模式**。film 是这一模式的
> 字幕采集分支：OCR cue 是唯一真源，ASR 不能覆盖 OCR，只能帮助沈括校正同音字和标点。
> 原始与 clean artifact 都固定落 `state/works/{platform}/{id}/film_subtitles/`。

> **沈括的"共享池"性质**：采一个对标账号的数据可复用于很多选题/作品，所以"采集"步骤**缓存感知**
> （命中已采账号则复用，不重采）。它仍是卧龙麾下的生产步骤，只是产物落共享池（`state/benchmark`），
> 不是每条实例都重采。〔实现期开放点：采集产物按"实例内步骤产物"存还是"共享池+实例引用"存——E3 定，见 §13〕

---

## 9. 补完 agent→render 闭环 + retro 学习（D5 范围）

**现实（研究实测）**：app 的卧龙 round 今天走到**成稿就停**；"成稿→成片"渲染段（旧 `CONVERGENCE` 设计的
`job_driver.py`）**从没建**；`wolong.mode="retro"` 是 `NotImplementedError` 桩。真正能到 mp4 的只有 web final_preview 链。

**本设计如何补完**（这是统一引擎的副产品红利，不再需要单独的 job_driver 把卧龙焊到 final_preview pipeline 上）：

1. **agent→render**：卧龙 driver 的 round line 沿全链（闸门见 §4，挡在贵步骤前）
   **沈括(采集)→鬼谷子(选题)→柳永(成稿)→〔闸〕→吴道子(分镜)→〔闸·生图前〕→伯牙(声音)→〔闸〕→render→〔闸〕**
   推进（入口随输入类型，见 §8）——今天断在"成稿"，本设计把后半段（分镜→声音→render）补成同一引擎的后续步骤，
   不需要第二套渲染管线。round line 的 `instance_id` 就是那条生产实例。
2. **retro 学习**：`retro_trigger`（夜间窗口 + 样本闸）→ 读 `label_store` 案卷 + done 战报 → opus 学 rubric →
   `state/wolong/rubric/v{n}.md` → 注入下一轮 `liuyong` brief 与 `prescreen`。把现有桩补成实链。

---

## 10. 迁移分期（目标路线：路径 C；带检查点）

> C 选了"最快见效、风险最高"。即便如此，**每个 milestone 都可验证、可回退**；护城河 = `main` 有 web 旧画布
> 可跑副本，本 branch 不并 main 就毁不掉它。**每步退出标准必含"全量 pytest 不掉绿"。**

| 期 | 做什么 | 退出标准（检查点） |
|---|---|---|
| **E0 引擎骨架** | 建 `server/engine/`：`types.py`(Instance/Step/Recipe) + `instance_store.py`(扩展 TaskStore 布局) + `instance_runner.py`(经 `build_full_registry()` 派发单步 + 状态机 + 分层 SSE)；`recipes.py` 把 final_preview 表达成一条 Recipe。**先不接任何视图** | 引擎可独立跑通"建实例→跑 render 步（已共享）→出事件→落 store"；新单测覆盖状态机 + 晚绑定派发；pytest 绿 |
| **E1 web 整条迁上引擎（C 的主刀）** | 目标是把 `PipelineRunner._execute_*` 七步逐个改成"引擎步骤执行者"（asr/rw/lines/storyboard/tts/image 复用其现有实现，但纳入 step 生命周期 + 经 registry），再让 `routes/{jobs,pipelines,preview}` 重指引擎（`/instances` + 兼容 `/jobs`），最后 `web/` 前端走新 instance API。当前只完成 facade strangler：web UI 仍走 `/jobs`，多数节点经 facade 进 engine，`asr` 仍 legacy。 | **目标检查点**：web 画布端到端冒烟：贴链接→逐节点→出 mp4，与旧行为对齐；`content_edit`（改 beats/prompt）通；旧 `job_id` 兼容读通；pytest 绿；**`PipelineRunner` 退役** |
| **E2 app driver 上引擎** | 卧龙 driver 调 `InstanceRunner.run_step`，沿 沈括→鬼谷子→柳永 链派发；`rounds_gate` 接 review→decision；`/tasks` 兼容读映射到 instance；app 决策视角订 `level=meta,step` | 卧龙 round 跑通到**成稿+验收**（含采集/选题入口），task 兼容读通；自治神经层(订阅/排产/学习) 不动照常；pytest 绿 |
| **E3 补完 agent→render** | round line 续到 吴道子→伯牙→render（同引擎后续步骤）；figure_talk 作第二条 recipe 入 `RECIPE_REGISTRY`；定采集产物存法（实例内 vs 共享池） | 卧龙 driver **端到端出 mp4**（采集→选题→成稿→分镜→声音→成片）；多 recipe 可选；贵步骤闸门生效 |
| **E4 retro 学习闭环** | 补 `retro_trigger`→`label_store`→opus rubric→注入 liuyong/prescreen 实链（去桩） | 标注样本攒够→夜间复盘出新 rubric→下一轮注入可观测；闭环跑通 |
| **E5 收口** | 删 `JobState`/`PipelineRunner` 残留 + 旧 `/jobs` 双线 + `video-jobs` 迁 `state/instances`；更新 `.project_map` + 文档 | 全仓单运行时；两视图同源；冷启动 OK；pytest 全绿 |

### E0 as-built + E1 driver API as-built（评审加固后）
**E0 已落地**（commit `b2e66e3` + 评审加固）：`server/engine/{types,recipes,instance_store,instance_runner}.py`；
按实例 `asyncio.Lock` 串行化、配方自检（悬空 deps/环/重复）。

**E1-a 引擎 driver API 已落地**（纯增量、零接视图；当期全量测试通过，24 新单测）。原"待补"项均已实现：

- ✅ `approve_step(iid, step_id, decision, *, edited_draft=None, note=None, reviewer="user")`：`awaiting_review` 出口——approved→定稿（`outputs`=最终草稿、`done`）、rejected→`rejected`（rework/skip 留给 driver 后续 `reset_step`/略过）；两路都写 `Review` + 发 `decision` 事件。
- ✅ `get_runnable_steps(iid)` + `get_step_output(iid, step_id)`：拓扑序返回 idle 且 deps 全 done/skipped 的步；取定稿 `outputs` 供 driver 装配下游 `step_inputs`。
- ✅ `reset_step(iid, step_id)`：硬重置该步 **及全部传递下游** 为 idle（保 `config`、清运行态），running 步拒绝；重置后重算 meta（已结算实例被重激活为 running）。
- ✅ `finalize_instance(iid)`：据各步终态置 meta——任一 failed→failed / 全 done·skipped→completed / 全 idle→pending / 否则 running；发 meta 事件。
- ✅ `run_step(..., config=...)`：config 作**溯源**落 `StepState.config`（步骤真正启动时）。⚠️ **不** splat 进 performer——真实命令闭合签名（rw 的 model 在 `payload` 里、非顶层 kwarg），driver 负责把选择折进 `step_inputs`（与 TaskRunner "调用方建好 params" 契约一致）。

**E1-a 评审加固**（2 轮对抗审查 + 逐条对抗复核）——抓出并修掉 6 条 E0 遗留/本期引入的真缺陷：
1. **闸门判据从 `performer` 改回 `intervention`**（blocker）：原 `_run_step` 的 `performer is None→done` 早返回在 intervention 判定之前，致 final_preview 的 content_edit 步被静默直通、强制闸门失效。现无 performer 但有 intervention 的步也停 `awaiting_review`，以上游 `step_inputs` 作初始草稿。
2. **守门提到副作用之前**（major×2）：抽 `_start_running` 先校验 `idle/queued→running` 合法、再翻 meta running / 落 config——非法重跑（对终态步）是干净的 no-op 失败，不再把已结算实例 meta 永久翻 running、也不脏写 config。
3. **config 不再 splat 进 performer**（major）：见上 `run_step` 条。
4. **直通步真正推进时才翻 meta running + 记 config**（minor×2）：消除"首步后 meta 滞留 pending"与"passthrough 漏记 config"两处不一致。

**E1-b1 /instances HTTP 路由已落地**（纯增量，不动 /jobs /tasks /pipelines；当期全量测试通过，含 21 条 /instances 路由单测）：
- `state.py` 接 `INSTANCE_STORE`/`INSTANCE_RUNNER` 单例（与 TaskRunner 同款 mock 门 + 内置 RECIPE_REGISTRY）。
- `routes/instances.py`：`GET/POST /instances`、`GET /instances/{id}`(+`/runnable`)、`POST .../steps/{sid}/{run,approve,reset}`、`POST .../finalize`、`GET /instances/{id}/events?level=` SSE（订内存总线、分层）。
- 错误映射：404=实例/步/recipe 不存在；409=状态机不允许（非法重跑/非 awaiting/运行中重置）；400=坏 recipe；引擎把 performer 异常收成 step.failed → HTTP 200 + `status=failed`。
- 经对抗审查加固 5 条：runnable 漏 KeyError→500、SSE 断连漏 unsubscribe（snapshot yield 挪进 try）、approve 未经 recipe 预检裸调 store、body-less `/run` 422、store 读入口补 iid/step_id 白名单。

**E1-b1 范围取舍（b2 补）**：`/run` 同步 await（贵步骤后台派发待 b2）；SSE 只推 meta/step（detail/progress 走 jsonl、tail-merge 待 b2）。

**E1-b2 slice-1 后端验证已落地**（纯增量、hermetic；当期全量测试通过）：
- `server/engine/pipeline_performers_final.py`：把 web `_execute_lines`/`_execute_storyboard` 的 opus 结构化算法**原样复用**成引擎 step-performer（经 `_opus_structure` 间接层可注入桩；保留 `video-jobs/` + 共享 `02_rw/episode.json` 耦合，去掉 PipelineRunner 的 self.* 状态管理交引擎接管）。
- 集成测试 `test_pipeline_performers_final.py`：引擎按真实 final_preview 拓扑（rw/lines/storyboard/preview 四道 `content_edit` 闸门经 `approve_step` 放行）驱动 lines/storyboard 真实 performer + asr/rw/tts/image/render 桩，经共享 episode.json **端到端出 mp4**——证明引擎能编排真实 final_preview 链 + 文件系统耦合 + 闸门，不依赖真 opus/node/ffmpeg/模板样例素材。
- 评审加固：补回 storyboard `groups_count`（与 web `StoryboardOutputs` 契约对齐）；e2e 闸门断言改 load-bearing（断真正 fire 的 content_edit 步 == recipe 声明集，删任一 intervention 即变红）。

**E1-b2 全部 7 步 performer 已真实包装**（lines/storyboard/tts/image/render/asr/rw；当期全量测试通过）：
- `run_*_step`（`pipeline_performers_final.py`）：忠实复刻 `_execute_*` 编排，复用模块级 helper（shenkuo.collect_one / tts_gen / generate_scene_image / rebuild_tts_items / render_final_preview.run / invoke_rw_candidate / MODEL_CANDIDATES），外部副作用经 seam（`_collect_one`/`_run_tts_gen`/`_gen_scene_image`/`_render_run`/`_invoke_rw`）注桩。job_dir/urls/asr_items/profile 经 step_inputs 传入、保留 video-jobs 布局；pipeline_runner 未动（编排暂双份，/jobs 退役时去重）。
- **模型差异**：web 的 mid-run 增量进度（`_push_outputs_patch` item_progress / model_progress / 增量 drafts）暂不复刻——引擎当前只在步末设 outputs，信息经 on_progress 文本透出；引擎加增量 outputs 后再补。asr 已改为与 legacy fast collect 同源的 `collect_one` 口径；rw 4 模型 async 并发（同步 performer 内 `asyncio.run` 跑原 `gather`）忠实保留。
- 单测：每步覆盖正常 + 失败/边界（image 异常/全失败、render picture_dir 转发、asr collect_one 快采/元数据兜底/单条失败/全失败/取消透传、rw 部分成功/全失败/profile/code-fence）。经 3 轮 fidelity+coverage 对抗审查加固。

**E1-b2 全局 final_preview recipe 绑定已落地**：`recipes.py` 的 FINAL_PREVIEW 各执行步 cmd 已从 bare command
重绑到 `final_*` orchestration performer；`server/state.py` 的 `INSTANCE_RUNNER` registry =
`build_full_registry()`(含 mock 门) ∪ `PERFORMERS_FINAL`。`preview` 仍是无 performer 的 content_edit 人工闸。
带生产 wiring 守护测试（final_preview 每个执行步的 performer 必须在生产 registry 解析）。

**E1-b2 #3 绞杀者（strangler）已落地，但不是前端直连 `/instances`**：`PipelineRunner` 经
`attach_engine(INSTANCE_RUNNER)` 注入引擎；`NOF_ENGINE_NODES` 控制哪些节点从 `_execute_*` 改走
`InstanceRunner.run_step`（经合并 registry 派发到 `final_*` performer + 引擎状态机）。
当前默认行为是：未设置 `NOF_ENGINE_NODES` 时初始化为 engine-safe 节点集合，`lines/storyboard/tts/image/render`
经 facade 走 engine；`asr` 与 `rw` 因执行处分支固定走 legacy。设置
`NOF_ENGINE_NODES=""|"none"|"off"|"legacy"` 可全量回旧路径；设置逗号列表时只让列出的非 `asr`/`rw` 节点走 engine。
**UI/`/jobs` 契约不变**——JobState 仍是 facade 真相源，每 job 复用一个引擎实例（iid 持久化在
`pipeline_state.json`，重启不留孤儿）；content_edit 步（lines/storyboard）跑出 awaiting_review 后由
facade 自动定稿；performer 的 on_progress 经 `run_step(on_progress=)` 回桥到 facade SSE（避免
storyboard/image/render running 态进度冻结）。

**Worker 拆分(S3)已落地**：离线 TaskRunner 任务执行从 8810 拆到独立 `nof-worker` 进程，Redis 承担队列、配额、inflight 与任务执行状态协调；server 只生产，worker 通过 Redis claim 消费并回写 completed/failed/cancelled。重启 8810 不打断在跑任务，Redis 不重启时任务调度状态连续（详见 `backlog/docs/S3-redis-worker-design.md`）。画布 run_node / final_preview 引擎 run_step 已通过 `pipeline_node` 任务迁到 worker 路径。

**E1-b2 仍待补**（路径 C 高风险段，下一步）：
- asr 若要改道引擎，需先给引擎补**步内增量 outputs**（asr `item_progress` / collected 渐进推送）并覆盖 done 后后台 enrich，否则会丢实时采集进度与补音轨/抠图行为。
- rw 当前按 task-3.3 固定走 legacy，以保留逐模型实时面板；若未来要再次改道引擎，需要先补引擎步内增量 outputs（rw `model_progress` / 增量 drafts）。
- 全切换：前端直走 `/instances`（退役 /jobs facade）+ 贵步骤后台派发 + 旧 `job_id`/`task_id` → `instance_id` 兼容适配层（§6）。
- 旧 `job_id`(12-hex)/`task_id`(t_*) → `instance_id` 兼容适配层（§6）。
- 保留：SSE 满队列丢事件（与现 PipelineRunner 同款取舍，客户端 GET 全量重同步）。

**E2 app `/tasks` → engine 迁移草案（task-3.1）**：
1. 先补 engine 跨进程事件总线：把 `InstanceRunner.EngineEventBus` 从进程内 `asyncio.Queue` 扩展为
   `events.jsonl` tail/merge，否则 S3.x 把 engine 步骤迁到 worker 后，8810 的 `/instances/*/events`
   收不到 worker 侧 `_emit`。
2. 引入 task/instance 兼容层：`POST /tasks` 为现有 app contract 保持不变，但内部为可生产类 agent
   创建 `ProductionInstance`，把 `task_id` 映射到 `instance_id`，`GET /tasks/{id}` 兼容读 instance meta/step。
3. 迁卧龙 driver：让 wolong/rounds_gate 不再直接串 TaskRunner 化石命令，而是按 recipe 调
   `InstanceRunner.run_step()`，review 仍经 rounds_gate 落案卷；app 订阅 `level=meta,step`。
4. app 前端切读实例列表/决策卡：保留 `/tasks` 兼容读一段时间，新增 `/instances` 直连视图验证后再收口。
5. 最后清理：废弃 TaskRunner 中仅为生产链保留的旧命令入口，保留非生产工具任务或迁为 engine recipe/step。

---

## 11. 风险与缓解（研究的 blocker/major → 折入）

| 严重度 | 风险 | 缓解 |
|---|---|---|
| BLOCKER | 过渡期 web/app 两套 state 并行，人介入窗口不对齐（web 改 episode.json vs app task.result 已定） | E1 先把 web 收进引擎单一 store；E2 再切 app；**任一时刻只有一处是"真相源"**，另一处兼容读 |
| BLOCKER | `agent→render`、`retro` 是缺口/桩（旧 CONVERGENCE 没建完） | 明确列为 E3/E4 的**补能力**，不是搬代码（D5 已确认范围） |
| MAJOR | `_execute_*` 复刻实现与命令签名不一致（spawn vs import、on_progress 回调） | E1 逐步迁、每步冒烟；保留各步现有实现体，只套进 step 生命周期，不重写算法 |
| MAJOR | rw 4 模型可用性探测（`shutil.which`/env）在不同进程/环境下静默失败 | 探测结果入 `step.config` + 共享配置，不藏在节点里 |
| MAJOR | preview 的 `regen_*` 在 DAG 之外原地改、不级联失效 | 显式建模为"微步骤"（step 内重生），与整步重跑区分；写 `draft` 触发下游 `stale` 标记 |
| MAJOR | storyboard→tts 顺序依赖（tts 要 scene_id） | recipe `deps` 硬约束 + 引擎拓扑校验，driver 不能乱序派 |
| MAJOR | render_final_preview 是 Python 调用，并发下线程安全 | 引擎里 `to_thread`/子进程隔离跑贵步骤 |
| MAJOR | 多租户后期加 `owner_id` 易漏校验→串户 | 中间件统一校验 + `list/get` 强制 `owner_filter`；演示期 `None` 直通 |
| MINOR | recipe 版本 vs 旧实例 replay | `recipe_version` 钉在实例上，升级 recipe 不影响在跑实例 |

---

## 12. 与既有成果的关系

- **P1（抽 core，6 primitive + runners + pipelines(DAG 类型) + 模板015 + registry/cli 二分）全部保留**——core 在本架构里是最底层能力，且 `build_full_registry()` 直接成为引擎的晚绑定派发表。
- **三包对等拆分作废**（P2+ 不做）；本文档接任权威设计。
- **测试基线以执行当天为准**；每个 E-期退出标准含"不掉绿"，不要复用历史 passed 数作为当前事实。
- **操作安全网**：`main` 有 web 旧画布可跑副本；本 branch `claude/gallant-hellman-27de2a` 做重做，**不并 main**。

---

## 13. 仍需实施期确认的开放点（不阻塞 E0/E1）

- recipe 定义放代码内置还是 `state/recipes/*.json`（E0 选）。
- `instance_id` 是否保留 `t_<ms>` 时序形（便于按时间查）vs 纯 UUID。
- opus/scodex 等 CLI 在高并发下是否要 per-account 串行队列（E1 压测时定）。
- `figure_talk` 的 `material_source="collected"` 是 recipe 显式声明还是 wudaozi 内部选项（E3 定）。
- **沈括采集产物存法**：作"实例内步骤产物"存（每条实例自带）还是"共享池(`state/benchmark`) + 实例引用"存
  （采一次多条复用）——后者更省但要处理缓存命中/失效；E3 定。
- 旧 `video-jobs/` 存量 job 冷迁移（新建走新树）vs 热迁移脚本（E5 定）。
