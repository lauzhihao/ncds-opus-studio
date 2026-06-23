---
id: task-4
title: 吴道子/伯牙第一版产线收敛
status: To Do
assignee: []
created_date: '2026-06-23'
labels:
  - product
  - frontend
  - pipeline
  - wudaozi
  - boya
dependencies:
  - task-3.7
priority: high
---

## Description

把 web 端柳永之后到成片 MP4 之前的下游体验收敛为第一版可用产线：**吴道子负责画面，伯牙负责声音**。

当前事实边界：

- 当前 web/engine 主链是 `final_preview`，目标节点顺序是 `storyboard -> image -> tts -> preview -> render`。
- `commands/wudaozi.py` / `commands/boya.py` 已在 task-3.7 标记为旧 figure_talk 冷链，第一版不复活、不兼容、不强行复用。
- web 端现有吴道子面板里的 `台词 / 分镜 / 画质` 是实现残留的暴露方式；其中 `lines` 应作为吴道子进入前的隐藏准备，不应让用户把它理解成吴道子职责。
- 第一版直接把 `image` 调整到 `tts` 前：吴道子先完成视觉方案和画面资产，再交给伯牙做声音。

第一版目标：

1. 柳永产稿后，用户进入的是吴道子视觉工作台，而不是三个技术 tab。
2. 吴道子自动准备台词结构，主工作是分镜、画面提示词、画面资产检查和重生成。
3. 伯牙包装现有 TTS 能力，负责声音结果、重试和进入后续成片。
4. 保留当前 `/jobs` + nof-worker + engine 路径，避免在 UI 收敛阶段同时改执行内核。

## 子任务

- task-4.1 吴道子视觉工作台前端收敛
- task-4.2 `lines` 隐藏前置与自动准备
- task-4.3 吴道子复用分镜与画面资产能力
- task-4.4 伯牙声音面板包装现有 TTS
- task-4.5 产线推进、节点状态与文案对齐
- task-4.6 第一版验收与文档收敛
- task-4.7 P0 `lines` 字幕保真：不得压缩柳永定稿

## Non-goals

- 不删除旧 `commands/wudaozi.py` / `commands/boya.py` 冷链；删除或替换放到后续专门任务。
- 不把 app 决策视图 catalog 和 web 生产画布强行一次性统一。
- 不在第一版改写视频 render、模板录屏器或 MP4 合成策略。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 柳永产稿后，web 画布的下一步以"吴道子/画面"呈现，用户不再看到残留式 `台词 / 分镜 / 画质` 三 tab 作为主入口
- [ ] #2 进入吴道子时，`lines` 缺失或过期能自动准备；失败时给出可重试状态，不要求用户理解 `lines` 节点
- [ ] #3 吴道子不调用旧 figure_talk 冷链，复用当前 `storyboard` / `image` 生产能力和现有产物契约
- [ ] #4 伯牙面板包装当前 TTS，能展示声音产物、重试状态，并把完成态交给成片检查流程
- [ ] #5 在当前三进程运行时下，完成一条从柳永稿件到 MP4 render 的 happy path 验收
<!-- AC:END -->
