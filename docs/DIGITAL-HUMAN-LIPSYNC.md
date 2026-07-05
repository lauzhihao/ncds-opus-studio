# 数字人口型链路

当前可用路线：预制数字人底片 + TTS 音频 + MuseTalk v1.5 + DWPose landmark + 模板/HyperFrames 合成。

## 链路结论

1. 数字人不每次重生成完整人物。每条作品只换 script 对应的 TTS 音频，再用 MuseTalk 重绘嘴部。
2. 底片应是有轻微头部、身体、表情变化的 silent presenter video，长度不足时 MuseTalk 会正反循环复用帧。
3. DWPose 用来生成更精细的 face landmark。没有 DWPose 时会退回 face detector bbox，能跑但裁切和贴回质量不够稳。
4. HyperFrames 或本项目模板负责最终合成：数字人大多作为角落小窗，少量全身/半身镜头切大画面。

## 远端环境

当前 POC 机器：

- SSH: `root@100.83.163.41`
- Workdir: `/root/lipsync-poc`
- MuseTalk repo: `/root/lipsync-poc/repos/MuseTalk`
- Python: `/root/lipsync-poc/envs/musetalk310/bin/python`
- GPU: RTX 5060
- 已修复：full `mmcv 2.0.1` CUDA ops、DWPose 权重、PyTorch `weights_only` 兼容。

验证标志：

```text
mmcv._ext ok
preprocessing model TopdownPoseEstimator
Total frame:「600」 Manually adjust range : [ -12~11 ] , the current value: 0
```

如果日志出现 `using face detector bbox fallback`，说明 DWPose 没有生效。

## 标准工具

入口：

```bash
python3 scripts/digital_human_lipsync.py --input job.json
```

也支持 stdin：

```bash
cat job.json | python3 scripts/digital_human_lipsync.py
```

dry-run：

```bash
python3 scripts/digital_human_lipsync.py --input job.json --dry-run
```

## 输入 JSON

最小 remote-input 示例：

```json
{
  "job_id": "finance-host-001",
  "source_video": {
    "path": "/root/lipsync-poc/inputs/heygen_source_24s_silent.mp4",
    "location": "remote"
  },
  "speech_audio": {
    "path": "/root/lipsync-poc/inputs/musetalk_new_script_probe.wav",
    "location": "remote"
  },
  "outputs": {
    "local_dir": "outputs/local-lipsync-poc",
    "name": "finance-host-001.mp4"
  }
}
```

local audio/video 会自动上传到远端 job 目录：

```json
{
  "job_id": "local-audio-demo",
  "source_video": {
    "path": "/root/lipsync-poc/inputs/heygen_source_24s_silent.mp4",
    "location": "remote"
  },
  "speech_audio": {
    "path": "outputs/audio/new_script.wav",
    "location": "local"
  },
  "outputs": {
    "local_dir": "outputs/local-lipsync-poc",
    "name": "local-audio-demo.mp4"
  },
  "musetalk": {
    "batch_size": 4,
    "fps": 25,
    "use_float16": true,
    "use_saved_coord": false,
    "strict_dwpose": true
  },
  "artifacts": {
    "contact_sheet": true,
    "keyframes": true,
    "compare_with_local": "outputs/local-lipsync-poc/old_version.mp4"
  }
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `job_id` | 否 | 输出目录、远端 job 目录的稳定 ID；会规范成 `A-Za-z0-9._-`。 |
| `source_video.path` | 是 | 数字人底片，建议 silent presenter video。 |
| `source_video.location` | 否 | `remote` 或 `local`；不填时本地存在则按 local，否则按 remote。 |
| `speech_audio.path` | 是 | TTS 音频。 |
| `outputs.local_dir` | 否 | 本地输出目录。 |
| `outputs.name` | 否 | 输出 MP4 文件名。 |
| `outputs.remote_result_dir` | 否 | MuseTalk 远端结果目录。 |
| `musetalk.use_saved_coord` | 否 | 固定底片二次生产可设 true，复用 landmark 缓存提速。 |
| `musetalk.strict_dwpose` | 否 | 默认 true；日志发现 fallback 时任务失败。 |

## 输出 JSON

工具 stdout 和 metadata 文件都会输出同一类 JSON：

```json
{
  "ok": true,
  "job_id": "finance-host-001",
  "inputs": {
    "source_video": "/root/lipsync-poc/inputs/heygen_source_24s_silent.mp4",
    "speech_audio": "/root/lipsync-poc/inputs/musetalk_new_script_probe.wav"
  },
  "outputs": {
    "remote_video": "/root/lipsync-poc/outputs/finance-host-001/v15/finance-host-001.mp4",
    "local_video": "outputs/local-lipsync-poc/finance-host-001.mp4",
    "metadata": "outputs/local-lipsync-poc/finance-host-001.metadata.json",
    "log": "outputs/local-lipsync-poc/finance-host-001.log",
    "contact_sheet": "outputs/local-lipsync-poc/finance-host-001_contact_sheet_1fps.jpg",
    "keyframes": "outputs/local-lipsync-poc/finance-host-001_keyframes.jpg"
  },
  "warnings": [],
  "duration_seconds": 156.29
}
```

## 性能基线

最近一次 RTX 5060 / 28.188s / 704 frames / DWPose 的结果：

- end-to-end: `156.29s`
- DWPose landmark: 约 `12fps`
- MuseTalk UNet: 约 `26fps`
- paste/composite: 约 `20fps`

更高显卡主要提升速度、并发和 batch size，不会自动提升模型画质。画质更依赖底片质量、DWPose 是否生效、TTS、合成模板和人工挑选。
