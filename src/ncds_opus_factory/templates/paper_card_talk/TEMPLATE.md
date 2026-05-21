# paper-card-talk 视频模板

> 1920×1080 中文口播短片：顶部品牌栏 + 中央纸质卡片 + 底部双语字幕，暖纸 + 红墨水风格。
> 单一数据源驱动（`beats.js`）+ AI 大模型管线（CosyVoice TTS + gpt-image-2 + headless Chrome 录制）。

## 复制成新一集（10 分钟工作流）

新一集（如 010-xxx）的标准做法是把本模板复制到 `~/ncds-materials/` 仓库下，按命名规则改名：

```bash
# 1. 从 ncds-opus-factory 拉模板到 ncds-materials
TEMPLATE_DIR=~/projects/ncds-opus-factory/src/ncds_opus_factory/templates/paper_card_talk
NEW_SLUG="010-your-slug"
cp -r "$TEMPLATE_DIR" ~/ncds-materials/.${NEW_SLUG}-assets
cp "$TEMPLATE_DIR/example-entry.html" ~/ncds-materials/${NEW_SLUG}.html

# 2. 改 HTML 里的资源路径（template 里默认路径是 009 风格）
sed -i '' "s/009-paper-card-talk-assets/${NEW_SLUG}-assets/g" ~/ncds-materials/${NEW_SLUG}.html

# 3. 清空旧产物 + 模板自带文档
cd ~/ncds-materials/.${NEW_SLUG}-assets
rm -rf audio pictures output example-narration.txt example-entry.html TEMPLATE.md
mv template.json ${NEW_SLUG}.json
mv README.md ../README-${NEW_SLUG}-notes.md 2>/dev/null  # 保留为参考，或删掉

# 4. 改 beats.js — 替换 BEATS 和 SCENES（保留章节卡 ch1-5 结构）
$EDITOR beats.js

# 5. 跑 TTS
python3 tts_gen.py

# 6. 跑文生图（需 codeproxy.dev 可达 + $GPT_IMAGE2_API_KEY 在 ~/.zshrc）
python3 pic_gen.py

# 7. 本地预览
cd ~/ncds-materials && python3 -m http.server 8765
# 浏览器打开 http://127.0.0.1:8765/${NEW_SLUG}.html

# 8. 渲染成片
cd ~/ncds-materials && node .${NEW_SLUG}-assets/render.mjs
# → output/${NEW_SLUG}.mp4

# 9. 提交 + 部署
git -C ~/ncds-materials add -A && git -C ~/ncds-materials commit -m "${NEW_SLUG}: ..."
git -C ~/ncds-materials push
ssh root@ncds.cc 'deploy-ncds-cc'
```

## 数据模型

详见同目录 `README.md`（来自 009 实现的完整解剖文档），那是模板设计的金本。

## 工具链文件

| 文件 | 角色 | 改频率 |
|---|---|---|
| `beats.js` | 数据源（BEATS 时间线 + SCENES 场景定义） | 每集都改 |
| `player.js` | 运行时（音频驱动播放、字幕推进、章节卡渲染） | 一次写好，长期不动 |
| `overlays.js` | overlay 渲染引擎（8 风格 × 6 动效） | 加新动效时改 |
| `image-slot.js` | 通用图片填充组件（仓库共用） | 不动 |
| `styles.css` | 视觉系统（暖纸底 + 红墨水 + Noto Serif/Sans SC） | 改全片风格时 |
| `tts_gen.py` | TTS 批量（DashScope CosyVoice） | 换音色时改默认 |
| `pic_gen.py` | 文生图批量（本机 gpt-image-2 网关） | 不动 |
| `render.mjs` | 离线渲染（headless Chrome + ffmpeg → MP4） | 改帧率 / 编码时 |
| `tweaks.jsx` / `tweaks-panel.jsx` | 编辑器面板（仅开发期辅助） | 不动 |

## 与 ncds-opus-factory 主项目的关系

本模板是 `paper-card-talk` 视频形态的金本。`pic_gen.py` 引用的 gpt-image-2 网关
（`../../../../gpt_image/gpt_image_gen.py`）和 `tts_gen.py` 引用的 DashScope CosyVoice
都已搬到 ncds-opus-factory 主项目里。换言之：模板只负责"视频形态的工具链"，
"图片 / 音频生成能力"由主项目托管。

模板被复制到 `~/ncds-materials/` 后，需要的依赖（Python: Pillow / dashscope；
Node: puppeteer-core / puppeteer-screen-recorder）安装在素材仓库里，
或通过 PYTHONPATH 引用主项目。

## 待优化

- `pic_gen.py` 当前硬编码 `~/.codex/skills/gpt-image/scripts/gpt_image_gen.py` 路径；
  应改为优先 `from ncds_opus_factory.commands.wst import IMAGE_GATEWAY`（或环境变量 override）。
- `tts_gen.py` 的 DashScope 调用逻辑后续可抽到 `ncds_opus_factory.common.tts`，模板只保留批处理逻辑。
