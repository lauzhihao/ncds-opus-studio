# 迁移进度

## 阶段

- [x] **骨架**：项目目录 + pyproject.toml + package.json + .gitignore + .env.example
- [x] **/vid**：搬 video_service.py → `commands/vid.py`；去掉飞书发送（返回视频路径），CLI 跑通
- [x] **/wst /tst**：搬 image_service.py 核心 → `commands/wst.py` / `tst.py`；搬 gpt-image 4 个脚本到 `gpt_image/`；CLI 跑通
- [x] **/asr**：搬 asr_command_runner + video_job_worker + 全部 skills + douyin_processing；写 Python wrapper `commands/asr.py`，CLI 跑通
- [x] **/rw**：搬 rewrite_command_runner + content_rewrite_runner + rewrite_profiles + feishu_sdk_adapter + video_rewrite_runner；写 Python wrapper `commands/rw.py`，CLI 跑通
- [x] **009 模板**：搬到 `src/ncds_opus_factory/templates/paper_card_talk/`，加 TEMPLATE.md 说明复制成新一集的工作流
- [x] **feishu_sdk_adapter.mjs 改造为 lark-cli**：完成。详见 [FEISHU-REFACTOR.md](FEISHU-REFACTOR.md)。`grep open-apis scripts/*.mjs` 非注释结果为空。91/92 测试通过。
- [ ] **lark-bot-listener 切换**：handler.py 的 ASR_RUNNER / RW_RUNNER 路径改向新项目；image_service / video_service 改为 import ncds_opus_factory
- [ ] **端到端验证**：5 个命令 + 009 模板各跑一次飞书 bot 触发
- [ ] **老位置打 DEPRECATED 标记**：xiaozhua workspace + gpt-image skill + .009-assets 加 DEPRECATED.md，双续 1-2 周后删

## 文件对照

| 来源 | 目标 |
|---|---|
| `~/lark-bot-listener/video_service.py` | `src/ncds_opus_factory/commands/vid.py` |
| `~/lark-bot-listener/image_service.py` `execute_wst` 路径 | `src/ncds_opus_factory/commands/wst.py` |
| `~/lark-bot-listener/image_service.py` `execute_tst` 路径 | `src/ncds_opus_factory/commands/tst.py` |
| `~/lark-bot-listener/image_service.py` 公网上传 | `src/ncds_opus_factory/common/public_upload.py` |
| `~/lark-bot-listener/image_service.py` 飞书下载图 | `src/ncds_opus_factory/common/lark_cli.py`（改造为 lark-cli wrapper） |
| `~/.codex/skills/gpt-image/scripts/{generate,generate_edit,gpt_image_gen,gpt_image_edit}.py` | `gpt_image/` |
| `~/.openclaw/workspaces/xiaozhua/scripts/*.mjs` | `scripts/`（剔除 query_ok_probe / https_cert_probe） |
| `~/.openclaw/workspaces/xiaozhua/skills/` | `skills/` |
| `~/.openclaw/workspaces/xiaozhua/douyin_processing/` | `pipelines/douyin_processing/` |
| `~/.openclaw/workspaces/xiaozhua/{requirements-bootstrap.txt,package.json,package-lock.json}` | `configs/` |
| `~/.openclaw/workspaces/xiaozhua/deploy/examples/` | `configs/openclaw-examples/` |
| `~/ncds-materials/.009-paper-card-talk-assets/{beats,player,overlays,image-slot}.js · styles.css · tts_gen.py · pic_gen.py · render.mjs · README.md` | `src/ncds_opus_factory/templates/paper_card_talk/` |

## 没搬的（保持外部依赖）

- `lark-cli`（系统级 CLI，本项目 spawn 调用）
- `ffmpeg`（系统级，本项目 spawn 调用）
- `~/.openclaw/openclaw.json`（账号凭据，运行时读取，不进 repo）
- node_modules（需要在新项目里跑一次 `npm install`）

## 老位置当前状态

- `~/.openclaw/workspaces/xiaozhua/`：保留运行，lark-bot-listener 还在用，等切换后再废弃
- `~/.codex/skills/gpt-image/scripts/`：保留运行，等切换后再废弃
- `~/ncds-materials/.009-paper-card-talk-assets/`：保留（这是已发布的素材本身），TEMPLATE.md 模板是它的可复用化版本，新一集走模板
