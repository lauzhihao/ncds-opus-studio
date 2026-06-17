---
id: task-2.2
title: 'P0 管道·后端：作品 manifest 落 domain + 沈括继承 + 生产实例透传'
status: Done
assignee: []
created_date: '2026-06-17'
labels:
  - domain-tag
  - backend
dependencies: []
parent_task_id: task-2
priority: high
---

## Description

让标签"流起来"，**纯管道、不改任何提示词**。三处接线，全程对空 domain 回退（不改变现有行为）：

### 1. 作品仓库 domain 字段（`works_repo.py`）
manifest 顶层加 `domain` 字符串字段。`merge()`（line 70-77）已支持任意顶层 key，补便捷读写：`load_domain(platform, aweme_id) -> str | None` 与 `save_domain(platform, aweme_id, domain)`（save 时空串/None 不写，保持 manifest 干净）。

### 2. 作者 → 作品继承（`shenkuo.py`）
- `run()`（line 424-541）接收 `--author <sec_uid>`，需从订阅配置（`subscriptions.load_subscriptions`）查出该作者的 `domain`。
- 把 domain 透传给 `collect_one()`（line 98-103 加可选参数 `author_domain: str | None = None`）。
- `collect_one` 写 manifest 处（line 306-319 的 `works_repo.merge(...)`）带上 `domain`（仅当非空；空则不写，不注入 null）。
- 同一作品已有 domain 时不被无脑覆盖（继承是补全，不是抢占——已有非空 domain 保留）。

### 3. 作品 → 生产透传（引擎）
- `instance_runner.run_step`（line 229 `params = dict(step_inputs or {})`）：确认 instance 级 input `domain` 能到达**每个** performer 的 `params`。追踪 instance.inputs → 各 step 的 step_inputs 装配处，把 `domain` 纳入透传（让 `run_rw_step` 等能 `params.get("domain")`）。
- 本期 performer **只接收不消费**（读取在 task-2.4/2.5）。`run_rw_step` 等签名以 `**_: Any` 兜底，确认新增 `domain` 不报错。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `works_repo.load_domain/save_domain` 能读写作品 domain，空值不落盘（present-only）；works_repo_test.py 7 单测
- [x] #2 沈括采集把订阅作者的 domain 写进所采作品 manifest；无 domain 不写、已有不覆盖；test_shenkuo_collect 3 用例
- [x] #3 instance input `domain` 透传到 performer params（`_run_step` 注入，step 级可覆盖）；test_instance_engine 3 用例
- [x] #4 domain 缺失/未知时全链路行为与改造前一致（回退）
- [x] #5 pytest 全绿：533 collected / 533 passed（两路核对一致）
<!-- AC:END -->

## 验收（2026-06-17，主线程亲验，抓到 2 个真问题已修）

委派 sonnet 执行，主线程读 diff + 亲跑全量复核：

**接线点确认**：
- `works_repo.load_domain/save_domain`（present-only、None-safe）。
- 继承不覆盖：`collect_one` 写 manifest 前 `load_domain` 查已有、非空则跳过。
- 透传：`instance_runner._run_step`（持有 instance_id）读 `store.get_inputs(instance_id).get("domain")` 注入 `params`，step_inputs 显式 domain 优先；`_run_step` 是唯一派发点，覆盖所有 performer。
- 单条 adhoc 模式（shenkuo.py:474）不继承 domain——正确（无订阅作者，adhoc 走手动选标签 task-2.3）。

**主线程抓到并修复的 2 个缺陷**（subagent 自报绿但实际有问题）：
1. **默认部署继承静默失效**（真 bug）：subagent 的订阅查询只认 `NOF_STATE_DIR` 环境变量，本机/默认部署不设该 env（订阅文件实在 `repo_root/state/shenkuo/`），导致继承不触发；而 NOF_STATE_DIR-based 测试照样绿。主线程改为复用 `works_repo._state_root()` 做与 works_repo 一致的根解析（设了用其、没设回退 `repo_root/state`），与生产 `subscriptions_path(STATE_DIR)` 对齐。
2. **漏更新的测试桩**：`commands/shenkuo_test.py` 的 `fake_collect` 桩未加 `author_domain` 形参 → `run()` 新调用触发 TypeError 被吞、`seen` 空 → 该既有测试 FAIL。subagent 只跑了 `tests/server/` 漏跑 `commands/`。主线程补桩签名。

修复后全量 **533 passed**（collect 533 / passed 533 两路一致）。

## 边界

- 不改任何 prompt 构造逻辑（选题/写作/生图原样）。
- 不碰 `domain_profiles.py`（schema 扩展在 task-2.4）。
- "谁把 manifest 的 domain 塞进 instance inputs" 由前端在 task-2.3 做；本期只保证 input 一旦带 domain 就能透传到底。
