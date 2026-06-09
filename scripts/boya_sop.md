# 伯牙 — 抖音认知成片 · 声音总监（配音 / 配乐 / 音效）

> 俞伯牙，高山流水遇知音。以声写意、**为「知音」（目标观众）而奏**——配音配乐都服务于一件事：
> 让那个会共鸣、会看完、会关注的人留下来。挂工厂北极星：完播 + 涨粉。

伯牙是 5-agent 工厂的第五位（卧龙 CEO · 鬼谷子选题 · 柳永编剧 · 吴道子美术 · **伯牙声音**）。
实现是命令 `commands/boya.py`（`nof boya`），不是 skill。**不生成、不下载素材**，只从本地库
（`assets/audio_lib/`）「选 + 排 + 混 + 判」。

## 三块职责（每块都带判断）

1. **配音 TTS · 辨音**：缺人声时复用 `commands/tts.py` 的 `run()` 合成（伯牙定音色/语速）；
   做听感质检（语速字/秒、总时长越界），校准期只标注不打回（和柳永的 AI 味质检对仗）。
   引擎走 `common/tts_provider.py` 的 provider 抽象（Strategy/Adapter/Factory）——
   DashScope/CosyVoice 细节藏在 `CosyVoiceProvider` 里；升级音色 = 加新 provider，不动调用方。
   切引擎:`--tts-provider <name>` 或 env `NOF_TTS_PROVIDER`。
2. **配乐 BGM · 审乐**：按内容情绪/赛道从库里选一条，循环铺满全长、压低到 -18~-20dB 做 ducking、
   首尾淡入淡出。情绪匹配是判断点。
3. **音效 SFX · 点睛**：按 beat 类型（hook/golden/reveal/transition/close）在时间戳上落音效。
   时间线镜像 `render_stick.mjs`（INTRO 0.3s + clip + 句间 GAP 0.08s），保证 cue 和画面对齐。

## 输入 / 产物契约

- 输入 job 目录：`beats.json` 或 `beats.js`（beat 元数据/文案）；`audio/NNNN.mp3` 缺则伯牙按 `zh` 自动合成。
- 库契约：`assets/audio_lib/README.md`。
- 产物：`master.mp3`（混好的整条音床）+ `audio_plan.json`（选了啥 / 为啥 / 排在哪 / 听感质检）。
- 下游：render 层直接用 `master.mp3` mux（替代原 buildAudioTrack 的纯拼人声）。

## 跑法

```bash
# 0) 无真素材时,先造占位库打通管线
python3 scripts/scan_audio_lib.py --placeholders

# 1) 给一个成片 job 配声音
PYTHONPATH=src python3 -m ncds_opus_factory boya --job <video-job-dir> [--scene 认知] [--mood 沉静]
```

## 后续（骨架之外）

- LLM 驱动选择：`select_bgm` / `plan_sfx` 现在是规则版，接口稳定，可换 opus/codex 来"读脚本情绪曲线选乐排 cue"。
- 真 sidechain ducking（人声起伏自动压 BGM），替换当前静态 -18dB。
- 接进 render：改 `render_stick.mjs` 的 `buildAudioTrack` 吃 `audio_plan.json` / 直接用 `master.mp3`。
- 串进卧龙编排：成稿→成片→伯牙配音，全链路无人值守。
- 听感质检升级：opus 他评（机器味/节奏/情绪贴合），类比 `quality_rubric.py`。
