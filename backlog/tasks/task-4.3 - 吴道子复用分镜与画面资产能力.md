---
id: task-4.3
title: 吴道子复用分镜与画面资产能力
status: To Do
assignee: []
created_date: '2026-06-23'
labels:
  - frontend
  - pipeline
  - wudaozi
dependencies:
  - task-4.1
  - task-4.2
parent_task_id: task-4
priority: high
---

## Description

吴道子第一版不新造一套旧 agent 执行链，而是复用当前主链已经跑通的视觉能力：

- 分镜和视觉方案：复用 `storyboard` 节点、`storyboard_director.py`、`run_storyboard_step` 及现有前端分镜展示/编辑能力。
- 图片资产：复用 `image` 节点、`PipelineImageRun` 及现有图片结果展示、下载、重试能力。
- 产物契约：保留当前 episode / scene / asset 路径结构，不引入 figure_talk、stickman 或旧模板。

当前 backend DAG 应与产品阶段一致：

1. 吴道子前半段完成视觉方案和分镜。
2. 吴道子继续生成画面资产。
3. 画面资产确认后，交给伯牙完成声音。

## Implementation Notes

- 不 import、不调用 `src/ncds_opus_factory/commands/wudaozi.py`。
- 不新增第二套 image 输出目录。
- 图片下载优先使用已落在画布中的现成 asset URL，避免重新发明下载链路。
- 若发现 `ImageResultPanel` 与画布下载按钮能力重复，应收敛到同一套 URL resolve/download helper。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 吴道子视觉方案使用当前 `storyboard` 节点产物，分镜内容、场景提示词、镜头结构能正常展示
- [ ] #2 图片资产使用当前 `image` 节点产物，已生成图片能在吴道子视觉工作台中查看、下载、重试
- [ ] #3 当前 `storyboard -> image -> tts` 的技术顺序与 UI 一致；用户不会在吴道子画面资产阶段被伯牙声音卡住
- [ ] #4 代码中不新增对旧 `commands/wudaozi.py`、figure_talk 模板或 stickman 资产的依赖
- [ ] #5 一条已有 job 的历史 storyboard/image 输出能被新工作台正确读取和展示
<!-- AC:END -->
