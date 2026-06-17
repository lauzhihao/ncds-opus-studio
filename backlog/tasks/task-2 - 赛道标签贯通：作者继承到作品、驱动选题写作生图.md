---
id: task-2
title: 赛道标签贯通：作者继承到作品、驱动选题写作生图
status: In Progress
assignee: []
created_date: '2026-06-17'
labels:
  - domain-tag
dependencies: []
priority: high
---

## Description

赛道标签（finance/emotion）当前"骨架在、血管断、映射空"：定义（web `config/domains.ts` + server `domain_profiles.py`）与作者打标签（`subscriptions.json` 的 `author["domain"]`）已就位，但：

1. 标签流不到作品——沈括采集（`collect_one`）与临时作品解析都不写 domain；
2. 流不到生产各阶段——选题（鬼谷子）/写作（柳永）/生图（吴道子）既不传也不读；
3. `domain_profiles.py` 映射字段全 `None`（占位）。

本任务把标签从作者继承到作品、再贯通到三个生产阶段，并填充映射。足球（football）确认弃用、一并删除。

### 核心架构决策（实现一律遵此，不再重新讨论）

- **作品仓库 manifest（`works_repo`）是 domain 的唯一落脚点**：顶层 `domain` 字段。作者继承与手动选标签都写它，生产阶段都读它。
- **生产实际走引擎路径**（`NOF_ENGINE_NODES` 默认全节点走引擎）：domain 作为 `POST /instances` 的 instance input 进来，经 `instance_runner.run_step` 透传到每个 performer 的 `**params`，performer 用 `params.get("domain")` 读。
- **全程对空/未知 domain 回退**：行为与改造前完全一致（`get_profile()` 已有 None 回退契约），不破坏现有链路。
- **`domain_profiles` 是 domain 专属内容的权威来源**；现有 rw 体裁 profile（`caijing`/`jitang`/`douyin_cog`…）是正交的"体裁结构"底座，domain 内容叠加其上，不重复造。

## 子任务

- task-2.1 删除足球赛道标签占位
- task-2.2 P0 管道·后端：作品 manifest 落 domain + 沈括继承 + 生产实例透传
- task-2.3 P0 管道·前端：临时作品解析时单选赛道、生产时把 domain 送进 instance inputs
- task-2.4 P1 映射：扩展 DomainProfile + 财经写作（吸收 xingmu）接入 rw
- task-2.5 P2：选题（软指导）+ 生图接入 + 情感领域补齐

## 验收主线（主线程负责）

每个子任务委派 subagent 执行后，主线程独立复核：读改动代码 + 亲自跑 pytest（collect-only 与 passed 两路核对，不信单次输出/不信 subagent 自报，见 env 工具输出偶发损坏的纪律）。

## 进度（2026-06-17）

- ✅ task-2.1 删足球（tsc/build 净、零残留）
- ✅ task-2.2 后端管道（works_repo domain + 沈括继承 + 引擎透传；主线程修了"默认部署继承静默失效"+ 漏更新测试桩 2 个真缺陷）
- ✅ task-2.3 前端选标签 + 写回 + 生产送 domain（tsc + vite build 净）
- ✅ task-2.4 财经写作映射（DomainProfile 扩 3 字段；finance.draft_prompt 吸收 xingmu；rw 端到端接线）
- ✅ task-2.5 选题软指导 + 生图接入 + 情感领域内容（X 接线 + Y 反推情感内容；主线程补完鬼谷子调用方线程化）
- ⏳ task-2.6（新拆，遗留跟进）卧龙自动产线 rounds 路径选题带 domain

原计划 2.1–2.5 全部完成。全量基线 520 → 现 **588 passed**（两路核对一致），无回归。

### 后续调整（2026-06-17，owner 拍板）

`DomainProfile` 从扁平三字段（topic_prompt/draft_prompt/image_style）升级为 **per-agent 槽位**：`guiguzi`/`liuyong`/`wudaozi`/`cover`/`boya`（domain 贯穿整条线、每 agent 各有要求）。封面从片内生图拆出成独立 `cover` 槽位（含 CTR≤15% 取向）；`boya`(配音) 建槽待填。消费端选题/写作/生图同步改读新键；`cover`/`boya` 内容/槽位就位但**尚无消费端**（无封面/配音生成步），待后续接线。存储仍内联 Python（owner 选内联非外置文件）。

三阶段 domain 均已端到端接通：选题(鬼谷子,软背景)/写作(柳永,draft_prompt)/生图(吴道子,image_style)；
继承链：作者(订阅 domain)→沈括采集写作品 manifest→生产实例 inputs→引擎透传 performer；
临时作品手动选标签(前端单选+PATCH 写回)。全程空/未知 domain 回退原行为。
