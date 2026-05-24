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
| `/tts <beats>` | 批量 TTS（DashScope CosyVoice，paper-card-talk 模板用） | `ncds_opus_factory.commands.tts` |
| `/render <html_url + audio_dir>` | 离线录屏 + ffmpeg 合成 MP4（headless Chrome） | `ncds_opus_factory.commands.render` |

## 设计原则

- **不接入飞书 API**。所有飞书 IO（发消息、下载图片、读写文档）由调用方通过 `lark-cli` 完成；本项目代码不直接 import 任何飞书 SDK，也不调任何 `open.feishu.cn` 端点。
- **5 个命令 = 5 个独立可调用单元**。每个命令同时支持 CLI（`python -m ncds_opus_factory.<cmd>`）和 Python import（`from ncds_opus_factory.commands import wst`）。
- **进度回调由调用方传入**。命令本身不知道"发飞书消息"，只通过 `on_progress(text)` 回调把状态吐给调用方。

## 目录

```
src/ncds_opus_factory/
├── cli.py                  # 统一 CLI 入口
├── commands/
│   ├── wst.py · tst.py · vid.py · asr.py · rw.py · tts.py · render.py
│   └── render_runner.mjs   # render.py 调用的 generic node runner
├── common/                 # 公共：媒体上传到公网、Whisper、yt-dlp 等
├── server/                 # HTTP 包装层（详见下文 "HTTP server" 章节）
│   ├── app.py · routes/tasks.py · task_runner.py · task_store.py
│   └── schemas.py · state.py
└── templates/
    └── paper_card_talk/    # 009 风格视频模板（beats.js 驱动 + AI 管线）

scripts/                    # /asr /rw 的 Node runner（spawn 自 Python wrapper）
gpt_image/                  # gpt-image-2 网关 Python 脚本（generate.py / generate_edit.py）
pipelines/                  # video_pipeline.py + douyin_processing 等 ASR pipeline
skills/                     # 各 skill 的 SKILL.md 说明（asr / rewrite / video-pipeline / ...）
configs/                    # openclaw.example.json 等
state/                      # 任务产物（gitignored）
```

## HTTP server（nof-server）

把 7 个 commands 暴露为「POST 提交任务 + SSE 拉进度」的 HTTP 协议，供
daoer 等远端调用方使用。daoer↔ncds 之间走标准 HTTP+SSE，避免跨机直接 import。

### 启动

```bash
pip install -e .         # 安装本仓库（装依赖：fastapi/uvicorn/sse-starlette/pydantic）
nof-server               # 等价于 uvicorn ncds_opus_factory.server.app:app --host 0.0.0.0 --port 8810
```

可通过环境变量调整：
- `NOF_SERVER_HOST` / `NOF_SERVER_PORT`（默认 `0.0.0.0:8810`）
- `NOF_STATE_DIR`（任务持久化根目录，默认 `<repo>/state/tasks/`）
- `NOF_RENDER_NODE_PATH`（render 找 puppeteer-core 的 node_modules 目录，默认 `/tmp/node_modules`）

### 端点

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | 健康检查（含 state_dir、已注册 commands） |
| `GET` | `/tasks` | 列出所有已注册的 commands |
| `POST` | `/tasks/{cmd}` | 提交任务，body `{"params":{...}}`，返回 `{task_id,status:"pending"}` |
| `GET` | `/tasks/{task_id}` | 查询任务详情（meta + 终态 result） |
| `GET` | `/tasks/{task_id}/events` | **SSE** 推送进度事件，先回放历史，再 tail 新增，终态后发 `[DONE]` |

每个命令的 `params` 直接 spread 给 `commands.<cmd>.run(**params)`，字段对照各 command 的 `run` 签名。

### 任务事件协议

`events.jsonl` 逐行 JSON，三种类型：

```json
{"type":"progress","ts":1779600315678,"text":"TTS 开始：2 段 · voice=longtian_v3 ..."}
{"type":"done","ts":1779600318344,"result":{"audio_files":["..."],"total":2,...}}
{"type":"error","ts":1779600318344,"error":"RuntimeError: ..."}
```

SSE 流将每行直接作为 `data:` 推下去；当任务状态变成 `completed`/`failed` 且事件读完，附加一条 `data: [DONE]\n\n`。

### 调用示例

```bash
# 提交一个 TTS 任务
curl -X POST http://localhost:8810/tasks/tts \
  -H 'Content-Type: application/json' \
  -d '{"params":{"beats":["第一句","第二句"],"output_dir":"/tmp/audio","force":true}}'
# -> {"task_id":"t_1779600315677_e3874132","status":"pending"}

# 拉进度（流式，直到 [DONE]）
curl -N http://localhost:8810/tasks/t_1779600315677_e3874132/events

# 查终态详情
curl http://localhost:8810/tasks/t_1779600315677_e3874132
```

### 设计要点

- 每个 `commands.<cmd>.run` 都是「同步阻塞 + `on_progress(text)` 回调」的统一形态；server 端用 `asyncio.to_thread` 把它扔到工作线程，回调里写文件。
- `vid` 内部本来就是 submit + poll（分钟级），与同步快任务（`wst`/`tst`/`tts`）走同一套接口。
- 状态全部落在 `state/tasks/{task_id}/{meta.json,events.jsonl,result.json}`，SSE 断线重连可以从头回放。
- `render` 用 subprocess 调 `commands/render_runner.mjs`（node），需要 `/tmp/node_modules` 装好 puppeteer-core + puppeteer-screen-recorder；server 端会在仓库根自动建 symlink。

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

## Git 推送说明

本仓库的远端是 `git@github.com:lauzhihao/ncds-opus-studio.git`。但本机默认的 SSH key
（`~/.ssh/id_ed25519`）是 `lauzhihao/sub2api` 的 deploy key，不能推到本仓库。

`.git/config` 已经把 remote 改写成 SSH 别名形式：

```
[remote "origin"]
    url = git@github-lauzhihao:lauzhihao/ncds-opus-studio.git
```

`github-lauzhihao` 在 `~/.ssh/config` 里指向 `~/.ssh/id_ed25519_github_lauzhihao`：

```ssh-config
Host github-lauzhihao
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_github_lauzhihao
  IdentitiesOnly yes
```

正常 `git push origin main` 就能跑通。如果有人 clone 本仓库到别的机器，需要：
1. 同时拥有这把 user key
2. 在自己的 `~/.ssh/config` 里也配 `github-lauzhihao` 别名

或者直接把 remote 改成 HTTPS + PAT（`git remote set-url origin https://github.com/lauzhihao/ncds-opus-studio.git`）。
