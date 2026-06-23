# AGENTS.md — ncds-opus-factory

> **权威指令在 [CLAUDE.md](CLAUDE.md)**（项目结构、执行协议 L0–L2、运行环境）。
> 本文件只为读 AGENTS.md 的工具（codex / cursor / aider 等）留一份速览，**有冲突以 CLAUDE.md 为准**。
> 接手三步：读 `docs/README.md`（索引/runbook）→ `.project_map`（结构/入口）→ `docs/PRODUCTION-ENGINE-DESIGN.md`（权威设计/为什么）。

## 这是什么

内容生产引擎：底层能力（asr/rw/wst/tst/vid/tts/render）+ agent 编排 + FastAPI server（:8810，含 `/studio` web 前端）+ Flutter app（`app/`，决策视角）+ 视频模板。
**当前方向**：web（内容视角）+ app（决策视角）统一到一个 agent 驱动的生产实例引擎之上。当前进度以 `docs/README.md`「当前方向与进度」为准；架构语义以 `docs/PRODUCTION-ENGINE-DESIGN.md` 为准。

## 硬约束（细则见 CLAUDE.md）

1. **不包含外部协作文档/消息平台交互**。项目代码不保留相关 SDK / OAuth / 文档或消息交付链路。
2. **进度回调机制**。命令接收 `on_progress: Callable[[str], None]`；命令本身不假设回调到哪里去（终端 / 文件 / noop）。
3. **分级执行协议**。大改先对齐再动手，小改直做，不自作主张扩大范围（L0/L1/L2 见 CLAUDE.md Part 2）。
4. **沟通用简体中文**，技术术语保留英文；日志只用 ASCII（无 emoji）。
5. **develop 永不并 main**。工作主线是 develop，main 保留旧 web 画布副本作护城河。

## 运行时（S3 后 = 三进程）

`redis`（队列）+ `nof-server`（:8810，入队+SSE，纯 producer）+ `nof-worker`（唯一执行）。按序起，重启 server 不打断在跑任务。红线见 CLAUDE.md「运行红线」；命令见 `docs/README.md`「三进程」。
