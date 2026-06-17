---
id: task-2.1
title: 删除足球赛道标签占位
status: Done
assignee: []
created_date: '2026-06-17'
labels:
  - domain-tag
  - cleanup
dependencies: []
parent_task_id: task-2
priority: high
---

## Description

赛道标签确认只保留财经（finance）+ 情感（emotion），删除足球（football）占位。订阅文件 `state/shenkuo/subscriptions.json` 当前**无 football 作者**（已核实：emotion×3、finance×1），删除零存量风险。

精确删除位置（来自勘察）：

- `web/src/config/domains.ts`：line 7 注释、line 10 `DomainKey`、line 16 `colorClass` 类型、line 21 DOMAINS 数组里的 football 项。
- `web/src/styles/scss/_agent.scss`：line 95 `.domain-badge.football {...}`、line 891 `&.football { --domain-c: ... }`。
- `src/ncds_opus_factory/server/domain_profiles.py`：line 3 注释、line 27 `"football"` 占位项。
- `src/ncds_opus_factory/server/subscriptions.py`：line 90 注释里的 football 字样（改注释，保持一致）。
- `web/src/api/types.ts`：line 251 注释 `finance/football/emotion`（改注释）。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `domains.ts` 的 `DomainKey`/`DOMAINS`/`colorClass` 去掉 football，`DEFAULT_DOMAIN` 仍有效（指向 finance）
- [x] #2 `_agent.scss` 中 `.domain-badge.football`/`.domain-pill &.football` 配色删除
- [x] #3 `domain_profiles.py` 的 `DOMAIN_PROFILES` 去掉 `"football"`
- [x] #4 全仓 `grep -rn "football"` 与 `grep -rn "足球"` 仅余有意保留项（无遗漏的代码/类型/配色/测试引用；注释一并清理）
- [x] #5 web `npm run build`（tsc + vite）通过；pytest 全绿
<!-- AC:END -->

## 验收（2026-06-17，主线程亲验）

委派 sonnet 执行 + 主线程独立复核：
- 代码零残留：`grep -rn "football\|足球"` 限 `*.ts/*.tsx/*.scss/*.py` 均无匹配（仅 backlog 文档留历史记录）。
- 主线程独立跑 `npx tsc --noEmit` EXIT=0；`domain_profiles` keys = `['emotion', 'finance']`。
- subagent 额外发现并清理了清单外的 `server/routes/subscriptions.py:36` 与 `_agent.scss` 顶部两处注释（纯注释，合理）。
- `DEFAULT_DOMAIN = DOMAINS[0].key` 删 football 后仍指向 finance，未受影响。
