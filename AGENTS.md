# AGENTS.md — ncds-opus-factory

本项目是 ncds 内容生产的核心引擎。

## 硬约束

1. **不接入飞书 API**。代码里不出现 `open.feishu.cn` / Feishu SDK / OAuth 流程。所有飞书 IO 由调用方走 `lark-cli`（subprocess）。
2. **5 个命令的边界清晰**。`/wst /tst /vid /asr /rw` 各是一个独立的 Python 模块，可单独 CLI 跑通，可单独被 import。
3. **进度回调机制**。每个命令接收 `on_progress: Callable[[str], None]` 参数；命令本身不假设回调到哪里去（飞书 / 终端 / 文件 / noop）。

## 工作原则

- 修改命令逻辑时，先验证该命令的 CLI 能独立跑通（不依赖 lark-bot-listener）。
- 修改 Node runner 时，遵循 `scripts/` 下的代码风格（ESM、`node:` 前缀的内置模块、`async/await`）。
- 修改 009 模板（`src/ncds_opus_factory/templates/paper_card_talk/`）时，参考目录下的 README.md，那是模板化方法的金本。

## 当前迁移状态

详见 `docs/MIGRATION.md`。
