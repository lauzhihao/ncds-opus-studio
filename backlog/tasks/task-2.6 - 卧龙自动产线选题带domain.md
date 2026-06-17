---
id: task-2.6
title: 卧龙自动产线选题带 domain（rounds 路径补线）
status: To Do
assignee: []
created_date: '2026-06-17'
labels:
  - domain-tag
  - backend
parent_task_id: task-2
priority: medium
---

## Description

task-2.5 已把 web/交互路径的选题（鬼谷子）domain 接通：`pipeline_runner._run_guiguzi_analyze_bg` / `_run_guiguzi_generate_bg` 从 `state.inputs["domain"]` 透传给 `guiguzi.analyze/generate_topics`。

但**卧龙自动产线**这条路径还没带 domain：`src/ncds_opus_factory/commands/wolong_rounds.py` 的 `open_round` 里 `params = {"items": items}` 调 guiguzi，未含 domain。要补这条线需要 round 先持有"源作品 domain"，涉及 rounds store 的数据结构（round 记录里加 domain 字段，从源作品 `works_repo.load_domain` 取并落库），再在 open_round 调 guiguzi 时透传。

非交互主路径，单列跟进；guiguzi 内部已支持 `domain` 参数（task-2.5 X 已做），本任务只补"调用方 + rounds 数据结构"。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 round 记录能携带 domain（从源作品/源对标号继承，rounds store 落库），缺失回退 None
- [ ] #2 `wolong_rounds.open_round` 调 guiguzi 时透传 round 的 domain
- [ ] #3 空/未知 domain 回退原通用选题；有测试
- [ ] #4 pytest 全绿（两路核对）
<!-- AC:END -->
