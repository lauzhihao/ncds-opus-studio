# 自定义字体目录

把 .woff2 文件放到这里（如 `chapter.woff2`、`handwrite.woff2`），
然后在 `episode.json` 的 `fonts[]` 数组里声明：

```json
"fonts": [
  {
    "family": "MyChapterFont",
    "src": "fonts/chapter.woff2",
    "weight": 900,
    "style": "normal",
    "format": "woff2",
    "display": "swap"
  }
]
```

声明后，bootstrap.js 会自动生成 `@font-face` 注入 head。
在 `scenes[id].style.numFont` / `subtitleFont` / 或 `overlays[*].style.font`
里用 `family` 名引用即可。
