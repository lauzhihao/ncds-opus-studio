# Monorepo 拆分设计：core / studio / factory（薄核心）

> 状态：**v1 PLAN（2026-06-13）。等用户 "Go" 再实施，本文档只产计划、不含已落地代码。**
> 产出方式：设计工作流（4 路分类/import 扫描 + 合成边界 + 2 视角对抗）。
> 对抗总评：**循环依赖视角 `has-blockers` / 运行时契约视角 `needs-revision`**——2 个 BLOCKER
> + 6 个 MAJOR 已折入 §3/§4/§5/§6；另有 **2 个决策须你拍板**（§0）。
> 取代 [CONVERGENCE-DESIGN.md](CONVERGENCE-DESIGN.md)（收敛方向已被"拆开"取代，该文档留作
> "为什么收敛很难"的证据）。前置阅读：[FRONTEND-API.md](FRONTEND-API.md)、[FEISHU-REFACTOR.md](FEISHU-REFACTOR.md)。

---

## 0. 决策回顾 + 两个待你拍板的点

**已定方向（你拍板）**：Monorepo + 3 package；**薄核心**——四步内容（分镜/配音/出图/渲染）
015 与 009 两套**暂不合并**，core 只共享 primitives。

**对抗逼出的两个新决策（用户已拍板，2026-06-13）**：

| # | 决策 | **已定** |
|---|---|---|
| D1 | **对外形态** | ✅ **A：顶层反代把 8810(studio)+8811(factory) 合并回单 origin**，iOS/Flutter base URL / `/openapi.json` 不变、端上零改动（§3） |
| D2 | **飞书边界** | ✅ **A：整条 `scripts/*.mjs` 链留 core 不拆 + 切掉 feishu IO 尾**，runner 只走 `on_progress` 回调（对齐 AGENTS.md"命令不发飞书" + FEISHU-REFACTOR，借拆分清历史债）（§4） |
| D3 | **app 前端拓扑** | ✅ **目标：Flutter app 进 monorepo 作 factory 前端**（`mobile/`），与 `web/` 之于 studio 对称；**不**把 factory 搬出去——那会把 core↔factory 紧耦合代码缝变跨仓、抵消 monorepo 初衷。**⚠️ "挪进来"动作暂缓**：另一 agent 正在 `~/Documents/claude_traffic_light_flutter` 做 iOS→Flutter 迁移设计，该目录由其负责，**本计划不碰它**；relocation 从 P0 解耦，待其迁移落定后单独执行（§5 M-mobile）。**协调点**：factory 对外 OpenAPI 契约（[FRONTEND-API.md](FRONTEND-API.md)）必须保持稳定，那是该 Flutter 迁移的消费目标——D1 单 origin 反代正是为此 |

定调：**两个产品各自全栈（studio=后端+`web/`，factory=后端+`mobile/`）、都在同仓**；产品形态在
后端隔离，但对外仍一个入口——最贴合"产品隔离、能力复用"的本意。
**关键原则**：让贵的缝（core↔factory 的 Python import）同仓，让稳定的缝（前端↔后端 OpenAPI 契约）
即便跨端也无妨。

---

## 1. 三包边界

```
ncds_opus_core   (纯能力库, 无 server, 不依赖任何产品层)
   ↑ 单向依赖                      ↑ 单向依赖
ncds_opus_studio                ncds_opus_factory
(画布产品: 后端 + web/ React)    (卧龙工厂产品: 后端 + mobile/ Flutter)
   ※ studio 与 factory 之间零 import（已 grep 复核：两套 runner 路由层零交叉）
   ※ 两个产品各自全栈，前后端同仓；前端↔后端只经 OpenAPI 契约耦合
```

| 包 | 角色 | 主要内容 |
|---|---|---|
| **ncds_opus_core** | 两端复用的纯能力 | primitives：`wst/tst/vid/asr/rw/tts/render/render_015` + `PRIMITIVE_REGISTRY`/`PRIMITIVE_SCHEMAS`/primitive CLI；中性 `common`：`node_runtime/tts_provider/public_upload/lark_cli/cancel`；`pipelines/`(DAG 类型 `PipelineDef/PipelineNode`+015 定义)；`templates/paper_card_talk_015`(render_015 复制它)；`gpt_image/`；`server/artifact_url.py`(新，纯 URL/路径安全工具)；`scripts/*.mjs` runner 链(见 §4) |
| **ncds_opus_studio** | 画布产品（依赖 core） | `server/`：`pipeline_runner`(015 节点执行)、`storyboard_director`、`mock`(画布 job)、`dev_proxy`、`state`(只 `PIPELINE_RUNNER`+`VIDEO_JOBS_DIR`)、`app`(create_app)、`artifacts`(只开 `video-jobs/` 根)、`routes/{jobs,pipelines,preview,mock,templates,artifacts}`；`web/` SPA |
| **ncds_opus_factory** | 卧龙工厂产品（依赖 core） | agents：`shenkuo/guiguzi/liuyong/wudaozi/boya/wolong/wolong_rounds/wolong_retro/prescreen` + `AGENT_REGISTRY`/`AGENT_SCHEMAS`/agent CLI；factory `common`：`round_store/topic_store/benchmark_store/signals/rubric_store/quality_rubric/ai_taste/tikhub_client`；`templates/{paper_card_talk(009),figure_talk,stickman,reading_confidence*}`；009 `.mjs` runner；`server/`：`task_runner/task_store/rounds_gate/label_store/planner/subscriptions/retro_trigger/mock_agents/schemas/state(STORE/LABELS/RUNNER)/app/artifacts(state 根+extract_artifacts)/routes/{commands,tasks,rounds,subscriptions,artifacts}`；**`mobile/`（Flutter app，factory 前端，对外经 OpenAPI 契约连 factory-server）** |

**零环铁律**：core 内**绝不** import server/ 或任何 agent（P1/P5 用 `grep wolong\|shenkuo\|guiguzi core/` 必须为空）；
studio↔factory 互不 import。`schemas.py`（TaskMeta/Review/source/reviewer/round_id…全是卧龙语义）
**归 factory**，不是共享契约（studio 用 dataclass `JobState/NodeState`，不碰它）。

---

## 2. Bridge 模块拆法（同时服务两端、必须一分为二）

| 文件 | 问题 | 拆法 |
|---|---|---|
| `server/app.py` | 一处 include 10 router + startup 接两套协程 | 拆 `studio/server/app.py`(jobs/pipelines/preview/mock/templates + studio /artifacts + mount `/studio` + dev_proxy；**无 factory 协程**) 与 `factory/server/app.py`(commands/tasks/rounds/subscriptions + RUNNER/rounds_gate/清扫/对账/planner/retro 协程) |
| `server/state.py` | 唯一物理单点：同时造 `RUNNER`(factory)+`PIPELINE_RUNNER`(studio) | 删除，拆 `studio/server/state.py`(只 `PIPELINE_RUNNER`+`VIDEO_JOBS_DIR`) 与 `factory/server/state.py`(`STORE/LABELS/RUNNER`+`STATE_DIR`，RUNNER merge core `PRIMITIVE_REGISTRY`) |
| `server/artifacts.py` | `ALLOWED_ROOTS=('state','video-jobs')` 两根混服务；URL 工具两端用；`extract_artifacts` 全是 agent 分支 | 三分：①纯工具 `kind_of/validated_rel/file_url/dir_url`→`core/server/artifact_url.py`；②`extract_artifacts`+`ALLOWED_ROOTS=('state',)`→`factory/server/artifacts.py`；③studio 自留 `ALLOWED_ROOTS=('video-jobs',)` 的薄 artifacts |
| `routes/artifacts.py` | `/artifacts/files\|dir` 两端都要、白名单根不同 | 随模块拆两份，各只开自己的根，共用 `core.artifact_url` 的路径安全校验 |
| `commands/__init__.py`(`COMMAND_REGISTRY`) | eager 加载 8 primitive + 6 agent 到同一 dict = core↔factory 环 | 拆 `core/commands/registry.py`(`PRIMITIVE_REGISTRY`) 与 `factory/commands/registry.py`(`AGENT_REGISTRY` + `build_full_registry()` merge) |
| `command_schemas.py` | 单 dict 混 primitive/agent | 按 group 二分：`PRIMITIVE_SCHEMAS`→core、`AGENT_SCHEMAS`→factory；factory `/commands` 暴露 merge〔MINOR 修复：断言 `GET /commands/render_015/schema` 与 `/wst/schema` 在 factory app 返 200〕 |
| `cli.py` 分发 | `python -m` 同时分发 primitive + agent 子命令 | `core/cli.py`(primitive 子命令，入口 `nof-core`) + `factory/cli.py`(agent 子命令，入口 `nof`/保留旧名)；**勿给 render/render_015/tts 误加 CLI 分支**（它们仅 server 暴露） |

---

## 3. 两个 server + 对外形态〔BLOCKER D1〕

**问题**：[FRONTEND-API.md](FRONTEND-API.md) 通篇单 base（8810）、明示"端点签名真源=`/openapi.json`"，
即**一份 OpenAPI 覆盖两端全部端点**；web SPA 的 vite proxy 也把 8 个前缀全转单后端。拆成
8810+8811 后，iOS base URL、`/openapi.json`、dev proxy 全断。

**拆分形态**：
- `studio-server`（`ncds_opus_studio.server.app:create_app`）：端口 8810，挂
  jobs/pipelines/preview/mock/templates + studio `/artifacts`(只 video-jobs 根) + `/studio` SPA，
  构造 `PIPELINE_RUNNER`，**无后台协程**。
- `factory-server`（`ncds_opus_factory.server.app:create_app`）：端口 8811(`NOF_FACTORY_PORT`)，
  挂 commands/tasks/rounds/subscriptions + factory `/artifacts`(只 state 根) + RUNNER/清扫/对账/planner/retro。

**对外形态（D1，须拍板）**：
- **选项 A（推荐）**：顶层**反代/网关**把两端口合并回**单 origin 单 base**
  （`/jobs|/pipelines|/preview|/mock|/templates|/studio`→studio；`/tasks|/commands|/rounds|/subscriptions`→factory；
  `/artifacts`→按 relpath 前缀分流或各挂子路径）。iOS base URL 与 `/openapi.json` **不变**。
  P4 退出标准加"单 origin 反代冒烟"。
- **选项 B**：iOS 迁到 factory 新 base、web 连 studio，`/openapi.json` 分成两份。端上要改、契约要重写。

**配套修复**：
- 〔MAJOR〕`web/vite.config.ts` proxy 表改：studio 前缀指 8810、factory 前缀(`/tasks /commands /artifacts`)
  指 8811（studio 前端若不用就直接删这几条，避免误导）。
- 〔MAJOR〕§6 `kind=script` 回写到 `/jobs`：厘清两类 script——studio 管线稿(在 video-jobs/，回写成立)
  vs factory agent 稿(liuyong，在 state/，§6 回写**从来不成立**)。拆分后 factory script 走
  `/artifacts/files` **只读**；iOS 若确需编辑 liuyong 稿，在 factory app 新增 `PUT /artifacts` 编辑端点（不要继续指向 studio 的 `/jobs`）。
- 〔MAJOR〕**禁止 `parents[N]` 猜仓库根**（拆 src-layout 后深度全变、两进程不同 cwd 会指向不同
  产物根、artifacts 越权校验形同虚设）。两 `state` 的产物根改**强制读 env**
  （`NOF_VIDEO_JOBS_DIR`/`NOF_STATE_DIR`/`NOF_ARTIFACTS_ROOT`），缺则 **fail-fast**。

---

## 4. 飞书 / .mjs 纠缠的解法〔BLOCKER D2〕

**问题**：core 候选 `rw.py`/`asr.py` spawn `rewrite_command_runner.mjs`，后者 `import
'./feishu_sdk_adapter.mjs'`（产品层 IO）。若 asr/rw 进 core、feishu 进 factory →
**core→产品层成环**。且 `.mjs` 之间是 ESM `'./'` 相对 import，**跨包必断**
（`content_rewrite_runner→video_rewrite_runner/rewrite_profiles`）。

注意：asr/rw **两端都用**（studio 的 pipeline `asr/rw` 节点 spawn 同一批 runner，factory 的
`/asr /rw` 命令 + 沈括也用）→ 它们确属 core。

**解法（D2，须拍板）**：
- **选项 A（推荐）**：整条 `scripts/*.mjs` 链**留 core、不拆**（一个 `.mjs` 目录跟一个包走，
  彻底消除 `.mjs` 跨包 import 问题）+ **切掉 feishu IO 尾**：runner 不再 `import feishu_sdk_adapter`，
  改为只走 `on_progress` 回调把状态吐给调用方（**正是 AGENTS.md 设计原则**："命令本身不知道
  发飞书消息"）。这与 [FEISHU-REFACTOR.md](FEISHU-REFACTOR.md) 既定方向一致，是借拆分清历史债。
- **选项 B**：asr/rw + 其 `.mjs` 整体下放某产品，core 不含 asr/rw——但这违背"两端都用"的事实，
  会逼另一端反向依赖，不推荐。

> 备注：MIGRATION.md 称 `feishu_sdk_adapter` 已改造为 lark-cli（`grep open-apis scripts/*.mjs`
> 非注释为空），但 **import 边仍在**。选 A 时第一步先核 `feishu_sdk_adapter` 现在还做不做实质
> IO，能否直接摘除 import 还是要保留 lark-cli 进度上报。

---

## 5. 迁移分期（每期保持可跑通）

| 期 | 做什么 | 退出标准 |
|---|---|---|
| **P0** | 建 `packages/{core,studio,factory}` + 各 `pyproject.toml` + 根 `[tool.uv.workspace]`；**先不挪代码**，仅打通 `uv sync` 三包 editable + pytest 聚合；产出所有 `parents[N]` 硬编码路径清单（state.py `parents[3]`、render_015 `parents[2]`、pipeline_runner 多处） | `uv sync` 成功；三空包可 import；现有 test 全绿；硬编码路径清单每条标注迁后改法 |
| **M-mobile**（解耦，待定） | **不在主迁移序列里**。待另一 agent 的 iOS→Flutter 迁移落定后，把 `~/Documents/claude_traffic_light_flutter` 挪进 `mobile/`（与 `web/` 平级，不进 uv workspace，自带 `pubspec.yaml`）。本计划只占位、不执行 | `mobile/` 就位且 `flutter pub get` 通（由那条迁移收尾时做） |
| **P1** | 抽 **core**：8 primitive + `scripts/*.mjs` 链(切 feishu 尾) + `pipelines/`(DAG) + `templates/paper_card_talk_015` + `gpt_image/`(从 repo 根收进 core) + 中性 common + `artifact_url.py` + `PRIMITIVE_REGISTRY/SCHEMAS` + core CLI | `ncds_opus_core` 独立 import；`grep -r 'server\|wolong\|shenkuo\|guiguzi' core/` **为空**；primitive 单测(render_015 复制模板/tts/asr-rw spawn) 通过 |
| **P2** | 拆 **factory**：6 agent + wolong_rounds/retro/prescreen + factory common(round_store/topic_store/…) + 009 templates + task/round 服务层 + `AGENT_REGISTRY/SCHEMAS` | `factory` agent 侧可 import；`grep 'pipeline_runner\|storyboard_director' factory/` 为空；对 core 仅单向 import |
| **P3** | 拆 **studio**：pipeline_runner/storyboard_director/mock/dev_proxy + 画布 routes + `studio/server/state.py`(只 PIPELINE_RUNNER) + `web/` | `studio` 可 import；`grep 'task_runner\|rounds_gate\|wolong' studio/` 为空；pipeline 单测过；`web/dist` 路径解析正确 |
| **P4** | 拆 **bridge 总装**：删旧 app.py/state.py，建两 `create_app` + artifacts 三分 + 两 `state`(env 强制)；**+ 对外形态 D1 落地** | 两 server 各自独立起；studio 进程内无 factory 协程、factory 进程内无 PIPELINE_RUNNER；各 `/artifacts` 只服务自己根；**单 origin 反代冒烟**(选 A)；端到端冒烟:画布建 job 出片 + 卧龙建 round 验收 |
| **P5** | 清依赖 + 删 shim + 入口点收口 + 更新 `.project_map` 生成器(三包目录) + `docs/MIGRATION.md` | 全仓无 factory→studio / studio→factory 生产 import(grep+importlib 静态校验)；core 无反向依赖；三包 pytest 全绿；两 server 入口点冷启动成功 |

---

## 6. 对抗加固表（blocker/major → 折入处）

| 严重度 | 对抗发现 | 折入 |
|---|---|---|
| BLOCKER | core asr/rw 经 .mjs 链 import feishu → 成环 | §4 D2：scripts 整体留 core + 切 feishu 尾 |
| BLOCKER | 单 origin 契约，拆两端口断 iOS | §3 D1：顶层反代合并回单 origin |
| MAJOR | .mjs ESM `'./'` 跨包 import 断 | §4：`.mjs` 整目录不拆、跟 core 走 |
| MAJOR | repo 根 `pipelines/douyin_processing`+`gpt_image/` 不在 src、parents[N] 找 | §5 P1：区分两个 pipelines/；`douyin_processing` 零消费者→评估删除；gpt_image 收 core 修 spawn 路径 |
| MAJOR | §6 script 回写 /jobs 跨根断 | §3：factory script 只读 /artifacts，必要时新增 factory `PUT /artifacts` |
| MAJOR | vite dev proxy 单后端 | §3：proxy 表按产品分流 8810/8811 |
| MAJOR | 两 app parents[N] 猜根 → 产物根错位 | §3：强制 env + fail-fast |
| MINOR | factory `/commands`、`/artifacts` 需全集 | §2：`get_schema` 兜 core PRIMITIVE_SCHEMAS；断言端点返全 |

---

## 7. 仍开放的风险（实施期再定）

- **`douyin_processing` 与 `reading_confidence` 模板零消费者**：本次 grep 未发现使用方，P1/P2
  直接评估**删除**而非搬运——需你确认是否还要保留。
- **单进程合并部署**：若运维要求对外仍单端口又不想加独立反代，可顶层 `Mount` 两 sub-app，但两
  app 的 startup/lifespan、同名 `/artifacts` 共存(路径前缀)未细化，属部署期决策。
- **`cancel.py` 归属**：放 core 是"便于 studio 未来用"的前瞻，当前 consumer 全 factory；坚持极薄
  核心可降到 factory（不影响纯净度，可回退小决策）。

---

## 8. 跨三包不变的契约（拆分中不许动）

- **`on_progress(text:str)->None`**：所有 primitive 与 agent 的
  `run(<args>, on_progress=_noop, ...) -> dict[str,Any]` 统一签名，是 `TaskRunner`/`PipelineRunner`
  反射拉起的隐式接口契约，**跨三包不变**。
- `templates/paper_card_talk_015` 资产目录：`render_015`(core) 用 `shutil.copytree` 复制它，
  studio 的 preview/templates/pipeline_runner/mock 引用同一目录 → **随 render_015 进 core**。

---

## 9. P0 as-built（2026-06-13，已落地）

P0 骨架已完成并验证，**对 §5 计划有两处务实偏离**：

1. **未用 `uv sync`，改 `uv lock` + `uv pip install -e`**：当前 venv 是普通 venv（无 lock、含
   whisper 等重依赖），`uv sync` 会按 lock 修剪、可能误清。`uv lock` 已验证 workspace 能解析出
   3 个成员（core/studio/factory-root），装包沿用项目既有 `uv pip install -e`，零 env churn。
2. **未建 `packages/factory` 空骨架**：包名与现存根包 `ncds-opus-factory` 撞。**factory = 根包
   本身**（未拆单体），P2/P4 才物理迁入 `packages/factory/`。

落地：`packages/{core,studio}` 骨架（importable）、根 `[tool.uv.workspace]`、`uv.lock`（3 成员）、
core/studio editable 装入 venv、`nof-server` 入口完好。测试 **298 passed / 0 回归**（2 个 boya
失败 = 本机 ffmpeg 4.1 太老缺 `amix normalize`，stash 验证与拆分无关）。
**待提交时决策**：`uv.lock` 现被 `.gitignore` 忽略；workspace 一般应提交 lock。

### 9.1 硬编码路径迁移清单（P0 交付物）
见 **[MONOREPO-SPLIT-PATHS.md](MONOREPO-SPLIT-PATHS.md)**：64 个 distinct 脆弱点 / 52 文件，29 个
**无 env 兜底**（断 = 链垮）。统一改法 = `repo_root()` helper + `importlib.resources` 取包内资源
+ 运行时 env-first。P1–P3 迁包逐条照它核对。

### 9.2 清单暴露的两个边界遗漏（补进规划）
- **`skills/`（repo 根）**：✅ **已定（2026-06-13，用户裁定）**——`skills/` 是含 SKILL.md 的
  混合大杂烩（10 子目录），**不是包形状，留 repo 根、谁都不搬**。`skills/video-pipeline` 被
  studio（`pipeline_runner._execute_asr`）与 factory（`video_job_worker.mjs`/沈括）共用。
  P1.7 = 把 `parents[N]/skills/...` 改 `repo_root()/"skills"/...` + `NOF_VIDEO_PIPELINE_SCRIPT`
  env 兜底（主要 `pipeline_runner.py:1207`）。**注**：§9.2 早先"asr 两端都用 → skills 随 core"的
  理由已被 §9.4 推翻（asr 命令归 factory）；现按"repo 级共享资源、env 引用"处置，与归属无关。
  执行细节见 [MONOREPO-SPLIT-HANDOFF.md](MONOREPO-SPLIT-HANDOFF.md) §2 P1.7。
- **`.mjs` 的 `node_modules` 锚点**：✅ **已定（D2 配套，2026-06-13）**——只有 `render_runner.mjs`
  需第三方包（puppeteer-core/puppeteer-screen-recorder）；asr/rw 的 `.mjs` 全是 `node:` 内置、
  不需 node_modules。**core 自带 `packages/core/package.json` + `node_modules`**（根 package.json
  的 puppeteer 部分挪进 core，`npm install` 落 core），render_runner.mjs 从 core/commands 向上找；
  `NOF_RENDER_NODE_PATH` env 保留兜底。

### 9.3 feishu 尾的处置（D2 落地裁定，2026-06-13）
`feishu_sdk_adapter.mjs` **已是 lark-cli 包装层**（FEISHU-REFACTOR 完成，不直调 OpenAPI），被
`rewrite_command_runner.mjs`（rw 链冒泡改写稿到飞书）与 `video_job_worker.mjs`（老视频任务流）import。
✅ **裁定**：P1.6 从 `rewrite_command_runner.mjs` **删掉飞书冒泡**（去 `feishu_sdk_adapter` import +
建文档/发消息调用），只留改写逻辑 + stdout JSON（符合 AGENTS.md"命令不发飞书、只 on_progress"）。
`feishu_sdk_adapter.mjs` / `lark_cli.mjs` / `video_job_worker.mjs` 等飞书 IO / 老 bot 流**不进 core**，
留 factory（或 repo 根 legacy）。

### 9.4 as-built 纠正：asr/rw 命令归 factory，只 rewrite 引擎进 core（2026-06-13，用户确认）
深入 `.mjs` 排查（[pipeline_runner.py:1194](../src/ncds_opus_factory/server/pipeline_runner.py:1194)
注释为铁证）**推翻了规划的"asr/rw = core primitive"**：studio **刻意绕开命令包装**，直接 spawn
底层引擎（asr 用 `skills/video_pipeline.py`、rw 用 `content_rewrite_runner.mjs`）；`asr.py`/`rw.py`
命令只被 cli + 任务系统用 = **factory 专属**。已查 `asr.py → asr_command_runner → video_job_worker`
牵连 feishu + skills + 腾讯 COS，全是 factory 关切。
**✅ 裁定**：
- **core**：rewrite 引擎 `content_rewrite_runner + video_rewrite_runner + rewrite_profiles +
  douyin_cog_kernel`（**无 feishu**，studio rw 节点 spawn + factory rw 命令 import 共用）。
- **factory**：`asr.py/rw.py` 命令 + `asr_command_runner/rewrite_command_runner/video_job_worker/
  feishu_sdk_adapter/lark_cli`（含 feishu/老 bot/COS/skills 编排）。
- **§9.3 的"切 feishu 尾"作废**：`rewrite_command_runner` 留 factory，feishu→factory 天然合法。
- **`PRIMITIVE_REGISTRY` 收为 6 个**：wst/tst/vid/tts/render/render_015（asr/rw 不在内）。
- **余下一步**（engine 进 core 时）：factory 的 `rewrite_command_runner` 要 ESM import core 引擎——
  跨包相对路径 or npm workspace 按名解析，P1.6 收尾时定。studio 侧是 spawn-by-path（不受影响）。
