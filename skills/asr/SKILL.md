---
name: asr
description: 本地媒体下载与 ASR 转写入口。
user-invocable: false
---

# ASR 本地入口

本项目没有聊天平台或文档平台交付链路。需要从媒体链接转写时，使用本地命令：

```bash
python -m ncds_opus_factory asr --text "<包含媒体 URL 的文本>"
```

底层执行 `skills/video-pipeline/scripts/video_pipeline.py`，产物落在 `video-jobs/<job_id>/`。
