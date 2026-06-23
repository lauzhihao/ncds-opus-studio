---
name: video-pipeline
description: 内部实现说明。底层多平台媒体下载 + 本地 ASR 转写能力。
---

# 媒体下载转写 Pipeline（内部）

这是底层实现说明。当前项目不包含聊天平台、文档平台或外部消息交付链路。

## 触发方式

本地命令入口：

```bash
python -m ncds_opus_factory asr --text "<包含媒体 URL 的文本>"
```

底层脚本：

```bash
python skills/video-pipeline/scripts/video_pipeline.py -o video-jobs/<job_id> <url>
```

支持的平台域名：
- `douyin.com` / `v.douyin.com` / `iesdouyin.com`
- `youtube.com` / `youtu.be`
- `bilibili.com` / `b23.tv`
- `xiaohongshu.com` / `xhslink.com`
- `lnns.co` / `listennotes.com` / `xiaoyuzhoufm.com` / `podcasts.apple.com` / `ximalaya.com`

## 输出结构

- `video-jobs/<job_id>/raw/` — 原始媒体、音频、转写文本
- `video-jobs/<job_id>/deliverables/` — 本地交付物、改写稿、汇总
- `video-jobs/<job_id>/deliverables/result.json` — pipeline 机器可读结果
