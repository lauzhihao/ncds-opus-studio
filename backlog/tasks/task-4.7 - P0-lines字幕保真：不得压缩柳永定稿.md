---
id: task-4.7
title: P0 lines 字幕保真：不得压缩柳永定稿
status: To Do
assignee: []
created_date: '2026-06-23'
labels:
  - backend
  - pipeline
  - wudaozi
  - boya
  - quality
dependencies:
  - task-4.2
parent_task_id: task-4
priority: high
---

## Description

当前 `lines` 阶段把柳永定稿二次压缩成短字幕，导致吴道子、伯牙和最终成片没有覆盖完整稿件。

已观察到的真实样本：

- job: `36aacfec847d`
- 柳永选中稿：`video-jobs/36aacfec847d/02_rw/draft.md`
- 柳永稿非空白字符数：`2595`
- `episode.json` 50 句字幕非空白字符数：`1105`
- 字幕只保留约 `42.6%`，不是完整切分，而是摘要式改写。

根因在 `src/ncds_opus_factory/server/pipeline_lines_tasks.py` 的 prompt：当前要求全篇 `30-80` 条，并允许“改写、压缩、重组文章信息”。这会让 `lines` 从“切字幕”变成“再写一版短稿”。

目标：`lines` 只负责把柳永定稿切成可朗读字幕，不负责删减、摘要、重写内容。最终 `episode.beats[].zh` 拼接后的信息量和字数应与柳永定稿基本一致。

## Suggested Implementation

- 调整 `lines` prompt：移除固定 `30-80` 条上限，明确禁止删减、摘要、压缩、改写事实和删段。
- 优先考虑确定性切句/切字幕逻辑：先按段落、中文句号/问号/叹号、逗号等切分，再按每条目标字数拆长句；LLM 只做极少量格式化或 chapter 标注。
- 如果仍使用 LLM 结构化，必须增加后置校验：比较 `draft.md` 与 `beats[].zh` 拼接文本的有效字符覆盖率，低于阈值直接失败并切换备用策略。
- `beats` 数量应由稿件长度自然决定。以当前 2595 字柳永稿为例，按每句约 18-26 字，预期约 100-140 条，而不是固定压到 50 条。
- 保留 `storyboard` / `image` / `tts` 读取 `episode.json` 的既有契约，不额外引入多套台词数据结构。

## Edge Cases

- 标点、空白、Markdown 段落标题不应被当成丢稿；校验应按“中文/英文/数字有效字符”或归一化文本比较。
- 柳永稿里有英文、数字、百分比、金额时，切分不能吞掉关键数字。
- 超长句需要拆成多条字幕，但拆分后顺序必须与原文一致。
- 用户在伯牙面板编辑字幕后，不应再被自动保真校验覆盖；保真校验只约束 `lines` 生成阶段。
- 旧 job 不要求强兼容，但重新执行 `lines` 后应按新规则产出完整字幕。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `lines` 生成的 `episode.beats[].zh` 拼接后，有效字符覆盖率相对柳永 `02_rw/draft.md` 不低于 `95%`
- [ ] #2 `lines` 不再使用“全篇 30-80 条”作为硬约束；字幕条数随稿件长度自然增长
- [ ] #3 `lines` prompt 或实现明确禁止摘要、删减、压缩、重写柳永定稿
- [ ] #4 当前样本 `36aacfec847d` 重新跑 `lines` 后，字幕总字数与柳永稿基本对齐，不再只剩约 42.6%
- [ ] #5 吴道子 `storyboard`、画面资产和伯牙 `tts` 继续消费同一份 `02_rw/episode.json`，不新增分叉数据通道
<!-- AC:END -->
