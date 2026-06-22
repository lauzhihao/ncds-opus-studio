---
id: task-3.6
title: 生产媒体管线去 emoji（ASCII 红线）
status: Done
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
- [x] #3 端到端实测一条采集任务，进度行解析正常、worker 不丢状态
- [x] #4 回归 608 passed
<!-- AC:END -->

## 完成记录

- 2026-06-22：完成配对梳理：`video_pipeline.py` 资产成功行 `下载/转写/清洗稿` 被 `video_job_worker.mjs` 解析为 `videoPath/transcriptPath/polishedTranscriptPath`；`pipeline_runner.py` 用转写进度行识别 ASR stage；worker 另用 warning/分片行推进 warning/transcribe 状态。
- 2026-06-22：生产媒体脚本 stdout 标记改为 ASCII：`[OK]`/`[FAIL]`/`[WARN]`/`[DL]`/`[ASR]`，并移除目标生产文件中的 emoji/特殊进度符号字面量。
- 2026-06-22：下游解析兼容新旧协议：worker 同时识别 `[OK]` 与旧成功前缀、`[WARN]` 与旧 warning 前缀、`[OK] n/m 片完成` 与旧分片前缀；`pipeline_runner.py` 同时识别 `[OK] 转写` 与旧转写前缀。
- 2026-06-22：早期离线合约回归：`pytest skills/video-pipeline/scripts/video_pipeline_test.py skills/video-pipeline/scripts/asr_service_test.py skills/douyin-downloader/scripts/douyin_download_test.py tests/server/test_pipeline_runner.py -q` 45 passed；`node --test scripts/video_job_worker.test.mjs` 38 passed；`py_compile` 4 个目标 Python 脚本通过；`git diff --check` 通过。
- 2026-06-22：补 `video_job_worker.mjs` 本地验证开关：`NOF_ASR_SKIP_HIGHLIGHT=1` 跳过爆款精华，`NOF_ASR_SKIP_ARTIFACT_UPLOAD=1` 跳过飞书产物上传；默认生产行为不变。
- 2026-06-22：完成真实 worker 同步冒烟：`task36_e2e_1782142270`，输入 `https://www.douyin.com/jingxuan?modal_id=7597329042169220398`，环境 `OPENCLAW_PYTHON=.venv/bin/python3`、`OPENCLAW_YT_DLP=.venv/bin/yt-dlp`、跳过 highlight/upload。结果 `job.json.state=completed`，产出 mp4/transcript/polished/rewrite；trace 中 `[OK] 下载:` 被解析成 `download_done`，`[OK] 转写:` 被解析成 `transcribe_done`，worker 未丢状态。
- 2026-06-22：补 `isEnvFlagEnabled` 单测；`node --test scripts/video_job_worker.test.mjs` 39 passed；媒体 Python 聚焦测试 58 passed。
- 2026-06-22：最终回归 `.venv/bin/python3 -m pytest -q` 608 passed；`.venv/bin/python3 -m pytest -W default -q` 608 passed；`node --test scripts/video_job_worker.test.mjs scripts/rewrite_command_runner.test.mjs` 45 passed；目标生产脚本 emoji 扫描无命中。
