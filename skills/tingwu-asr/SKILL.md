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

- 唯一实现：`src/ncds_opus_factory/common/capabilities/tingwu.py`
- CLI wrapper：`scripts/tingwu_v2_transcribe.py`
- 旧命令名 wrapper：`scripts/tingwu_transcribe.py`

说明：
- 聊天侧 `/asr` 主链路默认仍由 `skills/video-pipeline/scripts/video_pipeline.py` 驱动。
- 听悟 API 调用只保留 `capabilities/tingwu.py` 一处：本地文件上传为临时 URL、提交离线任务、轮询、提取原始文本。
- `whisper` fallback 属于 ASR capability 内部降级策略，不属于听悟 vendor adapter。
- 听写稿清洗属于 ASR 后处理，不在 vendor adapter 中实现。

### 使用流程

1. 确认文件路径。
2. 调用 wrapper：`python3 scripts/tingwu_v2_transcribe.py <文件路径>`
3. wrapper 默认输出单行 JSON 到 stdout；调试日志输出到 stderr。
