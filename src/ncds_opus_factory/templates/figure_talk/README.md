# figure_talk — 吴道子剪影成片模板

实心黑剪影成片模板：浅色纸纹舞台 + **居中实心剪影（Ken Burns 缓动）** + 关键词图标飞入 + 字幕。
对标号「PPT 录屏风格」的另一种实现，和 `stickman`（svg-rig 骨骼火柴人）平级 —— 这一套走
**剪影素材库选用**（不画骨骼），是当前主线渲染层。

## 真源关系

- **本目录 = 模板 / 真源**（版本化）。
- 一次具体成片 = 吴道子（`nof wudaozi`）产出的 `state/figure_jobs/<id>/` 实例（含 `figures/` `audio/` `output/` 等产物，gitignored）。
- 关系同 `stickman` 模板 ↔ 某一集。

## 架构（数据驱动，吴道子只输出 id / 路径）

```
index.html     舞台 + 引脚本
assets/styles.css  16:9 版式(title/tag/stage/subtitle,cqw 分辨率无关;复刻 stickman 版式)
player.js      逐句切换:剪影 <img> + Ken Burns(WAAPI) + 图标飞入 + 字幕 + 配音
render.mjs     无头 Chromium 录屏出片(照搬 stickman/render_stick.mjs,改端口/输出名)
tts_gen.py     CosyVoice longtian_v3 配音(读 beats.js 的 zh,只认双引号)
beats.js       本集分镜(吴道子写):[{ zh, figure?, icons?:[], motion?, title?, tag?, kind? }]
icons.js       图标库(54 个);由 wudaozi 从 stickman 复制进实例
figures/       本集选中的剪影(由 wudaozi 从 figure_lib 复制进来)
```

## 怎么跑（在 wudaozi 产出的实例目录里）

```bash
cd state/figure_jobs/<id>
ln -sf /tmp/node_modules node_modules          # ESM 依赖桥接(puppeteer 等)
export DASHSCOPE_API_KEY=<主仓库 .env 的 key>   # worktree 无 .env
python3 tts_gen.py                              # 生成 audio/NNNN.mp3
node render.mjs                                 # 出片到 output/figure_talk.mp4
```

## motion(Ken Burns 类型)

`zoom-in` / `zoom-out` / `pan-left` / `pan-right` / `still`。吴道子按 beat 的 kind 选，player 用 WAAPI 驱动。
