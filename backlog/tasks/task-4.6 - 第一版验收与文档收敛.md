---
id: task-4.6
title: 第一版验收与文档收敛
status: To Do
assignee: []
created_date: '2026-06-23'
labels:
  - qa
  - docs
  - wudaozi
  - boya
dependencies:
  - task-4.1
  - task-4.2
  - task-4.3
  - task-4.4
  - task-4.5
parent_task_id: task-4
priority: medium
---

## Description

第一版实现后做端到端验收，并把文档中的旧描述收敛到最新事实。重点不是补大篇说明，而是避免后续维护者再误以为旧 `commands/wudaozi.py` / `commands/boya.py` 是 web 主产线。

验收范围：

- 本地三进程运行：Redis + nof-server + nof-worker。
- web `/studio` 或当前生产画布 happy path。
- 从一个柳永已产稿 job 进入吴道子，再到伯牙，再到画面资产补齐和 render。
- 历史 job 打开、失败重试、刷新恢复。

文档范围：

- `docs/README.md` 的当前方向/进度需要记录吴道子/伯牙第一版职责。
- `.project_map` 若列出 web 面板或 agent 映射，需要同步最新入口。
- `docs/PRODUCTION-ENGINE-DESIGN.md` 只记录架构事实：第一版 UI 聚合不等于 backend DAG 已重排。
- task-3.7 保留旧冷链标记，不改成"已删除"。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `npm run build` 通过；若触及 Python 执行链，相关 pytest 或最小回归通过
- [ ] #2 本地三进程下完成一条柳永稿件到 MP4 render 的 happy path，并记录 job id 或验收截图
- [ ] #3 历史 job 打开能正确显示吴道子/伯牙新面板，不要求重新跑全链
- [ ] #4 `docs/README.md`、`.project_map`、`docs/PRODUCTION-ENGINE-DESIGN.md` 与第一版事实一致
- [ ] #5 backlog 中记录后续是否需要 backend DAG 语义重排；第一版不把这件事混入实现
<!-- AC:END -->
