---
id: task-3.6
title: 生产媒体管线去 emoji（ASCII 红线）
status: To Do
assignee: []
created_date: '2026-06-19 13:46'
labels:
  - convention
  - red-line
  - logging
dependencies: []
parent_task_id: task-3
priority: medium
---

## Description

违反 CLAUDE.md §1 Encoding「控制台日志只用 ASCII，生产代码无 emoji（launchd/journald 收集易乱码）」。
这些脚本是 launchd 托管生产热路径（`video_job_worker.mjs:54` spawn、`pipeline_runner.py:2514` 转发）：

- `skills/video-pipeline/scripts/video_pipeline.py` — 22 处 emoji
- `skills/video-pipeline/scripts/asr_service.py` — 7 处
- `skills/tingwu-asr/scripts/tingwu_transcribe.py` — 17 处
- `skills/douyin-downloader/scripts/douyin_download.py:176` — `print("✅ 下载完成…")`（emit 源头）

**陷阱**：下游 `video_job_worker.mjs:830-895` 用 emoji 前缀做协议匹配（`text.startsWith('✅ 下载:')`、
`pipeline_runner.py:2505` 的 `re.search(r"✅\s*转写…")`），把违规固化进进程间协议。所以**改 emit 必须同步
改下游解析**，否则断采集进度。这是跨文件契约，不是单纯文案替换。

## 关联 Findings
S1。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 梳理 emoji 前缀 → 下游匹配的全部配对（emit 点 ↔ worker.mjs/pipeline_runner 解析点）
- [ ] #2 emoji 统一换 ASCII 标记（[OK]/[FAIL]/[WARN]/[DL] 等），emit 与解析同一次改完
- [ ] #3 端到端实测一条采集任务，进度行解析正常、worker 不丢状态
- [ ] #4 回归 586 passed
<!-- AC:END -->
