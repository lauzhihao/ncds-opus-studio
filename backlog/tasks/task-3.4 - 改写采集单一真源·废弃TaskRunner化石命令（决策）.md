---
id: task-3.4
title: 改写/采集单一真源·废弃 TaskRunner 化石命令（决策）
status: Done
assignee: []
created_date: '2026-06-19 13:46'
labels:
  - decision
  - logic-divergence
  - cleanup
dependencies: []
parent_task_id: task-3
priority: high
---

## Description

同一能力多份实现已漂移（见 CODE-REVIEW §4.1 A3/A4、§6）：

**改写 4 套**：
- `commands/rw.py`：输入=飞书 docx URL，profile=`douyin`，spawn `rewrite_command_runner.mjs`，**零质检**，写飞书。docstring/argparse 还写"gpt-5.5+gemini"（已漂移）。
- `commands/liuyong.py`：输入=一句话选题，profile=`douyin_cog`，spawn `content_rewrite_runner.mjs`，自带 QC。
- `_execute_rw`（legacy）/ `run_rw_step`（engine）：输入=asr 清洗稿，直调 opus+deepseek（不走 .mjs），QC=`_apply_rw_qc`。engine import legacy = 这两者已是单一真源。

**采集 3 套**：collect_one 快采（legacy 活路径）/ engine `run_asr_step`（video_pipeline，**死代码**，被 `:1306` 排除）/ commands/shenkuo.collect_one 全量。

`commands/liuyong.py` + `commands/rw.py` 是旧 TaskRunner 时代化石，被 `task_runner`/`command_schemas`/
`label_store`/`mock_agents`/`artifacts` 注册引用。web `/jobs` 链**不**用它们。

## ⚠️ 决策（DEC-4，等 owner）

废弃 `commands/liuyong.py` + `commands/rw.py`？
- **默认建议**：先核实 app `/tasks` 与 CLI 是否仍在实际派 liuyong/rw；**若否**则废弃并从
  command_schemas/label_store/mock_agents/artifacts 清理残留；**若是**则先把 web 链的 `_apply_rw_qc`
  统一口径反哺，再决定。
- **为何没替你执行**：仍被 registry/CLI 引用，删可能断 app 派单 + CLI，需确认使用面。

## 实施记录

2026-06-22 已核实并执行收敛：

- `commands/liuyong.py` **仍是活入口，不能删**：
  - app catalog 暴露 `cmd: 'liuyong'`：`app/lib/features/home/agent_catalog.dart`
  - app 详情页会通过 `/tasks` 重新派发 `widget.agent.cmd`：`app/lib/features/detail/liuyong_task_detail_screen.dart`
  - `/tasks` / worker registry 仍注册 `liuyong`：`src/ncds_opus_factory/commands/__init__.py`、`src/ncds_opus_factory/server/command_schemas.py`、`src/ncds_opus_factory/server/task_runner.py`
  - 多个 server 测试以 `liuyong` 作为真实任务类型覆盖 rounds/review/labels/scheduler。
- `commands/rw.py` **仍是活入口，不能删**：
  - CLI 仍 dispatch `nof rw` 到 `commands.rw._cli`：`src/ncds_opus_factory/cli.py`
  - `/tasks` registry/schema 仍注册 `rw`：`src/ncds_opus_factory/commands/__init__.py`、`src/ncds_opus_factory/server/command_schemas.py`
- engine `run_asr_step` 已改为调用 `shenkuo.collect_one` 快采，移除内部 video_pipeline/polish/cache 口径。
- rw 源文本已固定优先使用 collect entry 的 `text`，保留 `article_relpath`/`transcript_relpath` legacy fallback；测试见 `tests/server/test_shenkuo_collect.py::test_rw_source_text_prefers_collected_text`。
- 文档/文案漂移已修：
  - `pipeline_runner.py` 顶部注释已无过期 rw 模型说明。
  - `commands/rw.py` docstring/argparse 当前不再写 `gpt-5.5+gemini`。
  - `scripts/rewrite_command_runner.mjs` 旧进度文案已从具体模型名改为通用“双模型改写”。
- 决策结果：**本轮不废弃** `commands/liuyong.py` / `commands/rw.py`，因此不清注册表；等 app/CLI 入口迁移后再开删除卡。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 核实 commands/liuyong.py、commands/rw.py 是否仍被 app /tasks 或 CLI 真实使用（给证据）
- [x] #2 死代码 engine `run_asr_step`：删除或让其内部也调 collect_one 统一口径（二选一，消除"注册了跑不到"）
- [x] #3 rw 源文本口径固定为 collect_one 的 entry.text（§6.4）
- [x] #4 修文档漂移：`pipeline_runner.py:15` rw 注释、`commands/rw.py` docstring/argparse 的"gpt-5.5+gemini"
- [x] #5（决策后）废弃化石命令则注册表残留全清，回归 586 passed；本轮决策为保留活入口，注册表不清理
<!-- AC:END -->
