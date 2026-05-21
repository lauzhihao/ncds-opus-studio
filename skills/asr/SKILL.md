---
name: asr
description: 显式 `/asr <url>` 转写入口，由 media-command-router 插件命令处理。
user-invocable: false
---

# ASR Command Entrypoint

`/asr` 现在由 `media-command-router` 插件以 plugin command 方式注册和处理，
不再通过 skill command-dispatch 路由。

对 agent 来说，处理 `/asr` 或裸媒体链接时，优先动作是调用插件 tool：优先调用 `asr` tool；如果不存在，再调用 `media_command_router` 并传 `commandName="asr"`。
如果当前会话里 `asr` 与 `media_command_router` 都不可用，允许使用一个很窄的 fallback：
直接 `exec` `node scripts/asr_command_runner.mjs '<json-payload>'`，把请求送入同一条 worker 链路。
fallback payload 至少要包含 `jobId`、`inputs`、`chatId`、`senderOpenId`、`channel`、`provider`、`messageId`、`chatType`。
禁止把 `/asr ...` 当 shell 命令执行，
禁止直接运行 `video_job_worker.mjs` 或 `video_pipeline.py`。

聊天里如果用户直接发媒体链接、抖音分享口令或包含媒体 URL 的自然语言，
也必须等价处理成 `/asr <提取到的URL>`，并通过插件 tool 发送完整上下文（`chatId`、`senderOpenId`、`channel`、`provider`、`messageId`、`chatType`）。
如果进入 fallback，也要透传同样的上下文，不要自行总结转写结果，不要输出“当前会话里没有可用的 `asr` / `media_command_router` 插件工具”这类拒绝文案。
