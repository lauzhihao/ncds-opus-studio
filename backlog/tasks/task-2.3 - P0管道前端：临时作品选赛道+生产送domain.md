---
id: task-2.3
title: 'P0 管道·前端：临时作品选赛道 + 生产时把 domain 送进 instance inputs'
status: Done
assignee: []
created_date: '2026-06-17'
labels:
  - domain-tag
  - frontend
dependencies:
  - task-2.2
parent_task_id: task-2
priority: high
---

## Description

打通"手动临时作品选标签"与"生产时把 domain 送进引擎"两条前端路径。

### 1. 临时作品选赛道
粘贴链接解析（`POST /works/resolve`，`routes/works.py`）得到的作品卡目前无标签入口。前端在作品卡上加赛道单选（复用 `web/src/config/domains.ts` 单一数据源，默认 `DEFAULT_DOMAIN`）。选定后写回该作品 manifest——需一个轻量后端端点（如 `PATCH /works/{platform}/{aweme_id}/domain` 或并入现有 works 路由），落 `works_repo.save_domain`（依赖 task-2.2 的读写函数）。

### 2. 生产时送 domain
创建生产作业时（前端发起 `POST /instances` 或现有 job 创建路径），把作品的 `domain`（继承来的或手动选的，前端已能从作品卡 `domainByKey` 拿到）放进 instance inputs，使其经 task-2.2 的透传到达各 performer。

### 3. 继承作品的展示
优质作者继承来的作品（manifest 已带 domain）在卡片上显示对应徽标（现有 `domainByKey` 逻辑），不被覆盖，除非用户主动改判。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 临时作品卡加赛道单选（HomePage.tsx AddTempTaskModal），复用 `domains.ts`，默认 `DEFAULT_DOMAIN`
- [x] #2 `PATCH /works/{platform}/{aweme_id}/domain` → `works_repo.save_domain`；resolve 响应回显已存 domain
- [x] #3 生产创建 `doCreate` 把 `inputs:{domain}` 送进 createJob → 经 task-2.2 透传到 performer
- [x] #4 继承来的作品按 resolve 回显的 domain 预选单选器，不被 DEFAULT_DOMAIN 覆盖
- [x] #5 `npm run build`（vite）通过 + tsc 0 error；test_works.py +3 通过
<!-- AC:END -->

## 验收（2026-06-17，主线程亲验）

委派 sonnet 执行，主线程读 diff + 独立跑 tsc/vite/pytest：
- 端点校验对齐 `DOMAIN_PROFILES.keys()`（finance/emotion），未知 422；resolve 顺带 `load_domain` 回显（避免前端多一次 RTT/竞态——合理）。
- 主线程独立：`npx tsc --noEmit` 0 error、`npm run build` 成功（1954 modules）、全量 pytest 552 passed。
- 多作品时 domain 取 `ready[0].domain`（subagent 决策，合理：同批应同赛道，可逐卡调整）。
