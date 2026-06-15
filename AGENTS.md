# AGENTS.md — ncds-opus-factory

> **权威指令在 [CLAUDE.md](CLAUDE.md)**（项目结构、执行协议 L0–L2、lark-cli 边界、运行环境）。
> 本文件只为读 AGENTS.md 的工具（codex / cursor / aider 等）留一份速览，**有冲突以 CLAUDE.md 为准**。
> 接手第一步：读 `docs/README.md`（索引）→ `docs/PRODUCTION-ENGINE-DESIGN.md`（权威设计）。

## 这是什么

内容生产引擎：底层能力（asr/rw/wst/tst/vid/tts/render）+ FastAPI server（:8810，含 `/studio` web 前端）+ Flutter app（`app/`，决策视角）+ 视频模板。
**当前方向**：web（内容视角）+ app（决策视角）统一到一个 agent 驱动的生产实例引擎之上。已落地：抽 `packages/core`（6 primitive + `build_full_registry()`）+ 引擎骨架（`src/ncds_opus_factory/server/engine/`）+ app 入仓。

## 硬约束（细则见 CLAUDE.md Part 1）

1. **不接入飞书 API**。代码里不出现 `open.feishu.cn` / Feishu SDK / OAuth 流程。所有飞书 IO 由调用方走 `lark-cli`（subprocess）。
2. **进度回调机制**。命令接收 `on_progress: Callable[[str], None]`；命令本身不假设回调到哪里去（飞书 / 终端 / 文件 / noop）。
3. **分级执行协议**。大改先对齐再动手，小改直做，不自作主张扩大范围（L0/L1/L2 见 CLAUDE.md Part 2）。
4. **沟通用简体中文**，技术术语保留英文；日志只用 ASCII（无 emoji）。

## 运行时（S3 后 = 三进程）

`redis`（队列）+ `nof-server`（:8810，入队+SSE，纯 producer）+ `nof-worker`（唯一执行）。按序起，重启 server 不打断在跑任务。细节见 CLAUDE.md §9。
