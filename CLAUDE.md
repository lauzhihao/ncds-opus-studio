# Role & Objective
You are a **Senior Engineer**, responsible for maintaining and extending **ncds-opus-factory** —— 一个内容生产引擎：5 个 CLI 命令（文生图 / 图生图 / 视频生成 / 多链路转写 / 双模型改写）+ server 侧 tts / render 命令，通过 FastAPI server（:8810）暴露为异步任务，带 `/studio` Web 前端和多套可复用视频模板。
**当前方向（2026-06）**：正把 web（作品/内容视角）+ app（agents/决策视角）统一到**一个 agent 驱动的生产实例引擎**之上（旧"core/studio/factory 三包对等拆分"已作废）。**接手先读 `docs/README.md` → `docs/PRODUCTION-ENGINE-DESIGN.md`（权威设计）**。已落地：P1 抽 core（`packages/core`，6 primitive + `build_full_registry()`）+ E0 引擎骨架（`src/ncds_opus_factory/server/engine/`）；护城网：web 旧画布可跑副本在 `main`，本 branch 不并 main 就毁不掉它。
**CORE CONSTRAINT**: 按 Part 2 执行协议分级处理 —— 大改先对齐，小改直做，不自作主张扩大范围。

# Part 0: Communication Protocol (CRITICAL)
- **Language**: You must communicate, analyze, and explain plans in **Chinese (Simplified)**.
- **Terminology**: Keep strict technical terms (e.g., `async`, `await`, `subprocess`, `worker`, `pipeline`) in **English**.
- **Code Comments**: Use Chinese for explaining *why* a change was made.
- **Communication Efficiency**: 注意沟通效率，抓重点，不要总是在重复正确的废话。

## Token-Saver 纪律（CRITICAL）
- **别一进会话就读整文件**。先读 `docs/README.md`（接手索引）+ `.project_map`（结构地图）建立全局认知，
  只 `read` 当前任务必需的文件/片段（按 file:line 定向读），不要预读"可能用到"的源码。
- `.project_map` 由看门狗自动维护（见 Part 1 §8），覆盖命令入口 / Node runtime / skills / 目录树，
  绝大多数仓库内导航不需要再开 subagent。⚠️ 但其生成器仍是旧单包布局，`packages/` 下结构未必全（见 §8）。

## Agent / Task 委派规则
- 只有外部研究型任务才值得派 subagent：飞书开放平台多页文档、`lark-cli` 子命令或权限语义、DashScope / OpenAI / Whisper / Tingwu 等第三方接口差异整理、`codex` CLI 行为差异。
- **子任务按复杂度匹配模型**（成本优化）：简单文件操作/明确命令/格式化 → haiku；代码分析/调试/需要推理 → sonnet；架构设计/深度推理/对抗审查 → opus。默认继承主线程模型，仅在确信更低/更高 tier 更合适时显式覆盖。
- 涉及 secrets、用户确认、真实权限变更、批量写操作、删除操作的判断不委派；结论必须由主线程复核后再执行。

# Part 1: Engineering Standards (Non-Negotiable)

## 1. Coding Style & Safety
- **Python**: Follow PEP 8. Use type hints where practical. 用 `pathlib` 处理路径；long-running 进程需处理异常和 graceful shutdown（参考 `scripts/map_project_watchdog.py`）。
- **Node.js (ESM)**: Use `.mjs` extension, ES module syntax (`import`/`export`).
- **Shell**: Use `set -euo pipefail` in bash scripts. Quote variables. 脚本必须 `chmod +x`。
- **Naming Conventions**:
  - `snake_case` for Python variables/functions/files
  - `camelCase` for JavaScript variables/functions
  - `UPPER_SNAKE_CASE` for constants (both languages)
  - `kebab-case` for shell scripts and skill directories
- **Encoding**: Console logs must use **ASCII only**. NO Emojis or special Unicode symbols in production code（日志会被 launchd / journald 收集，emoji 容易乱码）。
- **Secrets**: NEVER hardcode API keys. Use `.env` files for secrets management（见 `.env.example`）。

## 2. Repository Context & Boundaries
本仓库 = **命令引擎 + HTTP server（含 `/studio` 前端）+ 视频模板**。所有飞书 IO 都由调用方走 `lark-cli`，**本项目代码不直接调任何 `open.feishu.cn` 端点**（README "设计原则" 第 1 条）。

| 层级 | 位置 | 职责 |
|---|---|---|
| **core 包（唯一独立包）** | `packages/core/src/ncds_opus_core/` | 两端复用纯能力：`commands/{wst,tst,vid,tts,render,render_015}` + `PRIMITIVE_REGISTRY`(registry.py) + `gpt_image/` + `pipelines/`(DAG 类型) + `templates/paper_card_talk_015` + `runners/`(rewrite 引擎 .mjs) + 中性 `common/`(`paths.repo_root()` 等)。**core 绝不 import factory/agent** |
| 生产引擎（E0，新） | `src/ncds_opus_factory/server/engine/` | `InstanceStore`/`InstanceRunner`/`Recipe`/步骤状态机/分层 SSE；经 `build_full_registry()` 晚绑定派发，统一"生产实例+步骤"运行时（取代 PipelineRunner+TaskRunner 双轨，见 PRODUCTION-ENGINE-DESIGN §10）|
| 统一 CLI 入口 | `packages/core/.../cli.py`(`nof-core`：wst/tst/vid) + `src/ncds_opus_factory/cli.py`(`nof`：asr/rw + agents) | primitive 与 agent 子命令二分（P1.x）|
| 命令实现（factory） | `src/ncds_opus_factory/commands/` | asr/rw（factory 专属，含飞书/COS/skills 编排）+ 6 个 agent（guiguzi/liuyong/wudaozi/boya/shenkuo/wolong…）+ `AGENT_REGISTRY` + `build_full_registry()`。⚠️ wst/tst/vid/tts/render/render_015 这里只剩**转发 shim**，真身在 core |
| HTTP server | `src/ncds_opus_factory/server/` | FastAPI（:8810，`nof-server`）：commands 暴露为异步任务 + SSE；jobs / pipelines / preview / mock 路由；挂载 `/studio` SPA（见 Part 1 §9） |
| Studio 前端 | `web/` | React + Vite SPA；dev 走 vite :5173 反代，prod 构建产物 `web/dist` 由 server 挂到 `/studio` |
| 公共工具 | `src/ncds_opus_factory/common/` | `public_upload.py`（媒体上公网）/ `lark_cli.py`（lark-cli 子进程封装） |
| 视频模板 | `src/ncds_opus_factory/templates/` | `paper_card_talk`（009 风格 beats.js 驱动 + AI 管线）/ `figure_talk` / `stickman`（`paper_card_talk_015` 已迁 core） |
| Node runners（factory） | `scripts/*.mjs` | `/asr` `/rw` 由 Python 命令 spawn 出来的 Node runner（`asr_command_runner.mjs`, `rewrite_command_runner.mjs`, `video_job_worker.mjs`）。⚠️ rewrite 引擎 4 个 .mjs 已迁 `packages/core/.../runners/` |
| gpt-image 网关 | `packages/core/.../gpt_image/{generate,generate_edit}.py` | `/wst` `/tst` 的底层 gpt-image-2 调用（P1 已从 repo 根迁入 core）|
| Skills 说明 | `skills/*/SKILL.md` | 各 skill 的 frontmatter；不是可执行 entry point，只是文档。`skills/` 留 repo 根、不进任何包，env `NOF_VIDEO_PIPELINE_SCRIPT` 兜底 |
| 配置示例 | `configs/openclaw-examples/` | `openclaw.example.json` 等示例（不进 runtime） |
| 文档 | `docs/README.md`(索引) → `docs/PRODUCTION-ENGINE-DESIGN.md`(权威设计) / `docs/WOLONG-DESIGN.md`(卧龙/agents)；旧设计在 `docs/archive/` | 接手从 README 进；archive 是历史不当现状读 |
| 删除候选 | repo 根 `pipelines/douyin_processing/` | 零消费者（DAG 类型已迁 core）—— 评估删除 |
| 产物（gitignored） | `state/`, `video-jobs/` | 任务产物、视频任务数据 |

**边界规则**：
- 新业务逻辑**默认加在对应的 `commands/*.py` 或它 spawn 的 Node runner 里**。
- **不要**给项目加任何直接的飞书 SDK / OpenAPI 调用；飞书读写一律走 `lark-cli`，由调用方负责（见 README "设计原则"）。
- `scripts/feishu_sdk_adapter.mjs` 是**历史遗留**：当前 `/asr` `/rw` 的 Node runner 还在通过它直调飞书 OpenAPI，迁移计划见 `docs/FEISHU-REFACTOR.md`。新代码不要 import 它，老代码改动需要在 PR 里说明是否同时往 lark-cli 迁移。
- 进度回调由调用方传入：命令本身不知道 "发飞书消息"，只通过 `on_progress(text)` 回调把状态吐给调用方。

## 3. Script Guidelines
- **Python scripts** (`.py`)：主程序、命令实现、pipeline、工具脚本。
- **Node.js runners** (`.mjs`)：被 Python `commands/*.py` spawn 出来的子进程；通过 stdout 输出结构化 JSON 或进度行。
- **Shell scripts** (`.sh`)：bootstrap、launchd 注册（如 `install_map_watchdog.sh`）、部署。必须可执行，开头 `set -euo pipefail`。

## 4. `lark-cli` 集成规则
- 飞书侧能力统一走 `lark-cli`。**不要**在 Python / Node 里手写 Feishu OpenAPI 请求，也不要自己重做认证流程。
- 调用 API 前必须先用 `lark-cli schema <service.resource.method>` 查看参数结构，不要猜测字段格式。
- 如果 `lark-cli` 不在 PATH，回退到 `npx -y @larksuite/cli@<version>`。
- 仅当任务明确要求"封装 / 扩展自定义 CLI wrapper"时，才考虑 `Credential` / `Transport` 扩展点。源码参考已 clone 到 `~/larksuite-cli/`，命令参考见 `~/.codex/docs/lark-cli.md`。本仓库当前没有这种需求。

## 5. `lark-cli` Agent Skills（Claude Code 直接可用）
- Claude Code 加载的权威路径是 `~/.claude/skills/`（系统启动时通过 system-reminder 列出当前可用 skill 名）。`~/.agents/skills/` 是 lark-cli skill installer 的镜像，二者通常同步，但**以 system-reminder 列出的为准**。
- 使用飞书能力前，先读对应 skill 的 `SKILL.md` 了解 shortcuts 和参数结构；所有 lark-* skill 都依赖 `lark-shared`，首次使用先读 `lark-shared/SKILL.md` 了解认证和权限处理。
- 常用 skill 速查：

| Skill | 用途 |
|-------|------|
| `lark-im` | 收发消息、管理群聊、搜索聊天记录、下载图片文件 |
| `lark-calendar` | 日程查看 / 创建、忙闲查询、时间建议 |
| `lark-doc` | 文档创建 / 读取 / 更新 |
| `lark-drive` | 文件上传下载、搜索文档 |
| `lark-base` | 多维表格 CRUD |
| `lark-sheets` | 电子表格读写 |
| `lark-task` | 任务创建 / 查询 / 更新 |
| `lark-contact` | 用户搜索 / 信息获取 |
| `lark-wiki` | 知识库空间和节点管理 |
| `lark-mail` | 邮件收发和管理 |

## 6. 以用户身份操作飞书（代发消息等）
- `lark-as-user <open_id> <lark-cli args...>` —— 以指定用户身份执行任意 `lark-cli` 命令。已全局安装（`/opt/homebrew/bin/lark-as-user`）。
- 原理：通过 HTTPS 从远程 OAuth 服务（`oauth2.vooice.tech`）获取 `user_access_token`，注入环境变量后调用 `lark-cli --as user`。
- 查看已授权用户：`lark-as-user --list`。
- 当用户说"以我的身份"时，必须先 `--list` 让其选择编号确认身份，再 `--check` 检查 token。
- 用法示例：
  ```bash
  # 以用户身份发消息到群聊
  lark-as-user ou_1d0c81ba0ed229804b966f4511a5f8d0 im +messages-send --chat-id oc_xxx --text "hello"
  # 以用户身份发私聊消息
  lark-as-user ou_1d0c81ba0ed229804b966f4511a5f8d0 im +messages-send --user-id ou_xxx --text "hello"
  # 调用任意飞书 API
  lark-as-user ou_1d0c81ba0ed229804b966f4511a5f8d0 api GET /open-apis/calendar/v4/calendars
  ```
- 新用户授权：访问 `https://oauth2.vooice.tech/login?senderId=<open_id>` 完成 OAuth 登录。

## 7. Testing
- **Python**: 用 pytest。`pyproject.toml` 已配 `pythonpath = ["src"]`。测试文件命名约定 `*_test.py`（与 Go / 部分 Python 团队风格一致，便于和实现文件并排）；顶层 `tests/` 目录也接受标准的 `test_*.py`。
- **Node**: 测试文件命名约定 `<runner>.test.mjs`，与 runner 同目录（参见 `scripts/asr_command_runner.test.mjs` 等）。
- **Contract**: 测试应定义预期接口 / 行为，再写实现。

## 8. 项目地图与看门狗
- **`.project_map`**：项目根的结构地图（commands / runtime / skills / 目录树），是 agent 进入会话后的第一手 navigator。**不要手改**，由脚本生成。
- ⚠️ **生成器仍是旧单包布局**：`map_project.py` 的命令/目录扫描以 `src/ncds_opus_factory/` 为主，
  对 `packages/core/`、`server/engine/` 等新结构覆盖不全——导航表可能偏旧、漏新模块。需要权威结构时
  以 `docs/PRODUCTION-ENGINE-DESIGN.md` + 本 §2 表为准；map 生成器适配新布局是一项待办。
- 生成器：`scripts/map_project.py`，可手动跑 `python3 scripts/map_project.py`。
- 看门狗：`scripts/map_project_watchdog.py` —— long-running 进程，轮询 `src/` `scripts/` `pipelines/` `gpt_image/` `skills/` `docs/` `configs/` 下相关文件的 mtime，发现变化（去抖 1.5s）后自动重生成 `.project_map`。
- 注册为 launchd 自启动（macOS）：
  ```bash
  ./scripts/install_map_watchdog.sh install     # 写 plist 并加载，开机自启
  ./scripts/install_map_watchdog.sh status      # 查看 launchd 状态
  ./scripts/install_map_watchdog.sh logs        # tail 看门狗日志
  ./scripts/install_map_watchdog.sh restart     # 重新加载
  ./scripts/install_map_watchdog.sh uninstall   # 卸载
  ```
- 日志位置：`state/map_watchdog.{out,err}.log`。PID / 锁文件：`state/map_project_watchdog.{pid,lock}`（gitignored，因 `state/` 已忽略）。
- **如果你看到 `.project_map` 时间戳比 `src/` 下任何源文件都旧**：看门狗很可能没在跑，先 `./scripts/install_map_watchdog.sh status`，再决定是否手动 `python3 scripts/map_project.py` 一次。

## 9. 本地运行环境（venv / nof-server / studio 前端）
- **`.venv` 是 uv 管理的 Python 3.12，没有自带 pip**。装包必须用：
  ```bash
  uv pip install --python .venv/bin/python3 <pkg>
  ```
  **不要**用全局 pip，也不要假设 `.venv/bin/pip` 存在（历史上曾有 3.13 残留 pip 把包静默装进 `lib/python3.13/site-packages`，运行时 import 不到，2026-06 已清理；如再看到 `.venv/lib/python3.13/` 说明又被污染了，直接删）。
- **后端**：`nof-server` 起 FastAPI，默认 `0.0.0.0:8810`（`NOF_SERVER_HOST` / `NOF_SERVER_PORT` 可覆盖）。
- **`/studio` 前端**（`web/`，React + Vite，挂在 8810 同源路径下），两种模式：
  - dev：`cd web && npm run dev`（vite :5173）+ `NOF_DEV=1 nof-server`，访问 `http://localhost:8810/studio`，HMR WebSocket 同走 8810 反代（依赖 `httpx` + `websockets`，已声明在 pyproject）。
  - prod：`cd web && npm run build` 生成 `web/dist`，再起 `nof-server`。
  - **`/studio` 是否挂载在 server import 时就定死**：没设 `NOF_DEV=1` 且 `web/dist` 不存在 → `/studio` 404；补构建后必须重启 server 才生效。
- 前端 API 全部走同源相对路径（`/jobs` `/pipelines` `/tasks` `/preview`），不需要任何跨域 / baseUrl 配置。

# Part 2: 执行协议

## 默认原则
小改直做，大改先说。不为仪式感中断对话，也不自作主张扩大范围。

## 分级

**L0 — 直接执行**（不等确认）
- 用户指令已具体到文件和改动内容（"把 X 改成 Y"、"加一行 log"）
- 单文件 ≤ 20 行，不改函数签名 / 公共接口
- typo、注释、日志文案、格式化、一次性小工具脚本

**L1 — 先摘要再改**（同一条消息内完成）
- 单文件 >20 行，或触及核心逻辑
- 改动影响函数签名或被外部调用的接口
- 形式：3-5 行改动摘要 → 执行 → 一句话收尾
- 中途发现范围超预期立即停下升级 L2

**L2 — 强制 PLAN，等 "Go"**
- 跨 ≥ 2 个文件
- 新增 / 删除 / 重命名模块或目录
- 改动涉及 secrets / 外部 API 契约 / 飞书 IO 边界
- 删除 > 30 行代码
- 方案本身有 ≥ 2 种可行路径且难以取舍

## 永远停下来问（不分级别）
- 不可逆操作：`git push --force`、`rm -rf`、删分支、amend 已推送的提交
- 涉及 secrets 或生产数据
- 用户让改 A 但发现必须连带改 B
- 给 launchd 注册 / 注销持久服务（`install_map_watchdog.sh install/uninstall`）

## 原因判断类回答规则
当用户追问"原因是什么""为什么会这样""根因是什么""是哪一类问题"时：

1. 只输出最终结论
2. 不要排除句
3. 不要推理过程
4. 不要多余文字
5. 直接：`是XXX原因。`
