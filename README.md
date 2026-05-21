# ncds-opus-factory

5 个内容生产命令的核心引擎 + 可复用视频素材模板。

## 命令

| 命令 | 用途 | 入口模块 |
|---|---|---|
| `/wst <提示词>` | 文生图（gpt-image-2） | `ncds_opus_factory.commands.wst` |
| `/tst <参考图> <提示词>` | 图生图（gpt-image-2 edit） | `ncds_opus_factory.commands.tst` |
| `/vid [-秒数] <提示词>` | 视频生成（DashScope HappyHorse），可附参考图 | `ncds_opus_factory.commands.vid` |
| `/asr <抖音/媒体链接>` | 多链路并行转写 + 爆款精华分析（Node runner + Whisper/Tingwu + 飞书文档落库） | `ncds_opus_factory.commands.asr` |
| `/rw <精华文档URL>` | gpt-5.5 + gemini 双模型改写（Node runner） | `ncds_opus_factory.commands.rw` |

## 设计原则

- **不接入飞书 API**。所有飞书 IO（发消息、下载图片、读写文档）由调用方通过 `lark-cli` 完成；本项目代码不直接 import 任何飞书 SDK，也不调任何 `open.feishu.cn` 端点。
- **5 个命令 = 5 个独立可调用单元**。每个命令同时支持 CLI（`python -m ncds_opus_factory.<cmd>`）和 Python import（`from ncds_opus_factory.commands import wst`）。
- **进度回调由调用方传入**。命令本身不知道"发飞书消息"，只通过 `on_progress(text)` 回调把状态吐给调用方。

## 目录

```
src/ncds_opus_factory/
├── cli.py                  # 统一 CLI 入口
├── commands/
│   ├── wst.py · tst.py · vid.py · asr.py · rw.py
├── common/                 # 公共：媒体上传到公网、Whisper、yt-dlp 等
└── templates/
    └── paper_card_talk/    # 009 风格视频模板（beats.js 驱动 + AI 管线）

scripts/                    # /asr /rw 的 Node runner（spawn 自 Python wrapper）
gpt_image/                  # gpt-image-2 网关 Python 脚本（generate.py / generate_edit.py）
pipelines/                  # video_pipeline.py + douyin_processing 等 ASR pipeline
skills/                     # 各 skill 的 SKILL.md 说明（asr / rewrite / video-pipeline / ...）
configs/                    # openclaw.example.json 等
state/                      # 任务产物（gitignored）
```

## 迁移来源

| 文件/目录 | 旧位置 |
|---|---|
| `src/ncds_opus_factory/commands/wst.py · tst.py` | `~/lark-bot-listener/image_service.py` |
| `src/ncds_opus_factory/commands/vid.py` | `~/lark-bot-listener/video_service.py` |
| `scripts/*.mjs` | `~/.openclaw/workspaces/xiaozhua/scripts/` |
| `pipelines/` | `~/.openclaw/workspaces/xiaozhua/skills/video-pipeline/` + `douyin_processing/` |
| `gpt_image/` | `~/.codex/skills/gpt-image/scripts/` |
| `src/ncds_opus_factory/templates/paper_card_talk/` | `~/ncds-materials/.009-paper-card-talk-assets/` |

## 状态

迁移进行中。`docs/MIGRATION.md` 跟踪进度。
