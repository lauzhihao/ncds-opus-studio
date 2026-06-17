---
id: task-2.5
title: 'P2：选题（软指导）+ 生图接入 + 情感领域补齐'
status: Done
assignee: []
created_date: '2026-06-17'
labels:
  - domain-tag
  - backend
  - content
dependencies:
  - task-2.4
parent_task_id: task-2
priority: medium
---

## Description

把映射扩到另两个阶段和另一个领域。

### 1. 选题接入（鬼谷子，`guiguzi.py`）——只作软背景
按 domain 注入 `topic_prompt`，但**只能当软背景**（受众画像 / 调性 / 合规禁区），**不得变成选题公式或固定赛道**——鬼谷子核心设计是"不预设赛道、不套死公式"（见 `guiguzi.py` 模块注释 + `_build_analyze_prompt`/`_build_topic_prompt_template`）。违背此原则即应回退。空/未知 domain → 原通用 prompt 不变。

### 2. 生图接入（吴道子，`storyboard_director.py`）
按 domain 把 `image_style` 注入 `build_director_prompt`（line 62）的 style_bible / palette / container_guide。空 domain → 用现有 `DEFAULT_SKETCH_STYLE_PREFIX`。

### 3. 情感领域补齐
用与财经同法（从优质情感作者的爆款反推，可借鬼谷子 `analyze` 沉淀初稿）填 `emotion` 的 topic/draft/image 三字段，人工精修后入表。情感调性：共情、代入、不说教。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 guiguzi 按 domain 注入软指导（措辞"仅供参考、不预设赛道"+ 用户自定义 prompt 时不注入）；空/未知回退；test_domain_profiles_storyboard_guiguzi + 调用方线程化测试覆盖
- [x] #2 storyboard 按 domain 注入 image_style（叠加不替换风格圣经）；空回退 `DEFAULT_SKETCH_STYLE_PREFIX`；有测试
- [x] #3 emotion topic/draft/image 三字段实质内容（从已订阅 3 个情感号爆款反推，含心理红线：不诊断/不制造焦虑/引导专业求助）；finance topic/image 一并补全
- [x] #4 提示词切换在单测层验证（finance/emotion 各取到对应 profile，空 domain 回退）；pytest 全绿 584 passed
<!-- AC:END -->

## 验收（2026-06-17，主线程亲验）

委派 2 个 sonnet 并行（X=接线、Y=内容，文件集不重叠），主线程读 diff + 跑全量复核：

**接线（X）**：
- 生图：`run_storyboard_step` 读 domain → `get_profile.image_style` → `build_director_prompt(domain_image_style=...)`，作"本片领域视觉调性（仅供参考，叠加在风格圣经之外）"，空回退。
- 选题：guiguzi 的 `analyze/generate_topics/run` + prompt builders 加 domain 支持；软背景措辞"仅供参考，不要把它当成固定赛道或选题公式"，且**用户自定义 prompt 时不注入**——守住鬼谷子"不预设赛道"设计哲学。

**内容（Y）**：emotion 三件套从 森林心理/心知说/喵的心理课 的爆款反推（精读 8 篇，提炼"让人认出自己/共情不说教/心理学命名赋能"调性 + 高赞评论"说的就是我"特征）；finance 补 topic_prompt/image_style（延续 xingmu，合规红线一致）。

**主线程补完 X 标记的"调用方线程化"缺口**：X 只做了 guiguzi 内部参数支持，调用方未传 domain（选题实际拿不到 domain）。主线程在 `pipeline_runner._run_guiguzi_analyze_bg` / `_run_guiguzi_generate_bg` 从 `state.inputs.get("domain")`（前端 doCreate 写入的 job input）透传给 guiguzi，纯后端、不动前端；补 2 个直测（finance 透传 / 无 domain 回退 None）。**至此 web/交互路径选题 domain 真正接通**。

**遗留（拆出 task-2.6）**：卧龙自动产线 `wolong_rounds.open_round` 的选题仍未带 domain（需 round 级 domain，涉及 rounds store 数据结构）——非本次交互主路径，单列跟进。
