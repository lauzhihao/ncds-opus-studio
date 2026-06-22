---
id: task-3.6
title: 生产媒体管线去 emoji（ASCII 红线）
status: In Progress
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
- [x] #1 梳理 emoji 前缀 → 下游匹配的全部配对（emit 点 ↔ worker.mjs/pipeline_runner 解析点）
- [x] #2 emoji 统一换 ASCII 标记（[OK]/[FAIL]/[WARN]/[DL] 等），emit 与解析同一次改完
- [ ] #3 端到端实测一条采集任务，进度行解析正常、worker 不丢状态
- [x] #4 回归 590 passed
<!-- AC:END -->

## 完成记录

- 2026-06-22：完成配对梳理：`video_pipeline.py` 资产成功行 `下载/转写/清洗稿` 被 `video_job_worker.mjs` 解析为 `videoPath/transcriptPath/polishedTranscriptPath`；`pipeline_runner.py` 用转写进度行识别 ASR stage；worker 另用 warning/分片行推进 warning/transcribe 状态。
- 2026-06-22：生产媒体脚本 stdout 标记改为 ASCII：`[OK]`/`[FAIL]`/`[WARN]`/`[DL]`/`[ASR]`，并移除目标生产文件中的 emoji/特殊进度符号字面量。
- 2026-06-22：下游解析兼容新旧协议：worker 同时识别 `[OK]` 与旧成功前缀、`[WARN]` 与旧 warning 前缀、`[OK] n/m 片完成` 与旧分片前缀；`pipeline_runner.py` 同时识别 `[OK] 转写` 与旧转写前缀。
- 验证：`pytest skills/video-pipeline/scripts/video_pipeline_test.py skills/video-pipeline/scripts/asr_service_test.py skills/douyin-downloader/scripts/douyin_download_test.py tests/server/test_pipeline_runner.py -q` 45 passed；`node --test scripts/video_job_worker.test.mjs` 38 passed；`py_compile` 4 个目标 Python 脚本通过；`git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 590 passed, 173 warnings。
- 待验证：真实外部端到端采集尚未跑；该链路会触发真实下载/TikHub/yt-dlp 与 ASR backend，依赖外部网络、凭证和目标媒体可用性。本次先以 stdout 合约单测、stage 解析单测和全量回归作为替代验证，AC#3 保持未完成。
