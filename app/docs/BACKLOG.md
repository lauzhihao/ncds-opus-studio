# Backlog / 待办

本项目暂缓但已规划的功能。前端已就绪、等后端/外部依赖的条目记在这里,避免遗忘。

---

## 1. 安卓语音打回:服务端短语音 ASR 端点(`/asr`)

**状态**:前端已实现并上线;**等后端 `ncds-opus-studio` 实现 `/asr` 端点**(后端正在做大重构,稍后再加)。

### 背景
打回意见走语音口述(对齐 iOS `RejectVoiceOverlay`)。
- **iOS**:端上 `SFSpeechRecognizer`(zh-CN),正常,无需后端。
- **安卓**:`speech_to_text` 套系统 `SpeechRecognizer`,但国产去 Google 化 ROM(小米等)把系统听写服务阉割/区域停用,运行时报"不再提供听写服务"。
- 方案:安卓(或任何端上识别不可用时)**录音 → 上传服务端 → 阿里云「一句话识别」转写 → 回文字**。

### 前端(已完成)
- 依赖:`record`(录音 WAV)、`path_provider`(临时文件)。
- `lib/features/detail/reject_voice_sheet.dart`:双模式。端上识别 `initialize()` 失败或 `onError` → 切 `_serverMode`,改为录音 WAV(16kHz/16-bit/单声道)→ 调 `FactoryClient.transcribe(bytes)` 上传。
- `lib/core/net/factory_client.dart` → `transcribe(List<int> wavBytes)`:`POST /asr` multipart。
- 端到端目前断在这里:`/asr` 还没实现,安卓上停录后会回"转写失败"。

### 后端契约(待实现)
```
POST {factory_base}/asr            # 与其它端点同 base(dev: http://liuzhihao-mbp.local:8810)
Content-Type: multipart/form-data
  audio = <WAV 字节, 16kHz / 16-bit / 单声道 PCM>

成功 200:  { "text": "识别出的文字" }
失败:      非 2xx,body 可带 { "error": "..." }

服务端逻辑:收 WAV → 调阿里云「一句话识别」(同步,≤60s,中文 zh-CN)→ 返回 text。
```

注意:后端原有的听悟调用是给**沈括转写抖音视频**用的(异步、长音频文件),与这个**客户端短语音**端点不是同一个能力,需新增。

### 验收
- 安卓上点麦克风口述一句话 → 停录 → 几秒内识别文字出现在结果区 → 可加标签 → 确认打回。
- iOS 行为不变(仍走端上识别)。
