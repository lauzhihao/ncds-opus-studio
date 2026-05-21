# 飞书 API → lark-cli 改造记录

本项目硬约束：代码里不直接调用任何飞书 OpenAPI；所有飞书 IO 都 spawn `lark-cli`。

**状态**：已完成。`grep -nE "open-apis|tenant_access_token|larksuiteoapi" scripts/*.mjs`
无非注释结果。

## 改造前后

| 文件 | 改造前 | 改造后 |
|---|---|---|
| `scripts/feishu_sdk_adapter.mjs` | 加载全局 `@larksuiteoapi/node-sdk`，封装 OpenAPI 调用 | 委托给 `scripts/lark_cli.mjs`，保留原 exports |
| `scripts/lark_cli.mjs` | (新增) | spawn `lark-cli` 子进程，封装 IM 发消息 / 文档增删改读 / Drive 上传 / 权限管理 / 任务管理 |
| `scripts/video_job_worker.mjs` | 自带 `feishuApi` / `feishuApiMultipart` / `getTenantToken` + 直接 `fetch('/open-apis/...')` | 改用 adapter 的 `sendImMessage` / `uploadDriveFile` / `writeMarkdownToDocx` |
| `scripts/rewrite_command_runner.mjs` | 自带 `fetchTenantToken` + `buildFeishuApi`，直调 `docx/v1/raw_content` / `blocks/convert` / `blocks/descendant` / `im/v1/messages` | 改用 adapter 的 `readDocxContent` / `writeMarkdownToDocx` / `sendImMessage` |

## 新依赖关系

```
video_job_worker.mjs ─┐
rewrite_command_runner.mjs ─┼─► feishu_sdk_adapter.mjs ─► lark_cli.mjs ─► spawn lark-cli
content_rewrite_runner.mjs ─┘
```

## lark-cli 命令映射

| 老调用 | 新调用 |
|---|---|
| `POST /open-apis/auth/v3/tenant_access_token/internal` | (删除，lark-cli 自管 tenant_access_token) |
| `POST /open-apis/im/v1/messages` | `lark-cli im +messages-send --as bot --chat-id/--user-id --text` |
| `POST /open-apis/drive/v1/files/upload_all` | `lark-cli drive +upload --as bot --file --folder-token` |
| `POST /open-apis/drive/v1/files/upload_prepare` `upload_part` `upload_finish` | 同上（lark-cli 自动决定简单/分片上传） |
| `GET /open-apis/drive/explorer/v2/root_folder/meta` | (删除，传 `--folder-token` 缺省即根目录) |
| `POST /open-apis/drive/v1/files/create_folder` | `lark-cli drive +create-folder --as bot --name --folder-token` |
| `PATCH drive/v1/permissions/<token>/public` | `lark-cli drive permission.public patch --as bot --params --data --yes` |
| `POST drive/v1/permissions/<token>/members` | `lark-cli drive permission.members create --as bot --params --data --yes` |
| `POST docx/v1/documents` | `lark-cli docs +create --api-version v2 --as bot --title [--folder-token] [--markdown]` |
| `GET docx/v1/documents/<id>/raw_content` | `lark-cli docs +fetch --api-version v2 --as bot --doc` |
| `POST docx/v1/documents/blocks/convert` + descendant insert | `lark-cli docs +update --api-version v2 --as bot --doc --markdown - --mode append` |
| `POST task/v2/tasks` | `lark-cli task +create --as bot --data` |
| `PATCH task/v2/tasks/<guid>` | `lark-cli task task.patch --as bot --params --data` |

## 兼容性

- `feishu_sdk_adapter.mjs` 的所有 exports 保留原签名（含 `client` 形参），调用方代码改动量最小。
- 老 SDK 模式下的 `payload.appId` / `payload.appSecret` 不再被使用——lark-cli 自管账号凭据
  （通过 `lark-cli auth login` 配置）。`rewrite_command_runner.mjs` 已移除对这两个字段的强制校验。
- 老的 `buildSdkEntryCandidates(...)` 仍然 export 但永远返回 `[]`，仅为兼容老测试。

## 测试结果

`node --test scripts/*.test.mjs` — **91/92 通过**。

唯一失败的 `buildSuccessMessage lists uploaded drafts and skips failures` 是**迁移前就存在**
的测试/实现不一致（测试期望 `GPT-5.5: <url>` 格式，实现输出 `- <url>` 格式），
**与本次改造无关**，作为单独的 cleanup 项待跟。

## 验证清单

- [x] `grep -nE "open-apis|tenant_access_token" scripts/*.mjs`（非注释）无结果
- [x] `node --check scripts/*.mjs` 全部通过
- [x] `feishu_sdk_adapter.mjs` 22 个 exports 全部可加载（`import()` smoke test）
- [x] `node --test` 91/92 通过（剩 1 个为前置 bug）
- [ ] 端到端：在飞书 bot 上跑 `/asr <抖音链接>` 和 `/rw <doc URL>` 验证产物正确（需要 bot 重启 + lark-cli 已认证）
