# 010 Reading Confidence · 实现笔记

> 一个 1920×1080 的中文口播短片模板：顶部品牌栏、中央纸质卡片、底部双语字幕、暖纸 + 红墨水的设计语言。100 句台词、29 个场景（含 5 个章节卡）、24 张 AI 插图。本文档是 010 的实现解剖，模板派生自 009 paper-card-talk，可作为后续素材（011、012…）的复制模板。

## 一句话总览

**单一数据源驱动 + AI 大模型管线。**

`beats.js` 是唯一真理：BEATS 数组定义时间线（每条 = 一句字幕 + 对应场景），SCENES 字典定义每个场景的图、文字 overlay、动效。其它一切都是它的产物：

- **音频**：cosyvoice 批量 TTS，按 BEATS 顺序生成 `audio/NNNN.mp3`
- **插图**：gpt-image-2 按 SCENES.prompt 批量生成 `pictures/NN-<id>.webp`
- **运行时**：`player.js` 把这些拼起来；音频驱动字幕推进，image-slot 加载图片，overlays.js 把 SCENES.overlays 渲染成飞入/印章/手写体浮动文字
- **成片**：`render.mjs` headless Chrome + ffmpeg 一键出 `output/*.mp4`

改文案 = 改一个数组。换音色 = 改一个环境变量。换风格 = 改一个 prompt 字段。

---

## 数据模型

### BEATS — 时间线

```js
window.BEATS = [
  { zh: "真正危险的，不是不想上班", en: "The real danger isn't…", scene: "hook" },
  { zh: "一、先把「不上班」说清楚",   en: "1. Let's define…",     scene: "ch1", chapter: 1 },
  // ...
];
```

- `zh` / `en`：底部字幕双语
- `scene`：引用 SCENES 里的 key（连续相同 = 同一张图持续）
- `chapter`（可选）：1-5，触发章节封面卡的特殊版式

### SCENES — 场景定义

```js
window.SCENES = {
  "all-eggs": {
    prompt: "扁平插画。画面中下部摆着一个棕色编织扁篮，篮内并排横向放着四枚椭圆形米白色鸡蛋…",
    overlays: [
      { text: "工资",   xPct: 28, yPct: 52, style: "os-tag-pill", delay: 0   },
      { text: "安全感", xPct: 43, yPct: 52, style: "os-tag-pill", delay: 150 },
      { text: "未来",   xPct: 58, yPct: 52, style: "os-tag-pill", delay: 300 },
      { text: "身份",   xPct: 73, yPct: 52, style: "os-tag-pill", delay: 450 },
    ],
  },
  // chapter scenes 走 player.js 里的 CSS 渲染，prompt 可有可无
  ch1: { prompt: "章节封面卡（可选背景图）", label: "" },
};
```

- `prompt`：给 gpt-image-2 的中文 prompt。**关键写法**：明确"主体在哪/留白在哪"，让 overlay 位置对应到图里"空着"的区域，避免覆盖。所有需要画在图里的中文都用 overlay 替代，prompt 里强调"标签内部完全空白"。
- `overlays[]`：浮动文字层，详见下面 overlay 系统
- `label`：兼容字段，目前未使用，可忽略

### overlays — 浮动文字 + 动效

```js
{
  text: "工牌 = 靠山？",   // 必填
  xPct: 78, yPct: 26,    // 中心点位置（相对 scene 0-100）
  style: "os-callout-red", // 字体字号风格 (os-*)，省略走 auto
  animation: "oa-fly-right", // 入场动效 (oa-*)，省略走 auto
  delay: 0,              // 入场延迟 ms，省略按 index 阶梯（i × 180）
  size: 56,              // 可选，强制覆盖字号 px
  rotate: -3,            // 可选，整体旋转 deg
}
```

**字体字号库** (`os-*`，定义在 styles.css)：

| 类名 | 长相 | 适合 |
|---|---|---|
| `os-tag-pill` | 白底圆角徽章 + 黑墨衬线 | 多个并列短标签（鸡蛋、骨牌） |
| `os-stamp` | 红框红字 + 微旋转 | "稳定?" 反讽印章 |
| `os-marker` | 黑字 + 黄色 highlighter | 划重点 |
| `os-handwrite` | 衬线斜体 + 倾斜 | 年轮上的手写注 |
| `os-typewriter` | 等宽字 + 浅黄便利贴 | 引文 |
| `os-callout` | 大号衬线 + 细下划线 | 主标题 |
| `os-callout-red` | 同上但红墨水 | 警示标题 |
| `os-circle-mark` | 红色 marker 椭圆圈出 | 圈关键词 |

**入场动效库** (`oa-*`)：

`oa-fly-top / -bottom / -left / -right`（四向飞入）· `oa-fade`（渐入）· `oa-zoom`（缩放 0.6→1）· `oa-stamp-hit`（缩放回弹，"啪"地盖章感）· `oa-blur`（模糊+位移渐入）

**位置坐标系统**：scene 是 1640×740 卡片区域。image-slot 用 `fit=contain` 把 3:2 的 AI 图缩到正中，所以**图实际占 scene 的水平 20%–80%**（左右各 20% 是米黄留白）。overlay 想压在图上的元素 → xPct 应该在 20-80 之间；想"飘"在图外的 → 60-80 或 20-40。

---

## 工具链

```
.010-reading-confidence-assets/
├── beats.js                ← 数据：BEATS + SCENES（唯一真理）
├── player.js               ← 运行时：音频驱动播放
├── overlays.js             ← overlay 渲染引擎 + 8 风格 × 8 动效
├── image-slot.js           ← 通用图片填充组件（仓库共用）
├── styles.css              ← 视觉系统：暖纸底 + 红墨水 + Noto Serif/Sans SC
├── tweaks.jsx              ← 编辑器 Tweaks 面板（仅开发期）
├── tts_gen.py              ← TTS 批量生成（DashScope CosyVoice）
├── pic_gen.py              ← 插图批量生成（远程 ncds gpt-image-2）
├── render.mjs              ← 离线渲染（headless Chrome + ffmpeg → MP4）
├── 010-reading-confidence.json← 素材元信息
├── 010-narration.txt       ← 朗读稿明文（任何 TTS 引擎都能再用）
├── audio/                  ← 100 × NNNN.mp3
├── pictures/               ← 24 × NN-<scene-id>.webp
└── output/                 ← MP4 成片（gitignored，render.mjs 落地点）
```

### TTS · `tts_gen.py`

```bash
python3 .010-reading-confidence-assets/tts_gen.py            # 用默认 longtian_v3 跑全片
VOICE=longshuo_v3 RATE=1.0 python3 ... --force               # 换音色 + 重生
```

- 模型：`cosyvoice-v3-flash`（plus / v3.5-plus 当前账号未开通，返回 418）
- 默认音色 `longtian_v3`（磁性理智男 · 咨询调），rate=1.1
- 依赖 `$DASHSCOPE_API_KEY`
- 幂等：目标 mp3 存在则跳过
- 自带重试（4 次指数退避）+ 句间 250ms 节流避免 rate-limit
- 100 段约 3 分钟跑完

### 文生图 · `pic_gen.py`

```bash
python3 .010-reading-confidence-assets/pic_gen.py            # 跑全部缺失
python3 .010-reading-confidence-assets/pic_gen.py hook       # 只重做某几个
python3 .010-reading-confidence-assets/pic_gen.py --force    # 全部重生
```

- 模型：OpenAI gpt-image-2，本机调 `~/.codex/skills/gpt-image/scripts/gpt_image_gen.py`
- 路径：`python3 gpt_image_gen.py --size 1536x1024 --prompt ...`（依赖 `$GPT_IMAGE2_BASE_URL` / `$GPT_IMAGE2_API_KEY`，已在 `~/.zshrc` 导出）
- 落地：本机生成 PNG 到 `/tmp/gpt-image/010-NN-<id>/` → Pillow 转 1536×1024 WebP（quality 85）→ `pictures/NN-<id>.webp`
- 跳过 `ch*` 章节场景（player.js 走 CSS chapter-card）
- prompt 自动追加 `NO_TEXT_SUFFIX`：强制画面不出现任何文字
- 24 张约 12-14 分钟（gpt-image-2 单张 ~30-60s，串行调用避免限流）
- 网关：`https://codeproxy.dev/v1`（偶尔 502 → 重跑漏的）

### overlay 渲染 · `overlays.js` + `styles.css`

无配置即用。`player.js` 在 scene 激活时调 `window.__overlays.renderInto(sceneEl, def.overlays)`，把 SCENES[id].overlays 实时渲染成 div，CSS keyframe 跑入场动效。`style: "auto"` 会用 sceneId+index 确定性哈希挑一个，复用时视觉稳定。

### 离线渲染 · `render.mjs`

```bash
node .010-reading-confidence-assets/render.mjs
```

- headless Chrome 1920×1080，CDP screencast 30fps 抓帧
- 音视频对齐用 `scripted` 模式：player.js 在录制时用 `setTimeout(audio.duration)` 推进而不是 `audio.onended`，避免静音 audio.onended 抖动累积漂移
- 录前 page.evaluate 预设 `body.classList.add('recording')` 隐藏控件、清空字幕，再开 recorder
- ffmpeg concat 音轨：300ms 前导静音（覆盖空白纸面 intro）→ 100 mp3 + 80ms 间隙 → 1500ms 尾部淡出静音
- 最终 mux：h264 + aac 160k → `output/010-reading-confidence.mp4`
- 整段 ~5 分钟（视频实时录 3-4 分钟 + ffmpeg 后处理 30s）
- 依赖：仓库根 `npm install puppeteer-core puppeteer-screen-recorder`（已 gitignore `node_modules/`）+ 系统 ffmpeg

---

## 复制成新一集 · 10 分钟工作流

假设要做 011：

```bash
# 1. 复制模板
cp 010-reading-confidence.html 011-{your-slug}.html
cp -r .010-reading-confidence-assets .011-{your-slug}-assets

# 2. 改 HTML 里的资源路径
sed -i 's/010-reading-confidence-assets/011-{your-slug}-assets/g' 011-{your-slug}.html

# 3. 清空旧产物
rm -rf .011-{your-slug}-assets/audio .011-{your-slug}-assets/pictures
rm -rf .011-{your-slug}-assets/output

# 4. 改 beats.js — 替换 BEATS 和 SCENES（保留章节卡 ch1-5 结构）
#    每条 BEAT ≤ 16 字幕，长度 6-30 字最佳，配 scene id
#    每个 SCENES 项写 prompt（明确留白位置）和 overlays（如需文字）
$EDITOR .011-{your-slug}-assets/beats.js

# 5. 跑 TTS
python3 .011-{your-slug}-assets/tts_gen.py
# 验证：随便听一段
mpv .011-{your-slug}-assets/audio/0001.mp3

# 6. 跑文生图（需 codeproxy.dev 网关健康；env 在 ~/.zshrc）
python3 .011-{your-slug}-assets/pic_gen.py

# 7. 本地预览微调
python3 -m http.server 8765
# 打开 http://127.0.0.1:8765/011-{your-slug}.html
# 边播边看 overlay 位置；不到位的回 beats.js 改 xPct/yPct，刷新就生效

# 8. 渲染
node .011-{your-slug}-assets/render.mjs
# → output/011-{your-slug}.mp4

# 9. 提交 + 部署
git add -A && git commit -m "011: {description}"
git push
ssh root@ncds.cc 'deploy-ncds-cc'
```

---

## 命名 & 部署约定（仓库级，详见根 CLAUDE.md）

- 入口：`NNN-{slug}.html` 在仓库根
- 资源目录：`.NNN-{slug}-assets/` —— **前面有一个英文点**，让 nginx autoindex 跳过
- HTML 里所有 `<link>` / `<script>` / 数据 fetch 都用 dot 前缀
- 部署：`ssh root@ncds.cc 'deploy-ncds-cc'`
- nginx vhost 需保留 `location ~ /\.(?!well-known/|\d) { deny all; }` —— 这条放行 `.数字开头` 路径但仍拦 `.git` / `.env`

---

## 已知坑

| 现象 | 原因 / 修法 |
|---|---|
| cosyvoice 返回 418 | `cosyvoice-v3-plus` / `v3.5-plus` 当前账号未开通，用 `cosyvoice-v3-flash` |
| codeproxy.dev 502 | gpt-image-2 网关偶发，pic_gen.py 不会自动重试，手动 `pic_gen.py <scene-id>` 重补 |
| AI 图主体被裁掉头 | 别用 `fit=cover`，本模板已锁 `fit=contain`；prompt 也要说"画面四边大面积留白" |
| overlay 飘在米黄边外 | image-slot contain 后图只占 scene 的水平 20%-80%，xPct 越界就落在米黄留白上 |
| 录制成片左下角看到控件 | render.mjs 必须在 recorder.start 之前 page.evaluate 注入 `body.recording` 类 |
| 渲染音频比字幕慢 | 必须用 scripted 模式（setTimeout 替代 audio.onended），否则静音 Chrome 抖动累积 |
| MP4 永远不要 commit | 已加入 `.gitignore`（`output/`），每次内容变动 render 一次就好 |
| package.json 必须存在 | render.mjs 用 ESM import，需要根目录 `package.json` 含 `"type": "module"` |

---

## 成本 / 时间参考

| 项 | 数量 | 单价（约） | 单集总成本 |
|---|---|---|---|
| CosyVoice TTS | 139 段 ≈ 2200 字 | ¥0.05/万字（v3-flash） | **< ¥0.01** |
| gpt-image-2 | 32 张 1536×1024 | $0.04-0.10/张 | **$1.3-3.2** |
| 渲染算力（本机） | ~7 分钟 / 集 | — | 电费 |
| **合计** | | | **< $5 / 集** |

时间分布：beats.js 编辑 + 调 overlay 位置是主要人力（~1-2h），其它都是自动化。

---

## 文件清单速查（增量更新版本）

| 文件 | 角色 | 何时编辑 |
|---|---|---|
| `beats.js` | 数据源 | 每次新内容 |
| `player.js` | 运行时 | 一次写好，长期不动 |
| `overlays.js` | overlay 引擎 | 加新动效 / 字体风格时 |
| `styles.css` | 视觉系统 | 改全片风格时 |
| `tts_gen.py` | TTS 批量 | 换音色时改默认值 |
| `pic_gen.py` | 文生图批量 | 一次写好，长期不动 |
| `render.mjs` | 离线渲染 | 改帧率 / 编码参数时 |
| `audio/*.mp3` | 自动生成 | 不手编 |
| `pictures/*.webp` | 自动生成 | 不手编 |
| `output/*.mp4` | 自动生成 | gitignored，每次重渲 |
| `010-narration.txt` | 朗读稿明文 | 给剪映 / 其它 TTS 兜底用，可选 |
