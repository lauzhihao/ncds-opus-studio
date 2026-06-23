# 代码审查报告（2026-06-19）

> 触发：owner 要求整理项目代码，聚焦**工程规范 / 架构设计 / 编码规范 / 逻辑分叉**四个维度。
> 方法：5 个只读探查 agent 分维度取证 → 主线程复核 → 安全项直接执行 + 验证 → 其余落 backlog/tasks + 决策清单。
> 基线：审查前 `pytest` **587 passed**；执行安全清理后 **586 passed**（删了 1 个孤儿 dir 的 1 个测试，见 §3）。
> 红线：**不破坏现有 UI（除非是优化）、现有功能不能出错**。owner 外出无法决策，凡有"改动活路径"风险的一律不自动执行，记入 §5 决策清单。

---

## 1. 一句话结论

**真正的债不是分散的，而是高度集中**：一个 3172 行的 god-object（`server/pipeline_runner.py`）+ 三套并存执行运行时（legacy `pipeline_runner` / `task_runner` / 新 `engine/`），导致同一份业务逻辑（采集/选题/改写/质检）被写了 2~4 遍并已开始漂移。工程红线（secrets / core 反向依赖 / shell / 测试契约）**基本全合规**；编码纪律（裸 except、`os.path` 滥用、web `any`）**比预期好**。所以"整理"的收益 80% 来自**收敛运行时 + 拆 god-object + 统一单一真源**，其余是零散卫生。

---

## 2. 现状架构真相（澄清常见误解）

- 设计意图（`docs/PRODUCTION-ENGINE-DESIGN.md`）：`engine/` 是绞杀者（strangler），最终替代 `PipelineRunner` + `TaskRunner` 双轨。
- **现实**：engine **没有替代** PipelineRunner，而是被**塞进 PipelineRunner 当 6/7 步的执行内核**。本分支已把绞杀者**默认全开**（`pipeline_runner.py:149-156`），但：
  - **web** 走 `/jobs/*`（`routes/pipelines.py`）→ PipelineRunner facade → 命中节点转 engine `run_step`，**asr 例外永走 legacy**（`pipeline_runner.py:1306` 硬编码 `node_name != "asr"`）。
  - **app** 走 `/tasks`（`task_runner.py`）→ 完全没动迁移，仍是老轨。
  - **`/instances`**（engine 的 HTTP 入口，`routes/instances.py`）**前端零调用**，只有测试覆盖。
- 净结果：**6 步走 engine + asr 走 legacy；6 个 legacy `_execute_*` 作为 `NOF_ENGINE_NODES=legacy` 回退路径冷藏；engine 的 HTTP 层悬空；TaskRunner 老轨还活着**。这就是"逻辑分叉"的根。

---

## 3. 已直接执行（安全·已验证·未 commit，留工作树待 owner 复核）

| # | 改动 | 文件 | 为什么安全 |
|---|---|---|---|
| E1 | 删除孤儿目录 `pipelines/douyin_processing/`（4 文件） | repo 根 `pipelines/` | 全仓零代码 import（仅 docs/archive 提及且**自身建议删除**）；`map_project.py:221` / watchdog 对缺失 dir 有 `is_dir()` 守卫，不崩。删掉连带其 1 个网络型手跑测试 `test_download`（587→586） |
| E2 | 启动日志非 ASCII 箭头 `→`→`->` | `server/app.py:114` | 纯 log 文案单字符，修 CLAUDE.md「控制台日志只用 ASCII」红线，零行为影响 |
| E3 | rw 口味注入吞异常补一行 `logger.warning` | `server/pipeline_runner.py:3033` | 原 `except Exception: pass` 静默吞错；只在 except 分支加日志，控制流不变（logger 已存在于 line 43） |

> 这三项是**唯一"不可能搞坏"的改动**。所有触及活执行路径 / god-object 重构 / 删活代码的项，因 owner 外出 + "现有功能不能出错"红线，**一律未执行**，见 §4/§5。

---

## 4. Findings 全表（按维度 + 严重度）

### 4.1 架构设计 / 逻辑分叉（最重）

| ID | 严重度 | 标题 | 关键位置 | 归属 task |
|---|---|---|---|---|
| A1 | P1 | 三套运行时并存，engine 被塞进 PipelineRunner 当内核，TaskRunner 老轨未迁 | `pipeline_runner.py` / `task_runner.py` / `engine/` | task-3.1 |
| A2 | P1 | 同一份 015 逻辑在 legacy `_execute_*` 与 engine `run_*_step` 各一份，已漂移（lines/storyboard 加了 JSON 重试+domain_image_style，legacy 没有） | `pipeline_runner.py:1457-2440` ↔ `pipeline_performers_015.py` | task-3.1 |
| A3 | P1 | "改写"能力多套实现、输入语义/质检口径各异 | `commands/liuyong.py` / `_execute_rw` / `run_rw_step` | task-3.4 |
| A4 | P1 | "采集"能力 3 套：collect_one 快采 vs video_pipeline 全量，靠 `!= "asr"` 一行 guard 掩盖；engine `run_asr_step` 是死代码 | `_execute_asr_collect` / `run_asr_step` / `:1306` | task-3.4 |
| A5 | P1 | rw 默认走 engine → 丢逐模型实时增量进度面板（产物不变，仅 UX 退化，设计已知接受） | `pipeline_performers_015.py:316,397` | task-3.3（决策） |
| A6 | P2 | `/instances` engine HTTP 层 + app `TaskRunner` 都未真正进迁移；`/instances` 前端零调用 | `routes/instances.py` | task-3.1 |
| A7 | P2 | `/jobs/*` 的 26 个业务端点散在名为 `pipelines.py` 的文件，`jobs.py` 只剩 2 个文件 CRUD，命名误导 | `routes/pipelines.py` / `routes/jobs.py` | task-3.2 |

### 4.2 编码规范 / 可维护性

| ID | 严重度 | 标题 | 关键位置 | 归属 task |
|---|---|---|---|---|
| C1 | P1 | `pipeline_runner.py` 3172 行 god-object（1 类 60+ 方法，混状态机/SSE/cancel/6 步执行/3 类 agent 后台） | 整文件 | task-3.2 |
| C2 | P1 | opus CLI args 在 `_polish_transcript_with_opus`/`_call_opus_for_rw`/`quality_rubric._call_opus_judge` 各手拼一份，没走 `common/opus_cli.call_opus` | `pipeline_runner.py:2553,2746` `quality_rubric.py:147` | task-3.5 |
| C3 | P1 | `_purge_ai_taste` 消 AI 味逻辑两份副本且 prompt 已漂移 | `commands/liuyong.py:44` ↔ `pipeline_runner.py:2936` | task-3.5 |
| C4 | P1 | 硬编码模型名 `claude-opus-4-8`（4 处）+ 超时值（3600/900/600/60 手写）无常量收口 | `pipeline_runner.py:2237/2338/2605/2950 …` | task-3.8 |
| C5 | P1 | `collect_one()` 235 行 + 5 个 90~170 行超长函数 | `commands/shenkuo.py:98` 等 | task-3.2 |
| C6 | P2 | `_assert_known_model` / mock 短路逻辑在 rewrite_rw_model/refine_rw_model 复制 5 次 | `pipeline_runner.py:741,833` | task-3.2 |
| C7 | P2 | `/tmp/gpt-image` 输出根在 6+ 处硬编码，各自定义 DEFAULT_OUTPUT_ROOT | `gpt_image/*.py` 等 | task-3.8 |
| C8 | P2 | `rubric_store.injection_brief()` 宽吞 `except: pass`（**已修 E3**）；`dev_proxy.py` 3 处吞异常 | `pipeline_runner.py:3033`（已修）/ `dev_proxy.py:116…` | task-3.8 |
| C9 | P2 | `app/lib/core/net/models.dart` 978 行 + 77 处 `dynamic`；web `HomePage.tsx` 896 行 | app/web 表现层 | task-3.2（低优） |

### 4.3 工程规范红线

| ID | 严重度 | 标题 | 关键位置 | 归属 task |
|---|---|---|---|---|
| S1 | P1 | 生产媒体管线曾大量使用非 ASCII 日志前缀，违反「日志只用 ASCII」 | `skills/video-pipeline/scripts/*` | task-3.6 |
| S2 | P1 | 启动日志非 ASCII `→`（**已修 E2**） | `app.py:114` | 已修 |
| S3 | P2 | `nof-worker` 裸 `await asyncio.Event().wait()`，无 SIGTERM/graceful shutdown | `server/worker.py:76` | task-3.9 |
| S4 | P2 | `Dockerfile:33` 用全局 `pip install`（docker 已弃用，留作回滚） | `Dockerfile:33` | task-3.8（低优） |
| S5 | P2 | `edit-server.py` kebab-case 违反 Python snake_case | `templates/…/.015-draft-assets/edit-server.py` | task-3.8（低优） |
| — | ✅ | **合规**：secrets 无硬编码 / core 不反向依赖 factory / shell `set -euo pipefail` / 测试契约 / venv py3.12 未污染 | — | — |

### 4.4 死代码 / 旧实现（可清理）

| ID | 严重度 | 标题 | 处置 |
|---|---|---|---|
| D1 | P0 | `pipelines/douyin_processing/` 孤儿原型，零消费者 | **已删 E1** |
| D2 | P1 | `commands/wudaozi.py` / `boya.py` + figure_talk/stickman 模板 + scan 脚本 + assets = 旧 figure_talk 冷链；app catalog 经 `/tasks` 仍可派单 | task-3.7（决策，reachable，删触及 app UI） |
| D3 | P2 | factory primitive shim `commands/{wst,tst,vid,render}.py`（自标「P5 清理时删除」），生产侧零 import | task-3.8（低风险但低价值，留 backlog） |
| D4 | P2 | `scripts/{wudaozi,boya,shenkuo}_sop.md` 零代码引用（`wolong_sop.md` 被 code 加载，保留） | task-3.7 捆绑 |
| D5 | P2 | core `render.py`（非 015 录屏器）+ factory `templates/paper_card_talk`（非 015）随 figure_talk 冷链冷藏 | task-3.7 捆绑 |

---

## 5. 需 owner 决策清单（已给"最佳实践默认建议"，回来一句 Go 即可执行）

| # | 决策点 | 我的建议（默认） | 为什么没替你拍板 |
|---|---|---|---|
| **DEC-1** | rw 是否恢复逐模型实时进度面板？(A5) | **回退 rw 走 legacy**（`:1306` 改 `node_name not in ("asr","rw")`）—— 1 行、可逆、纯 UX 优化（你允许的"优化"），legacy/engine 共享全部 rw 内部逻辑无维护分叉 | 这改的是**当前在生产跑通的 rw 活路径**（engine→legacy），属"动活路径"，外出期不冒此风险。详见 task-3.3 |
| **DEC-2** | 是否删 6 个 legacy `_execute_*` 冷藏路径？(A2) | **暂不删**，留作 `NOF_ENGINE_NODES=legacy` 回退护城河，直到 engine 路径再跑通几个生产周期 | >30 行删除不可逆 + 放弃回退能力，需你确认运维不再依赖 |
| **DEC-3** | 是否删吴道子/伯牙旧 figure_talk 冷链？(D2) | **暂不物理删**，但标记为"冷链/重写时直接替换不必兼容"（与你"不为旧实现搞兼容屎山"一致）；真要删须**连 app catalog tile 一起删**（否则破坏 app UI） | reachable from app `/tasks` + catalog，删会改 app UI（违背"不破坏 UI"），且属产品节奏决策 |
| **DEC-4** | 是否继续收敛 `commands/liuyong.py` 与 web rw performer？(A3) | **先核实 app `/tasks` 与 CLI 是否仍在用**；若否则从 command_schemas/label_store/mock_agents 摘除 | 仍被 registry/CLI 引用，需确认使用面 |
| **DEC-5** | god-object 拆分分期方案 | **三步**：先抽 3 类 agent 后台（enrich/refresh/guiguzi）→ 再把 engine 桥接收进 adapter → 最后随漂移收敛删 legacy 执行体 | L 级跨文件重构，blast radius 大，需对齐边界与分期才动手（task-3.2） |

---

## 6. 应抽成单一真源（single source of truth）的横切能力

1. **opus 启动器调用** → 统一到 `common/opus_cli.call_opus`（需先给它补 system_prompt 通道）。
2. **消 AI 味 `purge_ai_taste`** → 抽到 `common/`，消灭 liuyong/pipeline_runner 两份漂移副本。
3. **rw 质检闸门** → `_apply_rw_qc` 已是 legacy+engine 共用单点，但 `commands/liuyong.py:144-174` 仍内联一份，待并。
4. **rw 源文本口径** → 固定"collect_one 的 `entry.text`"为唯一 rw 输入。
5. **domain 真源** → 以 `works_repo.manifest.domain` 为准（采集时写），rw/guiguzi 从 manifest 读而非 job inputs。
6. **采集能力** → `collect_one` 为唯一实现，删/并 engine `run_asr_step`。
7. **改写能力总入口** → 明确 legacy/engine rw step 为生产唯一真源，废弃两个 commands 化石。

---

## 7. 落地编号映射

详见 `backlog/tasks/task-3*`：task-3.1 运行时收敛 · 3.2 god-object 拆分 · 3.3 rw 增量进度(决策) · 3.4 改写/采集单一真源(决策) · 3.5 横切统一 · 3.6 去 emoji · 3.7 旧冷链清理(决策) · 3.8 编码收口 · 3.9 worker graceful shutdown。
