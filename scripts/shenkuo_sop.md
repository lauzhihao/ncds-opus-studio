# 沈括 — 对标号情报 / 素材采集（离线批处理）

> 《梦溪笔谈》博物采集、记录见闻 —— 盯对标号,把原料拉回来喂下游。**工厂最上游的供料层**
> (不在卧龙/鬼谷子/柳永/吴道子/伯牙 五大成片 agent 之内,是它们的上游)。

实现是命令 `commands/shenkuo.py`（`nof shenkuo`）。**不碰飞书**、离线、幂等、可被 cron/watchdog 调度。

## 职责（一拉两拆）

1. **拉作品列表**：`common/tikhub_client.fetch_user_posts(sec_uid)` 分页 → `all_posts.json`(鬼谷子 guiguzi 吃的格式)。
2. **下载 mp4**：`tikhub_client`(TikHub,无水印播放地址 + streaming 下载)。
3. **拆文案**：听悟/Paraformer 转写(`skills/tingwu-asr`) → `.paraformer.json` + `.txt`(喂 benchmark 拆解 / 鬼谷子)。
4. **拆画面**：`ffmpeg` 场景切变截关键帧 → **裁舞台区**(`STAGE_CROP` 比例,去顶标签/底字幕/水印,只留中间内容) → **阈值分割抠图**(默认 `engine=threshold`:黑剪影/彩色道具 on 纸纹背景 → 透明,矢量级锐利、保留全部元素、不下模型;`--engine rembg` 为彩色照片类备选) → 按前景占比过滤纯色过场帧 → **对标素材池**(待筛进吴道子 figure_lib)。
5. **拉评论**：`common/tikhub_client.fetch_top_comments(aweme_id, top_n=20)` → `<aweme_id>.comments.json`(受众反馈,喂对标拆解)。热门序 + 早停,爆款也只需几次调用,不翻全量;`--top-comments 0` 关闭。

## 指标层(SQLite 时间序列)

作品的 `digg/comment/collect/share` 会随时间涨 —— `common/benchmark_store.py` 把每次刷新存进
`state/shenkuo/benchmark.db`(WAL,为未来多实例并发写打底)：

- `posts` 表:作品身份(desc/create_time)+ first_seen/last_seen。
- `metric_snapshots` 表:每次刷新一条快照,**仅当指标较上次有变化才插** —— 既留增长曲线又不爆库。
- 查询助手:`top_by_digg(sec_uid)` / `growth(aweme_id, since_ts)`(算窗口涨幅,挑"在加速"的作品)。

**注意**:列表接口返回的是「置顶优先 + 发布时间倒序」,**不是按赞排**;且 `fetch_user_posts` 按
`max_items` 截断 —— 拉 30 条只覆盖最近约一个月。要历史全账号指标,用 `--refresh-only`(默认拉 200 条)
或调大 `--max-posts`。

## 输入 / 产物契约

- 输入：对标号 `sec_user_id`(`--author`)或单条 `aweme_id`(`--aweme`,验证链路)。
- 产物(本地 `state/`,NAS 同步是后续)：
  - `state/shenkuo/benchmark.db`：指标层 SQLite(跨号、时间序列)。
  - `state/benchmark/author_<sec_uid>/`：`all_posts.json`(最新快照,鬼谷子吃)+ `<aweme_id>.mp4` / `.paraformer.json` / `.txt` + `<aweme_id>.comments.json`(top 评论)+ `<aweme_id>/frames/*.jpg` + `collected.json`(采了啥/状态)
  - `state/figure_collected/<aweme_id>/*.png`：抠好的透明素材

## 跑法

```bash
# DASHSCOPE key:优先 env,其次主仓库 .env,其次 ~/.openclaw(转写用)
PYTHONPATH=src python3 -m ncds_opus_factory shenkuo --author <sec_uid> --top 10 [--frames 8] [--top-comments 20]
PYTHONPATH=src python3 -m ncds_opus_factory shenkuo --author <sec_uid> --refresh-only  # 只刷指标层(高频 cron,便宜)
PYTHONPATH=src python3 -m ncds_opus_factory shenkuo --aweme <aweme_id>   # 单条验证
```

幂等:已下载/已转写/已截帧/已抠图的跳过,可反复跑、断点续。单条失败不拖垮整批。

## 复用 / 坑

- 复用:`common/tikhub_client`(下载 + 按作者拉) / `common/capabilities/tingwu.py`(听悟 vendor adapter) / `ffmpeg` / `rembg`。
- **不用** `commands/asr.py`(它把结果发飞书,违背离线) / `pipelines/douyin_processing/download_and_transcribe.py`(yt-dlp+本地 whisper 老垃圾)。
- 转写 key:统一由 capabilities 基座读取环境变量 / 主仓库 `.env` 的 `DASHSCOPE_API_KEY`。
- 抠图依赖:`rembg`+`onnxruntime`(装在 python3,`pip install --break-system-packages`)。
- 截帧抠图质量:对标号是浅纸背景 + 黑剪影/图标,rembg 抠深色前景质量待验,v0 先跑通。

## 后续（不在本次范围）

- 文生图/图生图补料(缺素材时调 `gpt_image/`)。
- 24h 常驻(cron/watchdog)—— 现在只是可被调度的批处理命令。
- NAS 挂载/同步(先本地 state/)。
- 抠好的素材自动纳入吴道子 `figure_lib`(现在只落素材池,人工筛)。
