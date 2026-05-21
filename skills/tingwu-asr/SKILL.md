---
name: tingwu-asr
description: 使用阿里云通义听悟（DashScope/Paraformer）提取音视频文件中的语音文字。支持 MP4、MP3 等常见格式。
---

# 通义听悟 ASR 提取技能

## 使用方式

当用户请求提取音视频文件中的文字时，或者在下载完媒体文件后需要转写时，使用此技能。

### 示例命令

```
提取这个文件的文字：/path/to/media.mp4
```

或自动触发：
```
帮我下载这个抖音链接，并提取文字内容。
```

## 触发方式

- "提取文字"
- "语音转文字"
- "视频转文字"
- "通义听悟"
- "ASR"

## 配置要求

### 首次使用

需要在 `~/.openclaw/config.json` 中配置阿里云 DashScope API Key：

```json
{
    "dashscope_api_key": "您的密钥"
}
```

### 获取 API Key

1. 登录 [阿里云百炼 (ModelStudio)](https://bailian.console.aliyun.com/) 或 [DashScope 控制台](https://dashscope.console.aliyun.com/)。
2. 创建并获取 API Key。

## 脚本位置

- 旧实现：`scripts/tingwu_transcribe.py`
- 新实现：`scripts/tingwu_v2_transcribe.py`

说明：
- 聊天侧 `/asr` 主链路默认仍由 `skills/video-pipeline/scripts/video_pipeline.py` 驱动。
- 当前默认优先级为：`scripts/tingwu_v2_transcribe.py` -> 旧版 DashScope `audio.asr.Transcription` 实现 -> 本地 `whisper`。
- 当 `OPENCLAW_TINGWU_BACKEND=legacy` 或 `~/.openclaw/config.json` 中设置 `"tingwu_backend": "legacy"` 时，会先尝试旧版 DashScope 实现，再回退到 `tingwu_v2_transcribe.py` 和本地 `whisper`。
- 新脚本基于 `dashscope.multimodal.tingwu.tingwu.TingWu` 的离线任务接口，保留旧脚本以便回退和对照。

### 使用流程

1. 确认文件路径。
2. 调用旧脚本：`python3 scripts/tingwu_transcribe.py <文件路径>`
3. 调用新脚本：`python3 scripts/tingwu_v2_transcribe.py <文件路径>`
4. 新脚本默认输出单行 JSON 到 stdout，便于被 `video_pipeline.py` 解析；调试日志输出到 stderr。
