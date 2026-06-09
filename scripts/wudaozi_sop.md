# 吴道子 — 抖音认知成片 · 美术 / 视觉总监（分镜 + 选图）

> 画圣吴道子，吴带当风。把柳永的**文字口播稿**分镜成**可渲染的剪影成片**——
> 一句台词一个画面：居中实心剪影 + Ken Burns 缓动 + 关键词图标飞入 + 字幕。挂工厂北极星：完播 + 涨粉。

吴道子是 5-agent 工厂的第四位（卧龙 CEO · 鬼谷子选题 · 柳永编剧 · **吴道子美术** · 伯牙声音）。
实现是命令 `commands/wudaozi.py`（`nof wudaozi`），不是 skill。**不生成、不下载素材**，只从本地剪影库
（`assets/figure_lib/`）「按语义选用」。设计与伯牙（声音）完全对仗。

## 职责（每块都带判断）

1. **分镜 · 切句**：用 codex（gpt-5.5，走 scodex shim）把口播稿切成逐句分镜，并概括每句的
   `concept / keywords / emphasis / kind`。这是唯一的 LLM 步骤（导演判断）。
2. **选图 · 选用**：**规则打分**从剪影库选每句的主体剪影（`select_figure`：keywords 交集 + scene 命中 + concept 子串），
   从图标库（`templates/stickman/icons.js`，54 个）选 0-2 个强调图标（`select_icons`）。
   规则版、可解释、库扩了自动覆盖——和伯牙 `select_bgm`/`plan_sfx` 一样"接口稳定，后续可换 LLM 驱动"。
3. **动效 · 排镜**：按 beat 类型选 Ken Burns（`pick_motion`：hook/reveal→zoom-in、golden→still、close→zoom-out、body→左右交替平移）。
4. **质检 · 把关**：
   - **硬闸门（不丢句/不改词）**：beat 的 `zh` 拼起来去标点后必须 ≈ 原稿（`check_coverage`，difflib ratio ≥ 0.95），
     不过线就把 codex 打回**重切一次**。防止模型漏句、改写柳永的台词。
   - **软标注**：句数 / 单句过长 / 无主体剪影占比（`soft_checks`），校准期仅提示不打回（和柳永/伯牙质检对仗）。

## 输入 / 产物契约

- 输入：柳永成稿（纯文字口播 `.md`，`--script`；或 `--text` 直接传文本）。首行短标题会被剥出当 title 提示、不计入正文。
- 库契约：`assets/figure_lib/README.md`（剪影按 `keywords/scene/concept` 结构化）。
- 产物：一个可直接渲染的 **figure_talk 实例** `state/figure_jobs/<job_id>/`：
  - `beats.js` + `beats.json`（`[{zh, figure?, icons?, motion, title?, tag?, kind?}]`；伯牙也读 `beats.json`）
  - `storyboard.json`（每 beat 选了啥 / 为啥，对仗伯牙 `audio_plan.json`）
  - `beats.qc.json`（不丢句校验 + 软标注）
  - 复制好的 `index.html/player.js/render.mjs/tts_gen.py/assets/styles.css/icons.js` + `figures/`（选中的剪影）

## 跑法

```bash
# 0) 无真素材时,先造占位剪影库打通管线
python3 scripts/scan_figure_lib.py --placeholders

# 1) 把一篇柳永稿分镜成可渲染实例
PYTHONPATH=src python3 -m ncds_opus_factory wudaozi --script video-jobs/OGV_xxx/deliverables/rewrite/douyin_cog-gpt5.md

# 2) 进实例目录出片(配音 + 录屏)
cd state/figure_jobs/<id>
ln -sf /tmp/node_modules node_modules
export DASHSCOPE_API_KEY=<主仓库 .env 的 key>
python3 tts_gen.py && node render.mjs        # -> output/figure_talk.mp4
```

## 后续（骨架之外）

- 选用升级 LLM 驱动：`select_figure`/`select_icons` 现在是规则版，接口稳定，可换 codex/opus「读语义曲线选图」。
- 接伯牙：实例产 `master.mp3` 后改 render 吃整条音床（替代 tts_gen 纯拼人声）。
- 串进卧龙编排：选题→脚本→分镜→成片，全链路无人值守。
- 真素材：往 `figure_lib/figures/` 填实心黑剪影 + 补标签（占位库只验链路）。
