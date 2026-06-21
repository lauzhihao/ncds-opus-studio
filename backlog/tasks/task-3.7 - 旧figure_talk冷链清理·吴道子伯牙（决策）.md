---
id: task-3.7
title: 旧 figure_talk 冷链清理·吴道子/伯牙（决策）
status: To Do
assignee: []
created_date: '2026-06-19 13:46'
labels:
  - decision
  - cleanup
  - dead-code
dependencies: []
parent_task_id: task-3
priority: low
---

## Description

下游未实现 agent 的旧 figure_talk 实现及其冷链附属物（见 CODE-REVIEW §4.4 D2/D4/D5）：

- `commands/wudaozi.py`（450 行）/ `commands/boya.py`（482 行）+ 各 `_test.py` —— 完整旧实现（非 stub），
  产 figure_talk/剪影成片。**但 live 生产线（web 画布 + engine）走 storyboard→tts→image→render_015，
  完全不经 wudaozi/boya**。
- 附属冷链：`templates/figure_talk/`、`templates/stickman/`、`scripts/scan_figure_lib.py`、
  `scan_audio_lib.py`、`scripts/{wudaozi,boya,shenkuo}_sop.md`、`assets/{figure_lib,audio_lib}/`、
  core `render.py`（非 015 录屏器）、factory `templates/paper_card_talk`（非 015 目录）。

**关键约束**：wudaozi/boya 经 `commands/__init__.py` 进 registry，**app 决策视图 catalog 仍列为可派单
agent**（`app/lib/features/home/agent_catalog.dart`），app 有 WudaoziResult/BoyaResult 解析器。所以
技术上 reachable from `/tasks`，**不是纯死代码**。

## ⚠️ 决策（DEC-3，等 owner）

owner 原话："吴道子/伯牙/渲染都还没实现或是旧实现，不必为了保住这些旧实现而搞兼容屎山。"

- **默认建议**：**暂不物理删**（删 wudaozi/boya 会连带删 app catalog tile = 改 app UI，违背"不破坏
  UI"）。改为**标记冷链**：在 wudaozi.py/boya.py 顶部 + agent_catalog 注明"旧 figure_talk 实现/冷链，
  重写吴道子-伯牙-渲染时直接替换、不必兼容"。等真正重写下游时一次性替换（与 owner 意图一致）。
- 可独立先删的无引用项：`scripts/{wudaozi,boya,shenkuo}_sop.md`（零代码引用，但属低价值 doc，建议随
  本任务一并处理而非单独删）。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 owner 拍板"现在删整条冷链（连 app catalog）" vs "标记冷链、重写时替换"
- [ ] #2 若删：bundle 一次清掉 commands + app catalog/models/panel + 模板 + scan 脚本 + assets + core render.py + factory 非015 模板 + SOP md，回归 586 passed 且 app 编译通过
- [ ] #3 若标记：加冷链注释，关单，留待下游重写任务
<!-- AC:END -->
