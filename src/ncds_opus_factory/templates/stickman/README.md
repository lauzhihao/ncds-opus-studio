# stickman — 吴道子 svg-rig 小人成片模板

黑剪影简笔画小人成片模板，对标号的「PPT 录屏风格」：空白纸纹舞台 + 会动的黑色简笔画小人
+（待补）情绪图标 / 道具 + 关键词字幕。和 `paper_card_talk` 平级，是吴道子（美术 / 视觉）的渲染层。

## 真源关系

- **本目录 = 模板 / 真源**（版本化）。
- `state/stickman_poc/` 是一次具体 PoC 运行（含 `audio/` `output/` `node_modules/` 等产物，gitignored）。
- 关系同 `paper_card_talk` ↔ 某一集：模板可复用，实例在 `state/` 下生成。

## 架构（数据驱动，Agent 只输出 id）

```
rig-spec.js   骨骼契约（唯一真源）：一套 VIEWBOX + BONES + 旋转锚点
  ├ characters.js  5 角色：standing / present / thinking / cheer / wushu
  ├ motions.js     13 动作模板（WAAPI 轨道，按 bone 命中，可叠加 / 调幅）
  └ sequences.js   动作序列编排
beats.js        scene 契约：{ character, motion:[...], zh, title?, tag? }
player_stick.js 浏览器播放器（逐句切换 + 字幕 + 配音）
render_stick.mjs 无头 Chromium 录屏出片
tts_gen.py      CosyVoice longtian_v3 配音（读 beats.js 的 zh，只认双引号）
```

## 怎么跑

```bash
cd src/ncds_opus_factory/templates/stickman
ln -sf /tmp/node_modules node_modules          # ESM 依赖桥接（puppeteer 等）
export DASHSCOPE_API_KEY=<主仓库 .env 的 key>   # worktree 无 .env
python3 tts_gen.py                              # 生成 audio/NNNN.mp3（4 位命名）
node render_stick.mjs                           # 出片到 output/
```

## 自由图标层（icons.js）

独立于角色、漂浮在舞台、为强调关键词飞入的图标 / 道具（对标号画面的另一半）。与 `motions.js`
平级、同样数据驱动：`ICONS[id] = { name, cat, keywords, enter, svg }`，Agent 只输出 id。
注意 `rig-spec.js` 的 `prop` / `bubble` 骨头是「角色手持 / 头顶、跟着人走」的，不是这层。

- **来源**：Tabler Icons（MIT 线性圆角）批量「染黑」生成，统一 viewBox 24 + currentColor + 粗线圆角，
  与小黑人同源。当前 54 个，覆盖 时间/能量/趋势/认知/判断/目标/价值/沟通/突破/工具。
- **扩 / 改图标**：编辑 `build_icons.mjs` 的 MANIFEST（加 tabler 名 + 中文 name/cat/keywords/enter）
  → `node build_icons.mjs` 重跑覆盖 `icons.js`。**勿手改 `icons.js`（AUTO-GENERATED）**。
  不直接 npm 依赖 xicons/@vicons：那是 Vue/React 组件库 + 混集风格杂，和无框架录屏管线不搭。
- **预览**：浏览器开 `icons-preview.html` 看全部图标 + 点击重播入场动效。

待接入：beats 加 `icons` 字段 + player 加 icon 渲染通道 + 吴道子 agent 按 keywords 自动选图。
