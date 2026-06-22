---
id: task-3.10
title: 文档记忆与实现现状对齐
status: Done
assignee: []
created_date: '2026-06-22'
labels:
  - docs
  - memory
  - cleanup
dependencies: []
parent_task_id: task-3
priority: high
---

## Description

把 agent / 接手者会优先读取的“记忆层”更新到当前实现事实，避免后续按过期设计、旧进度或错误入口继续推进。

本任务只处理文档、指令、注释、测试说明、项目地图生成器等低风险信息面；不得借机删除活路径、改运行时分叉或重构
PipelineRunner / TaskRunner / engine。

当前实现事实快照：

1. web 内容生产主路径仍是 `/jobs/*` -> `PipelineRunner` facade -> engine nodes。rw/lines/storyboard/tts/
   image/render 等大多已通过 engine 执行；asr 仍被 `node_name != "asr"` 强制走 legacy。
2. Flutter app 当前主路径仍是 `/tasks` -> `TaskRunner` / `nof-worker`，尚未切到 `/instances`。
3. `/instances` backend driver API 与测试已存在，但不是 web/app 前端主路径。
4. `RECIPE_REGISTRY` 当前只有 `paper_card_talk_015`；`figure_talk` 仍属于 future / cold chain，不应写成已注册主路径。
5. nof-server 是纯 producer + serve；nof-worker 是唯一执行体。重启 server 不应中断在跑任务。
6. 测试基线需要以执行当日 `pytest --collect-only` / `pytest` 的真实结果为准，不继续引用过期的 382/389 或 586 passed。

## 批次范围

1. `docs/README.md`
   - 更新“当前进度”日期与事实快照。
   - 删或改写“asr/rw 改道”这类已过期下一步表述。
   - 明确 web、app、instances 三条入口的当前关系。

2. `docs/PRODUCTION-ENGINE-DESIGN.md`
   - 修正 `NOF_ENGINE_NODES` 默认行为说明：当前不是“默认空、零行为变化”。
   - 明确 asr 的实际 legacy 分叉、rw 的 engine 现状、`figure_talk` 未注册事实。
   - 保留设计目标与演进方向，但把“已实现/未实现/未来项”分开。

3. `docs/FRONTEND-API.md`
   - 对齐 `/jobs`、`/tasks`、`/instances` 的资源边界和实际消费者。
   - 避免把 `/instances` 写成已替代 web/app 主路径。

4. Feishu / lark-cli 相关记忆
   - 更新 `asr.py`、`rw.py` 等命令 docstring 中“feishu_sdk_adapter 直连 OpenAPI”的过期描述。
   - 检查 docs / tests / scripts 中对飞书 IO 的描述，统一为调用方通过 `lark-cli` subprocess 负责。
   - 处理 `scripts/feishu_sdk_adapter.mjs` 中 `open.feishu.cn` 字面量与根指令红线的冲突：删除、隔离或显式废弃，需先确认零活路径。

5. `.project_map` 生成链路
   - 更新 `scripts/map_project.py` 中过期标签，例如 “SDK / CLI wrappers”、`/rw` 仅 deepseek、app 只写 `/tasks` 等。
   - 重新生成 `.project_map`，确保地图能反映当前入口和架构事实。
   - 注意 `.project_map` 是 ignored/generated 文件；若要纳入提交，需单独说明。

6. `app/README.md`
   - 替换默认 Flutter starter 内容。
   - 写成当前 app 的真实定位：决策视角移动控制台、通过服务端任务入口驱动生产，不直接等同 web `/studio`。

## 非目标

- 不删除 `PipelineRunner`、legacy `_execute_*`、`TaskRunner`、`figure_talk` cold chain 或任何仍可能被引用的活路径。
- 不改变 `NOF_ENGINE_NODES` 行为、不切换 app 到 `/instances`、不调整 worker/server 运行时。
- 不做主分支合并；继续遵守 develop working branch 约束。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `docs/README.md` 的进度、入口关系、测试基线描述不再引用过期的 382/389 或“asr/rw 改道下一步”。
- [x] #2 `docs/PRODUCTION-ENGINE-DESIGN.md` 区分当前实现、设计目标、未来项，并准确描述 `NOF_ENGINE_NODES` 默认与 asr legacy 分叉。
- [x] #3 `docs/FRONTEND-API.md` 准确描述 `/jobs`、`/tasks`、`/instances` 的当前职责和消费者。
- [x] #4 Feishu 相关 docstring / docs / tests 不再误导为直连 OpenAPI；`open.feishu.cn` 字面量冲突被清理或明确废弃且确认零活路径。
- [x] #5 `scripts/map_project.py` 反映当前实现事实；`.project_map` 由看门狗自动刷新，本次不手动生成、不主动提交。
- [x] #6 `app/README.md` 不再是默认 Flutter starter，能作为 app 接手入口。
- [x] #7 文档清理不改运行时行为；如只改 Markdown/docstring，可不跑全量测试，但需记录未跑原因。
<!-- AC:END -->

## 完成记录

- 2026-06-22：已对齐 docs / app README / Feishu docstring / `scripts/map_project.py`。遵 owner 要求未手动调用 `scripts/map_project.py`，`.project_map` 交由看门狗自动刷新。
- 验证：`node --test scripts/feishu_sdk_adapter.test.mjs`、`python3 -m py_compile scripts/map_project.py src/ncds_opus_factory/commands/asr.py src/ncds_opus_factory/commands/rw.py`、`git diff --check` 通过；关键词检查在当前事实文档与代码范围内无旧命中。
- 未跑全量 `pytest`：本批只改 Markdown、docstring、map 生成摘要和 Feishu adapter 常量等低风险信息面；未改变任务执行路径。
