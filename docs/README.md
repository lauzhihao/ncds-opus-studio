# docs 索引（接手先读这里）

**ncds-opus-factory** = 内容生产引擎：底层能力（asr/rw/wst/tts/render…）+ FastAPI server（:8810，含 `/studio` 前端）。
项目说明/执行规约见仓库根 [CLAUDE.md](../CLAUDE.md) 与 [AGENTS.md](../AGENTS.md)；本目录只放设计/契约文档。

> **运行时（S3 后）**：redis(队列 + 执行状态协调) + `nof-server`(:8810，入队+SSE) + `nof-worker`(执行)。重启 server 不打断在跑任务；Redis 不重启时任务调度状态连续。设计见 backlog/docs/S3-redis-worker-design.md；红线见 CLAUDE.md §6；命令见下「本地运行」。

## 本地运行（runbook）

> CLAUDE.md 只留红线，具体命令在这里。

### Python venv
`.venv` 是 uv 管理的 Python 3.12，**没有自带 pip**。装包：
```bash
uv pip install --python .venv/bin/python3 <pkg>
```
不要用全局 pip，也不要假设 `.venv/bin/pip` 存在（历史上 3.13 残留 pip 会把包静默装进 `lib/python3.13/site-packages`，运行时 import 不到；再看到 `.venv/lib/python3.13/` 说明被污染，直接删）。

### 三进程（按序起）
离线任务执行已从 nof-server 拆到独立 `nof-worker`，跨进程队列和执行协调走 Redis：
1. `redis-server`（任务中间件，先起；`brew services start redis` 或临时 `redis-server`）
2. `nof-server`（:8810，HTTP/SSE/入队，纯 producer 不执行任务；`NOF_SERVER_HOST`/`NOF_SERVER_PORT` 可覆盖）
3. `nof-worker`（`.venv/bin/python3 -m ncds_opus_factory.server.worker`，唯一消费+执行+写状态）

Redis 连不上 → nof-server `POST /tasks` 返 503、nof-worker fail-fast 退出。同 cmd 仍只起一个 nof-worker（任务 claim 已在 Redis，但 wolong/round 文件与外部账号池仍是单 worker 业务假设）。重启 nof-server 不打断 worker 在跑任务。launchd 常驻见 `bin/install_nof_worker.sh`。

**后端热重载（dev）**：worker 拆分后 8810 是纯 producer，任务都在 worker 跑，给 nof-server 加 `--reload` 已**不再**误杀长任务（旧红线作废）。dev 起法：
```bash
.venv/bin/uvicorn ncds_opus_factory.server.app:app --host 0.0.0.0 --port 8810 --reload --reload-dir src
```
`--reload-dir src` 只盯后端源码；存 `.py` 自动重启（会断掉当时挂着的 SSE/in-flight 请求，客户端自动重连）。**只热重载后端**——前端改动仍要 `npm run build`（见下）。生产别长开 `--reload`。

**一键起 HMR 三件套（前后端都免 build 免刷）**：`bin/dev_up.sh {up|down|restart|status}`（最省事：`bin/reload-server.sh` = 先 `kill -9` 清 8810/5173 再 `dev_up up`，或 claude 里 `!bin/reload-server.sh` / `/reload-server`）—— 确保 redis → 起 vite(:5173) → 起 `NOF_DEV=1` + `--reload` 的 nof-server，访问入口 `http://localhost:8810/studio/`（带尾斜杠走 dev 反代，HMR WS 同走 8810）。**不碰 nof-worker**（只查状态提示；worker 起停走 `bin/install_nof_worker.sh` 或 `bin/reload-worker.sh`）。注意：`up`/`restart` 要**在交互终端里跑**——常驻进程靠 `nohup+disown` detach，别在会被回收的子 shell（如 agent 后台任务）里跑，否则进程组被杀牵连。dev 专用，正式部署仍要 `npm run build`。

### /studio 前端（web/，React + Vite）
- dev：`cd web && npm run dev`（vite :5173）+ `NOF_DEV=1 nof-server`，访问 `http://localhost:8810/studio`，HMR 同走 8810 反代（依赖 httpx + websockets，已声明 pyproject）。
- prod：`cd web && npm run build` 生成 `web/dist`，再起 `nof-server`。
- 挂载在 server import 时定死：没设 `NOF_DEV=1` 且 `web/dist` 不存在 → `/studio` 404，补构建后必须重启 server。
- 前端 API 走同源相对路径（`/jobs` `/pipelines` `/tasks` `/preview`），无需跨域/baseUrl 配置。

### 项目地图看门狗
`.project_map` 由 `scripts/map_project_watchdog.py` 自动重生成（mtime 轮询 + 30s 去抖）。手动重生成：`python3 scripts/map_project.py`。launchd 自启：
```bash
./bin/install_map_watchdog.sh install|status|logs|restart|uninstall
```
日志 `state/map_watchdog.{out,err}.log`、PID/锁 `state/map_project_watchdog.{pid,lock}`（均 gitignored）。若 `.project_map` 比 `src/` 源文件旧 → 看门狗没在跑。

## 当前方向与进度（2026-06-22）
**权威设计 = [PRODUCTION-ENGINE-DESIGN.md](PRODUCTION-ENGINE-DESIGN.md)**：把 web（作品/内容视角）与 app
（agents/决策视角）统一到**一个 agent 驱动的生产实例引擎**之上（取代早先的"core/studio/factory 三包对等拆分"）。

当前实现事实：
- **P1 抽 core 已完成**（`packages/core`，6 primitive + `build_full_registry()`）。
- **生产引擎后端已存在**：`src/ncds_opus_factory/server/engine/`、`routes/instances.py`、`pipeline_performers_final.py` 已落地；`RECIPE_REGISTRY` 当前只注册 `final_preview`。
- **web 当前主路径仍是 `/jobs/*`**：`/studio` React 画布调用 `/jobs`、`/pipelines`、`/preview`；`PipelineRunner` 作为 facade 保存 `JobState`，命中节点再转到 engine performer。默认未设 `NOF_ENGINE_NODES` 时，`lines/storyboard/tts/image/render` 走 engine；`asr` 因步内增量与后台 enrich 仍固定走 legacy，`rw` 因逐模型实时 `model_progress/drafts` 面板仍固定走 legacy。
- **app 当前主路径仍是 `/tasks`**：Flutter 决策视角通过 `TaskRunner` / `nof-worker` 消费任务，还没有切到 `/instances`。
- **`/instances` 是可用的后端 driver API**，目前主要由测试与内部迁移使用，尚未替代 web/app 前端主路径。
- 测试基线不要沿用历史文档里的 passed 数字；执行任务当天以 `pytest --collect-only` / `pytest` 的真实结果为准。

下一步按 backlog task-3 系列继续推进运行时收口、god-object 拆分与单一真源。
⚠️ 护城河：web 旧画布可跑副本在 `main`，本 branch 不并 main 就毁不掉它。

## 活文档（current）
| 文档 | 作用 |
|---|---|
| **[PRODUCTION-ENGINE-DESIGN.md](PRODUCTION-ENGINE-DESIGN.md)** | **权威设计**：目标架构、核心抽象、步骤生命周期/介入点、多配方、E0–E5 迁移分期 |
| [WOLONG-DESIGN.md](WOLONG-DESIGN.md) | 卧龙子系统实装（调度/闸门/离线学习）——在新架构里=app driver + 自治神经层的规格 |
| [FRONTEND-API.md](FRONTEND-API.md) | 对外 HTTP API 契约：当前 `/jobs`=web 主路径、`/tasks`=app 主路径、`/instances`=engine 后端 driver API |

## [archive/](archive/) —— 历史/已作废，**不要当现状读**
仅保留少量早期收敛设计作为演进证据；当前架构以本目录 active 文档为准。
