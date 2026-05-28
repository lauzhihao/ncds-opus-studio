# 仓库共享字体目录

所有素材共用一份字体。新素材**不要**在 `.<slug>-assets/fonts/` 里再放一份，直接引用根目录的 `/fonts/...`。

## 现存字体

共 34 款 woff2 (总 112529 KB ≈ 109 MB)。

| family 名 | 路径 | 来源 (ziti666 id / 名称) | 大小 |
|---|---|---|---|
| `Fengsao` | `/fonts/fengsao/Regular.woff2` | ziti666 id 32 · 风骚体 | 3563 KB |
| `Huangyou` | `/fonts/huangyou/Regular.woff2` | ziti666 id 88 · 黄油体 | 2424 KB |
| `Huanxi Shouzha` | `/fonts/huanxi-shouzha/Regular.woff2` | ziti666 id 34 · 欢喜手扎体 | 4761 KB |
| `Mao Zedong` | `/fonts/mao-zedong/Regular.woff2` | 毛泽东书法字体 | 1056 KB |
| `PP Fangfang` | `/fonts/pp-fangfang/Regular.woff2` | ziti666 id 18 · 屁屁方方体 | 1526 KB |
| `PP Katong` | `/fonts/pp-katong/Regular.woff2` | ziti666 id 19 · 屁屁卡通体 | 6390 KB |
| `PP Keai` | `/fonts/pp-keai/Regular.woff2` | ziti666 id 20 · 屁屁可爱体 | 5311 KB |
| `PP Naila` | `/fonts/pp-naila/Regular.woff2` | ziti666 id 21 · 屁屁奶酪体 | 7282 KB |
| `PP Riji` | `/fonts/pp-riji/Regular.woff2` | ziti666 id 22 · 屁屁日记体 | 5474 KB |
| `PP Shouxie` | `/fonts/pp-shouxie/Regular.woff2` | ziti666 id 23 · 屁屁手写体 | 5356 KB |
| `PP Xiaogou` | `/fonts/pp-xiaogou/Regular.woff2` | ziti666 id 24 · 屁屁小狗体 | 5357 KB |
| `XK Naila` | `/fonts/xk-naila/Regular.woff2` | ziti666 id 16 · 小可奶酪体 | 4004 KB |
| `XY Kaiti` | `/fonts/xy-kaiti/Regular.woff2` | ziti666 id 30 · 行韵楷体 | 3884 KB |
| `ZQK Dundun` | `/fonts/zqk-dundun/Regular.woff2` | ziti666 id 10 · 郑庆科墩墩体 | 987 KB |
| `ZQK Guaiguai` | `/fonts/zqk-guaiguai/Regular.woff2` | ziti666 id 08 · 郑庆科乖乖体 | 611 KB |
| `ZQK Jingsu` | `/fonts/zqk-jingsu/Regular.woff2` | ziti666 id 13 · 郑庆科竞速体 | 536 KB |
| `ZQK Jingya` | `/fonts/zqk-jingya/Regular.woff2` | ziti666 id 01 · 郑庆科静雅体 | 1166 KB |
| `ZQK Keai` | `/fonts/zqk-keai/Regular.woff2` | ziti666 id 11 · 郑庆科可爱体 | 1579 KB |
| `ZQK Lengku` | `/fonts/zqk-lengku/Regular.woff2` | ziti666 id 06 · 郑庆科冷酷体 | 629 KB |
| `ZQK Naihei` | `/fonts/zqk-naihei/Regular.woff2` | ziti666 id 17 · 郑庆科奈黑体 | 1283 KB |
| `ZQK Nainai` | `/fonts/zqk-nainai/Regular.woff2` | ziti666 id 14 · 郑庆科奈奈圆 | 1596 KB |
| `ZQK Qiaoke` | `/fonts/zqk-qiaoke/Regular.woff2` | ziti666 id 07 · 郑庆科巧克体 | 2445 KB |
| `ZQK Shouhui` | `/fonts/zqk-shouhui/Regular.woff2` | ziti666 id 05 · 郑庆科手绘宋 | 3730 KB |
| `ZQK Shuaihei` | `/fonts/zqk-shuaihei/Regular.woff2` | ziti666 id 12 · 郑庆科帅黑体 | 1549 KB |
| `ZQK Shuma` | `/fonts/zqk-shuma/Regular.woff2` | ziti666 id 09 · 郑庆科数码体 | 471 KB |
| `ZQK Yayun` | `/fonts/zqk-yayun/Regular.woff2` | ziti666 id 02 · 郑庆科雅韵体 | 1923 KB |
| `ZQK Youhuabi` | `/fonts/zqk-youhuabi/Regular.woff2` | ziti666 id 15 · 郑庆科油画笔 | 6291 KB |
| `ZQK Zhiya` | `/fonts/zqk-zhiya/Regular.woff2` | ziti666 id 03 · 郑庆科智雅体 | 602 KB |
| `ZQK Ziyou` | `/fonts/zqk-ziyou/Regular.woff2` | ziti666 id 04 · 郑庆科自由宋 | 2795 KB |
| `ZS Cukai` | `/fonts/zs-cukai/Regular.woff2` | ziti666 id 25 · 智枢粗楷 | 5438 KB |
| `ZS Fangsong` | `/fonts/zs-fangsong/Regular.woff2` | ziti666 id 26 · 智枢仿宋 | 7189 KB |
| `ZS Moran` | `/fonts/zs-moran/Regular.woff2` | ziti666 id 27 · 智枢墨染体 | 5107 KB |
| `ZS Qingkai` | `/fonts/zs-qingkai/Regular.woff2` | ziti666 id 28 · 智枢清楷体 | 4864 KB |
| `ZS Shiguang` | `/fonts/zs-shiguang/Regular.woff2` | ziti666 id 29 · 智枢时光体 | 5331 KB |

完整字符 woff2，未 subset，跨素材复用。30 天 immutable 缓存，浏览器第一次加载之后基本不再请求。

另：素材入口 HTML（如 `014-draft.html`）通过 `<link>` 静态加载了 Google Fonts 的 `Noto Sans SC` / `Noto Serif SC` / `Inter`，
不放在本目录但也在 `episode.json` 的 `fonts[]` 里以 **无 src** 条目列出，inspector 字体下拉就能选到它们。

## 在 episode.json 里引用

`src` 必须用**绝对路径** `/fonts/...`（开头带 `/`），bootstrap.js 会照原样写进 `@font-face src`：

```json
"fonts": [
  { "family": "XY Kaiti",    "src": "/fonts/xy-kaiti/Regular.woff2",    "weight": 400, "format": "woff2", "display": "swap" }
]
```

声明后在 `scenes[id].style.numFont` / `subtitleFont` / `overlays[*].style.font` 里用 family 名引用。

## 加新字体

1. 从 ziti666.cn 拉完整 ttf：`curl -sL https://www.ziti666.cn/api/download-all/<id> -o /tmp/x.zip && unzip -o /tmp/x.zip -d /tmp/x/`（zip 里的小 woff/woff2 只含 30 来个预览字符，**别用**，用 ttf）
2. ttf → woff2（不 subset）：
   ```bash
   uvx --with brotli --from fonttools python3 -c "from fontTools.ttLib import TTFont; f=TTFont('/tmp/x/SRC.ttf'); f.flavor='woff2'; f.save('fonts/<family>/Regular.woff2')"
   ```
3. 在 episode.json `fonts[]` 加一条 `{"family":"…","src":"/fonts/<family>/Regular.woff2", ...}`，更新本 README 表格

批量下载脚本：`/tmp/ziti666-fetch.py`（一次拉 31 款 ziti666 全集，本仓库的 31 个 ziti woff2 就是这么生成的）。

## 为什么不放在 `.fonts/`

ncds.cc nginx vhost 的 `location ~ /\.(?!well-known/|\d) { deny all; }` 只放行 `.well-known/` 和 `.数字开头/`，`.fonts/` 会被 403。改 nginx 不如直接用不带 dot 的 `fonts/`——会在 / autoindex 里露出一项，可接受。