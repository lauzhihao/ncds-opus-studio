# 本地剪影库（吴道子的素材库）

吴道子（`nof wudaozi`）**不生成、不下载**人物剪影——它只从这个库里「按语义选用」。
素材文件由你（库主）维护，版权与商用授权也由你负责；本目录只把素材**结构化**成吴道子能选的清单。
设计与伯牙的音源库（`assets/audio_lib/`）完全对仗。

## 风格基准

实心黑色剪影 pictogram（flaticon 风格）：整体填充、可带白色挖空表现细节（嘴/手机/手臂分界），
场景化具象语义（如「人坐桌前低头看手机」= 分心/沉迷）。透明背景最佳（png）；矢量（svg）亦可。

## 目录结构

```
assets/figure_lib/
  figures/   人物剪影  *.png / *.jpg / *.svg / *.webp
  library.json   清单（吴道子读它来选；用扫库工具生成骨架）
```

> 实际图片文件（figures/ 下的 *.png 等）已 gitignore，不入库。入 git 的只有本 README、结构与 `library.json`。

## 维护流程

1. 把剪影丢进 `figures/`。
2. 跑扫库工具自动补尺寸、生成骨架：
   ```bash
   python3 scripts/scan_figure_lib.py
   ```
3. 打开 `library.json`，给每条补标签（工具不会覆盖你已填的）：
   - **keywords**：语义关键词（吴道子按句子语义匹配的核心），如 `["手机","分心","低头","沉迷"]`
   - **scene**：赛道，如 `["认知","职场"]`
   - **concept**：一句话描述（给人看 / 兜底匹配），如 `低头看手机`

## 吴道子怎么选

吴道子用 codex 把脚本切句、概括每句的语义关键词，再用规则按 `keywords` 交集 + `scene` 命中**打分选**
最贴的剪影（一句一个主体）。强调点缀的小图标走另一层（`templates/stickman/icons.js`，54 个），不在本库。
选中的剪影会被**复制**进成片实例的 `figures/` 目录，相对路径引用。

## 没有真素材时先打通管线

```bash
python3 scripts/scan_figure_lib.py --placeholders   # 合成几张占位黑剪影 + 自动建 manifest
```

之后 `nof wudaozi --script <柳永稿.md>` 就能端到端产出可渲染实例。
占位剪影只为验证链路，**正式出片请换成真素材**。
