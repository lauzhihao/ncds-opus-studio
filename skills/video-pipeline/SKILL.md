---
name: video-pipeline
description: 内部实现说明。底层多平台媒体下载+转写能力，由 worker 间接调用。
---

# 媒体下载转写 Pipeline（内部）

这是底层实现说明，不是聊天前台入口。
聊天中收到媒体链接、抖音分享文本或 `/asr` 类请求时，agent 不应直接选择本 skill，而应优先调用 `asr` tool；若不可用，再调用 `media_command_router`。只有当前会话里 `asr` 与 `media_command_router` 都不可用时，才允许退化为直接 `exec` `node scripts/asr_command_runner.mjs '<json-payload>'`。

## 触发方式

聊天中收到媒体链接时，统一走 `/asr <url>` 入口。agent 必须优先调用 `asr` tool；如果不可用，再调用 `media_command_router`；只有当前会话里 `asr` 与 `media_command_router` 都不可用时，才允许直接执行 `node scripts/asr_command_runner.mjs '<json-payload>'`；随后进入 `asr_command_runner.mjs` → `video_job_worker.mjs` → 底层 pipeline。

统一只走 `/asr <url>` 这条入口；插件 tool（优先 `asr`，其次 `media_command_router`）仍然是聊天侧首选入口；只有当前会话里 `asr` 与 `media_command_router` 都不可用时，才允许直接执行 `node scripts/asr_command_runner.mjs` 作为窄 fallback；这里描述的是底层处理能力，不是 agent 的前台可选技能。

禁止在聊天会话中直接执行 `video_pipeline.py` 或手动创建 job 目录。
不要在 agent 会话里手动创建 job 目录。
不要在 agent 会话里直接 `exec /asr ...`。
不要在 agent 会话里常规直接执行 `node scripts/asr_command_runner.mjs ...`；只有当前会话里 `asr` 与 `media_command_router` 都不可用时，才允许这样做。
不要在 chat-triggered handling 里直接执行 `python3 skills/video-pipeline/scripts/video_pipeline.py`。
一旦 `asr_command_runner.mjs` 返回 `status=started`、`jobId`，或确认 `job.json` 已进入 `running` / `starting`，立即停止额外脚本调用。
启动成功后不得再次手动调用 `video_job_worker.mjs`。
启动成功后不得再直接调用 `video_pipeline.py`，只能等待 worker 推送、或轮询 `video-jobs/<job_id>/job.json` 查看状态。
启动成功后不要自己读取转写文本并直接总结成交付结果，除非 worker 已完成且结果文件已稳定落盘。

## 自动触发（必须遵守）

> **规则：用户消息中只要包含以下任一平台的 URL，无论消息中有多少其他文字，立即执行 `/asr <提取到的URL>`，不要回复文字、不要询问确认。**

支持的平台域名：
- `douyin.com` / `v.douyin.com` / `iesdouyin.com`
- `youtube.com` / `youtu.be`
- `bilibili.com` / `b23.tv`
- `xiaohongshu.com` / `xhslink.com`
- `lnns.co` / `listennotes.com` / `xiaoyuzhoufm.com` / `podcasts.apple.com` / `ximalaya.com`

不触发：用户明确要求分析错误、排查问题、讨论结果时。

## 抖音分享口令（重要）

用户从抖音 App 复制分享的文本通常是这样的：

```
5.12 复制打开抖音，看看【用户名的作品】标题描述... [视频标题](https://v.douyin.com/EI2DPWqkuhM/) T@y.TL oQK:/ 11/16
```

**处理方式：从文本中提取 `https://v.douyin.com/...` 链接，立即调用 `asr` tool；若不可用，再调用 `media_command_router`；只有当前会话里 `asr` 与 `media_command_router` 都不可用时，才允许直接执行 `node scripts/asr_command_runner.mjs '<json-payload>'`，按 `/asr <链接>` 进入统一 worker 链路。** 不要回复"收到"或询问用户意图，不要生成自由文本启动确认；如果 fallback 成功启动，也只能返回统一任务启动消息，不能回复“当前会话里没有可用插件工具”。

## 输出结构

- `video-jobs/<job_id>/raw/` — 原始媒体、音频、转写文本
- `video-jobs/<job_id>/deliverables/` — 交付物、改写稿、汇总
- `video-jobs/<job_id>/job.json` — 任务状态

## 运行后行为约束

- 允许：查看 `job.json`、等待 worker、在 worker 完成后基于结果做总结
- 不允许：在 worker 启动成功后再次手动跑任何 pipeline / worker / 原始转写脚本
