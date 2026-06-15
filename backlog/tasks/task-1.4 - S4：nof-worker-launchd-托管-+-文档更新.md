---
id: task-1.4
title: S4：nof-worker launchd 托管 + 文档更新
status: To Do
assignee: []
created_date: '2026-06-15 02:39'
labels:
  - worker-split
  - docs
  - ops
dependencies:
  - task-1.3
parent_task_id: task-1
priority: medium
---

## Description

父任务 task-1，依赖 S3(task-1.3)。让 nof-worker 常驻、与 8810 解耦、开机自启，对标 map_watchdog 的 launchd 方式。写 launchd plist + scripts/install_nof_worker.sh(install/status/logs/restart/uninstall，参考 scripts/install_map_watchdog.sh)；更新文档。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 scripts/install_nof_worker.sh install/status/logs/restart/uninstall 可用、开机自启
- [ ] #2 docs 更新：本地运行章节从单 nof-server 改为 nof-server(HTTP)+nof-worker(执行)含启停；PRODUCTION-ENGINE-DESIGN 标注落地
<!-- AC:END -->

## Implementation Plan

复制改造 install_map_watchdog.sh；plist 跑 .venv/bin/nof-worker、日志落 state/；文档改 docs/README、PRODUCTION-ENGINE-DESIGN、CLAUDE.md 第9节。
