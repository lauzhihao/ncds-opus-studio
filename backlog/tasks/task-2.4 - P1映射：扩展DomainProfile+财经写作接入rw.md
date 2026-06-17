---
id: task-2.4
title: 'P1 映射：扩展 DomainProfile + 财经写作（吸收 xingmu）接入 rw'
status: Done
assignee: []
created_date: '2026-06-17'
labels:
  - domain-tag
  - backend
  - content
dependencies:
  - task-2.2
parent_task_id: task-2
priority: high
---

## Description

让标签真正起作用的第一个端到端闭环：**财经 × 写作**。

### 1. 扩展 DomainProfile（`domain_profiles.py`）
`DomainProfile` 从 `topic_prompt/draft_prompt` 扩成三字段（均可选，None 回退；`get_profile` 回退契约不变）：
```python
class DomainProfile(TypedDict):
    topic_prompt: str | None   # 鬼谷子/选题：角度、受众、禁区、调性（task-2.5 用）
    draft_prompt: str | None   # 柳永/写作：口播稿风格、结构、用词、开头钩子
    image_style: str | None    # 吴道子/生图：符号系统、调色板、画面调性（task-2.5 用）
```

### 2. 填 finance.draft_prompt（吸收 xingmu）
吸收 skill `anthropic-skills:xingmu-douyin-copy`（星沐财经｜零距离看懂财经 抖音爆款 SOP）的写作规范，沉淀成 finance 的 `draft_prompt`（实质内容，非占位）。注意合规红线（不荐股、不预测点位等）。

### 3. 接入 rw 写作
- 现状：`run_rw_step` 用 `profile`（默认 `DEFAULT_RW_PROFILE="douyin_cog"`），调 `_build_rw_prompt(profile, source_text)`（`pipeline_runner.py:3151`）。已有体裁 profile：`caijing`(财经)/`jitang`(鸡汤)/`douyin_cog`… 与 domain 正交。
- 接法（决策）：`_build_rw_prompt` 增加可选 `domain_guidance: str | None` 参数；rw step 用 `params.get("domain")` → `get_profile(domain)` 取 `draft_prompt` 作为 domain_guidance，**叠加在体裁 profile 之上**（体裁底座不变）。domain 空 / draft_prompt 空 → 与今天完全一致。
- 不要把 domain 内容散到两处：domain 专属写作要求只放 `domain_profiles`，体裁 profile（caijing 等）保持"结构性"职责。若实现中发现 caijing 与 finance.draft_prompt 重复严重，在交付说明里指出、由主线程裁决，不擅自删 caijing。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `DomainProfile` 扩成 topic/draft/image 三字段，`get_profile` None 回退不变，test_domain_profiles_rw 覆盖
- [x] #2 `finance.draft_prompt` 实质内容来自 xingmu SOP（钩子公式/10步结构/节奏/语言标准/合规红线），非 None
- [x] #3 `_build_rw_prompt` 加 `domain_guidance`；run_rw_step 读 domain→get_profile→draft_prompt 注入；双向验证
- [x] #4 pytest 全绿：552 collected / 552 passed（两路一致）
<!-- AC:END -->

## 验收（2026-06-17，主线程亲验）

委派 sonnet，主线程读 diff 复核端到端接线：
- 链路确认：`run_rw_step` 读 `kwargs.get("domain")` → `get_profile(domain).draft_prompt` → 作 `domain_guidance` 传 `_build_rw_prompt`；guidance 插在「体裁 profile 底座」与「通用约束」之间，空 domain → None → 行为与改造前完全一致（test_domain_profiles_rw 16 用例双向验证）。
- finance.draft_prompt 内容主线程已审：合规红线齐全（不荐股/不预测点位/不承诺收益/数据需信源），调性=男性受众·国际猎奇视角，质量可用（owner 可再微调）。
- caijing（体裁）与 finance（domain）职责正交，subagent 建议不删 caijing——采纳。
- 注：subagent 自报"新增 21 测试"失真，实为 16；以主线程全量 552 为准（测试只增不删核实）。
