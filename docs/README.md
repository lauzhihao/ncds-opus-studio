# docs 索引（接手先读这里）

**ncds-opus-factory** = 内容生产引擎：底层能力（asr/rw/wst/tts/render…）+ FastAPI server（:8810，含 `/studio` 前端）。
项目说明/执行规约见仓库根 [CLAUDE.md](../CLAUDE.md) 与 [AGENTS.md](../AGENTS.md)；本目录只放设计/契约文档。

> **运行时（S3 后）**：redis(队列) + `nof-server`(:8810，入队+SSE) + `nof-worker`(执行)。重启 server 不打断在跑任务。设计见 backlog/docs/S3-redis-worker-design.md；红线见 CLAUDE.md §6；命令见下「本地运行」。

## 本地运行（runbook）

> CLAUDE.md 只留红线，具体命令在这里。

### Python venv
`.venv` 是 uv 管理的 Python 3.12，**没有自带 pip**。装包：
```bash
uv pip install --python .venv/bin/python3 <pkg>
```
不要用全局 pip，也不要假设 `.venv/bin/pip` 存在（历史上 3.13 残留 pip 会把包静默装进 `lib/python3.13/site-packages`，运行时 import 不到；再看到 `.venv/lib/python3.13/` 说明被污染，直接删）。

### 三进程（按序起）
离线任务执行已从 nof-server 拆到独立 `nof-worker`，跨进程队列走 Redis：
1. `redis-server`（队列信道，先起；`brew services start redis` 或临时 `redis-server`）
2. `nof-server`（:8810，HTTP/SSE/入队，纯 producer 不执行任务；`NOF_SERVER_HOST`/`NOF_SERVER_PORT` 可覆盖）
3. `nof-worker`（`.venv/bin/python3 -m ncds_opus_factory.server.worker`，唯一消费+执行+写状态）

Redis 连不上 → nof-server `POST /tasks` 返 503、nof-worker fail-fast 退出。同 cmd 只能起一个 nof-worker（出队 CAS 单进程语义）。重启 nof-server 不打断 worker 在跑任务。launchd 常驻见 `scripts/install_nof_worker.sh`。

### /studio 前端（web/，React + Vite）
- dev：`cd web && npm run dev`（vite :5173）+ `NOF_DEV=1 nof-server`，访问 `http://localhost:8810/studio`，HMR 同走 8810 反代（依赖 httpx + websockets，已声明 pyproject）。
- prod：`cd web && npm run build` 生成 `web/dist`，再起 `nof-server`。
- 挂载在 server import 时定死：没设 `NOF_DEV=1` 且 `web/dist` 不存在 → `/studio` 404，补构建后必须重启 server。
- 前端 API 走同源相对路径（`/jobs` `/pipelines` `/tasks` `/preview`），无需跨域/baseUrl 配置。

### 项目地图看门狗
`.project_map` 由 `scripts/map_project_watchdog.py` 自动重生成（mtime 轮询 + 30s 去抖）。手动重生成：`python3 scripts/map_project.py`。launchd 自启：
```bash
./scripts/install_map_watchdog.sh install|status|logs|restart|uninstall
```
日志 `state/map_watchdog.{out,err}.log`、PID/锁 `state/map_project_watchdog.{pid,lock}`（均 gitignored）。若 `.project_map` 比 `src/` 源文件旧 → 看门狗没在跑。

## 当前方向与进度（2026-06-13）
**权威设计 = [PRODUCTION-ENGINE-DESIGN.md](PRODUCTION-ENGINE-DESIGN.md)**：把 web（作品/内容视角）与 app
（agents/决策视角）统一到**一个 agent 驱动的生产实例引擎**之上（取代早先的"core/studio/factory 三包对等拆分"）。

进度：**P1 抽 core 已完成**（`packages/core`，6 primitive + `build_full_registry()`）；
**E0 引擎骨架 + E1-a driver API + E1-b1 /instances 路由 + E1-b2 全 7 步 step-performer（lines/storyboard/tts/image/render/asr/rw 真实包装 + 引擎驱动真实 015 链端到端出 mp4 验证）已落地**
（`src/ncds_opus_factory/server/engine/` 全套 driver 原语 + `routes/instances.py` + `pipeline_performers_015.py` + 多轮评审加固，382 passed）；
**E1-b2 #2 全局 recipe 绑定 + #3 绞杀者已落地**（`NOF_ENGINE_NODES` 命中节点执行改走引擎、UI 不变，389 passed）；
**下一步**=引擎补步内增量 outputs（让 asr/rw 也能改道不丢实时进度）+ 全切换前端直走 /instances（见设计 §10）。
⚠️ 护城河：web 旧画布可跑副本在 `main`，本 branch 不并 main 就毁不掉它。

## 活文档（current）
| 文档 | 作用 |
|---|---|
| **[PRODUCTION-ENGINE-DESIGN.md](PRODUCTION-ENGINE-DESIGN.md)** | **权威设计**：目标架构、核心抽象、步骤生命周期/介入点、多配方、E0–E5 迁移分期 |
| [WOLONG-DESIGN.md](WOLONG-DESIGN.md) | 卧龙子系统实装（调度/闸门/离线学习）——在新架构里=app driver + 自治神经层的规格 |
| [FRONTEND-API.md](FRONTEND-API.md) | 对外 HTTP API 契约（E1 起新增 `/instances` 路由，会随之演进） |
| [FEISHU-REFACTOR.md](FEISHU-REFACTOR.md) | 飞书 IO → lark-cli 边界的改造记录（设计原则：命令不直调飞书） |

## [archive/](archive/) —— 历史/已作废，**不要当现状读**
三包对等拆分系列（MONOREPO-SPLIT-{DESIGN,HANDOFF,PATHS}）、CONVERGENCE-DESIGN、MIGRATION（旧
lark-bot-listener→factory 合并）。仅留作"为什么走到今天"的证据；其中 MONOREPO-SPLIT-DESIGN §9.x 是
**P1 抽 core 的 as-built 记录**，要追 P1 细节时可查。
