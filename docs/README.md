# docs 索引（接手先读这里）

**ncds-opus-factory** = 内容生产引擎：底层能力（asr/rw/wst/tts/render…）+ FastAPI server（:8810，含 `/studio` 前端）。
项目说明/执行规约见仓库根 [CLAUDE.md](../CLAUDE.md) 与 [AGENTS.md](../AGENTS.md)；本目录只放设计/契约文档。

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
