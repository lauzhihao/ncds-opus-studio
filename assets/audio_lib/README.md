# 本地音源库（伯牙的素材库）

伯牙（`nof boya`）**不生成、不下载**音乐与音效——它只从这个库里「选 + 排 + 混」。
素材文件由你（库主）维护，版权与商用授权也由你负责；本目录只把素材**结构化**成伯牙能选的清单。

## 目录结构

```
assets/audio_lib/
  bgm/   背景音乐  *.mp3 / *.wav
  sfx/   关键音效  *.mp3 / *.wav（短:钩子/金句/反转/转场/收尾）
  library.json   清单（伯牙读它来选；用扫库工具生成骨架）
```

> 实际音频文件（bgm/ sfx/ 下的 *.mp3 等）已 gitignore，不入库。入 git 的只有本 README、结构与 `library.json`。

## 维护流程

1. 把音乐丢进 `bgm/`、音效丢进 `sfx/`。
2. 跑扫库工具自动补时长、生成骨架：
   ```bash
   python3 scripts/scan_audio_lib.py
   ```
3. 打开 `library.json`，给每条补标签（工具不会覆盖你已填的）：
   - **BGM**：`mood`（情绪，如 沉静/紧张）、`energy`（1-5）、`tempo`（slow/mid/fast）、`scene`（赛道，如 认知/职场）、`loopable`
   - **SFX**：`cue`（触发场景，见下）、`gain_db`（相对音量）

## SFX 的 cue 取值（伯牙按 beat 类型来匹配）

| cue | 落在哪 |
|---|---|
| `hook` | 开场冒犯硬钩（第 1 句） |
| `golden` | 金句 |
| `reveal` | 反转 / 反常识断言 |
| `transition` | 句间转场 |
| `close` | 收尾赋能（最后一句） |

beat 的类型来自成片 job 的 `beats.json`（字段 `kind`，或单 beat 直接写 `sfx:"golden"`）；
没标就由伯牙按位置兜底（首=hook、尾=close、含"其实/真相/反而"等词=reveal）。

## 没有真素材时先打通管线

```bash
python3 scripts/scan_audio_lib.py --placeholders   # 合成几条占位正弦音 + 自动建 manifest
```

之后 `nof boya --job <成片目录>` 就能端到端产出 `master.mp3` + `audio_plan.json`。
占位音只为验证链路，**正式出片请换成真素材**。
