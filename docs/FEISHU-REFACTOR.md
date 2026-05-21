# 飞书 API → lark-cli 改造清单

本项目硬约束：代码里不直接调用任何飞书 OpenAPI。所有需要从飞书读 / 写的动作，
都改成 spawn `lark-cli`。

迁移过来的 Node runner 目前**仍然违反**这个约束，这份文档列出所有触点，作为后续改造的清单。

## 现状（违反约束的位置）

### `scripts/feishu_sdk_adapter.mjs`

整个文件就是 `@larksuiteoapi/node-sdk` 的封装，要全部废弃。
对外导出的符号 → 改为对应的 lark-cli 命令：

| 现导出 | 等价 lark-cli 命令 |
|---|---|
| `createFeishuClient(...)` | （不再需要 client 对象，每次 spawn lark-cli 即可） |
| `createDoc(client, title, folderToken)` | `lark-cli docs +create --api-version v2 --title <title> [--folder-token <token>]` |
| `addMemberPermission(...)` | `lark-cli drive +permission-grant ...` |
| `setOrgEditablePermission(...)` | `lark-cli drive +permission-public-set ...` |
| `resolveFeishuApiBase(...)` | 删除（lark-cli 自管域名） |
| `resolveFeishuDocBase(...)` | 删除 |

### `scripts/video_job_worker.mjs`

直调 `open-apis/` 的位置（行号见下）：

| 行 | 用途 | 改造方向 |
|---|---|---|
| 394 | `/open-apis/auth/v3/tenant_access_token/internal` | 删除（lark-cli 自管 token） |
| 545 | `/open-apis/im/v1/messages` 发消息 | `lark-cli im +messages-send --as bot --receive-id <chat_id> --receive-id-type chat_id --text ...` |
| 695 | `/open-apis/drive/explorer/v2/root_folder/meta` | `lark-cli drive +file-list` 或删除 |
| 750 | `/open-apis/drive/v1/files/upload_all` | `lark-cli drive +file-upload --file <path> --parent-token <folder>` |
| 766 | `/open-apis/drive/v1/files/upload_prepare` | 同上（lark-cli 自动选择分片） |
| 795 | `/open-apis/drive/v1/files/upload_part` | 同上 |
| 801 | `/open-apis/drive/v1/files/upload_finish` | 同上 |
| 1148 | `createDoc(client, title)` | `lark-cli docs +create --api-version v2 --title <title>` |
| 1159 | `/open-apis/docx/v1/documents/blocks/convert` | `lark-cli docs +update --api-version v2 --doc-id <id> --content-file <md>` |
| 1166 | `/open-apis/docx/v1/documents/<id>/blocks/<id>/descendant` | 同上 |

### `scripts/content_rewrite_runner.mjs`

待审计（同样依赖 feishu_sdk_adapter）。

## 改造策略

1. 写一个 `scripts/lark_cli.mjs` helper：`async function larkCli(args: string[]): Promise<{stdout, stderr, code}>`，统一 spawn。
2. 改写 `feishu_sdk_adapter.mjs` 的每个导出函数为基于 larkCli 的实现，**保持原签名**，最小化外层改动。
3. 改写 `video_job_worker.mjs` 里所有 `feishuApi(...)` / `feishuApiMultipart(...)` 调用为 larkCli 调用。
4. 跑测试 `npm test`（已迁移 `*.test.mjs`）。

## 验收标准

- `grep -nE "open-apis|larksuiteoapi|tenant_access_token" scripts/` 无结果。
- /asr 和 /rw 端到端跑通，飞书产物（消息、文档、文件）和改造前一致。
