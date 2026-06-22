# 前端集成指南 · ncds-opus-studio HTTP API

给前端 / app 接手者：端点签名的真源是 `GET /openapi.json` 和 `/docs`（Swagger UI）。本文只补 OpenAPI 表达不了的当前资源边界、SSE 信封、artifact 约定和实际消费者。

## 1. 当前入口关系（2026-06-22）

| 入口 | 当前消费者 | 运行时 | 状态 |
|---|---|---|---|
| `/jobs/*` + `/pipelines` + `/preview/*` | `web/` 的 `/studio` React 画布 | `PipelineRunner` facade；多数节点经 engine performer 执行，`asr` 固定 legacy | **web 主路径** |
| `/tasks/*` + `/commands` + `/artifacts/*` | `app/` Flutter 决策视角，也可给外部调用方提交 agent / primitive 任务 | `TaskRunner` 入队；`nof-worker` 唯一执行 | **app 主路径** |
| `/instances/*` | 后端测试、内部迁移、未来 web/app 统一入口 | `InstanceStore` + `InstanceRunner` + recipe | 已存在，但**尚未替代前端主路径** |

不要把 `/instances` 写成已经接管 web/app 的入口；当前它是 engine driver API。web 仍以 `job_id` 为 UI 句柄，app 仍以 `task_id` 为 UI 句柄。

## 2. 起 server

运行方式以 [docs/README.md](README.md) 的 runbook 为准。开发期常用：

```bash
.venv/bin/uvicorn ncds_opus_factory.server.app:app --host 0.0.0.0 --port 8810 --reload --reload-dir src
```

- `/studio` 是仓库自带 SPA 挂载点：prod 读 `web/dist`，`NOF_DEV=1` 反代 vite。
- nof-server 在 S3 后只负责 HTTP/SSE/入队/serve；`/tasks` 的离线执行在 `nof-worker`。
- 常用 env：`NOF_SERVER_HOST` / `NOF_SERVER_PORT` / `NOF_STATE_DIR` / `NOF_VIDEO_JOBS_DIR` / `NOF_ARTIFACTS_ROOT`。

## 3. `/jobs`：web 内容画布主路径

`/studio` 当前通过 `web/src/api/client.ts` 调 `/jobs`。它是作品/内容视角：一个 job 对应一条 015 画布，节点状态、画布位置、episode、预览和本地文件都围绕 `job_id`。

常用端点：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/pipelines` | 可创建的 pipeline catalog |
| `POST` | `/jobs` | 创建作品，body `{pipeline_id, title?, inputs}` |
| `GET` | `/jobs` | 作品列表 |
| `GET` | `/jobs/{job_id}` | 作品详情，含节点状态与画布数据 |
| `POST` | `/jobs/{job_id}/nodes/{node}/run` | 跑单个节点，会 reset 自身与下游 |
| `POST` | `/jobs/{job_id}/nodes/{node}/cancel` | 取消正在跑的节点 |
| `GET` | `/jobs/{job_id}/events` | web 画布 SSE |
| `GET` / `PUT` | `/jobs/{job_id}/episode` | 读写 `02_rw/episode.json` |
| `GET` / `PUT` | `/jobs/{job_id}/files/{relpath}` | 读写 `video-jobs/{job_id}/` 下文本/媒体文件 |
| `GET` | `/preview/{job_id}` | 015 预览 iframe |

当前 strangler 行为：`NOF_ENGINE_NODES` 未设置时，`rw/lines/storyboard/tts/image/render` 经 facade 走 engine；`asr` 因需要步内增量与后台 enrich，仍走 legacy `_execute_asr_collect`。

`GET /jobs/{job_id}/events` 推的是画布节点事件；客户端断线后应重新 `GET /jobs/{job_id}` 对齐全量状态。

## 4. `/tasks`：app 决策视角主路径

Flutter app 的 `FactoryClient` 当前围绕 `/tasks` 工作：拉 agent 收件箱、提交任务、看详情、收 SSE、提交审核决定。`/tasks` 不是 `/instances` 的兼容读层；它仍由 `TaskRunner` / `nof-worker` 驱动。

| 资源 | 端点 | 说明 |
|---|---|---|
| 命令清单 | `GET /commands` | catalog，渲染 agent / primitive 入口 |
| 命令表单 | `GET /commands/{cmd}/schema` | 动态字段说明 |
| 建任务 | `POST /tasks` | body `{cmd, params}`，成功 `201` + `Location: /tasks/{id}` |
| 任务列表 | `GET /tasks` | app 收件箱，全量任务 meta |
| 任务详情 | `GET /tasks/{task_id}` | meta + result + artifacts + review |
| 任务进度 | `GET /tasks/{task_id}/events` | SSE，回放历史后 tail 新事件 |
| 提交决策 | `POST /tasks/{task_id}/review` | body `{decision, note?, note_origin?}` |
| 撤销决策 | `DELETE /tasks/{task_id}/review` | 幂等撤销 |
| 取消/恢复 | `POST /tasks/{task_id}/cancel` / `restore` | 操作离线任务状态 |

最小流程：

```js
const schema = await (await fetch('/commands/liuyong/schema')).json();

const r = await fetch('/tasks', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ cmd: 'liuyong', params: { topic: '同事阴阳你怎么办' } }),
});
const { task_id } = await r.json();

const es = new EventSource(`/tasks/${task_id}/events`);
es.onmessage = (e) => {
  if (e.data === '[DONE]') {
    es.close();
    return;
  }
  const ev = JSON.parse(e.data);
  if (ev.type === 'progress') appendLog(ev.text);
};
```

## 5. `/instances`：engine driver API

`/instances` 暴露生产引擎原语，供迁移和测试使用：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` / `POST` | `/instances` | 列实例 / 创建实例 |
| `GET` | `/instances/{iid}` | 读 InstanceState |
| `GET` | `/instances/{iid}/runnable` | 查询当前可跑 step |
| `POST` | `/instances/{iid}/steps/{sid}/run` | 同步跑一步 |
| `POST` | `/instances/{iid}/steps/{sid}/approve` | `awaiting_review` 出口 |
| `POST` | `/instances/{iid}/steps/{sid}/reset` | 重置该步与传递下游 |
| `POST` | `/instances/{iid}/finalize` | 根据步骤终态结算实例 |
| `GET` | `/instances/{iid}/events?level=meta,step` | 分层 SSE |

当前 `RECIPE_REGISTRY` 只注册 `paper_card_talk_015`。`figure_talk` 仍是未来 recipe / cold chain，不要在前端当成可选 engine recipe。

## 6. SSE 信封

`/tasks/{id}/events` 事件：

```jsonc
{ "type": "progress", "ts": 1733740800123, "text": "质检: pass - ..." }
{ "type": "done", "ts": 1733740900456, "result": {} }
{ "type": "error", "ts": 1733740900456, "error": "RuntimeError: ..." }
```

`/jobs/{id}/events` 是画布节点事件，包含 node status / outputs patch 等 web 专用结构。`/instances/{id}/events` 按 `level=meta,step,detail` 过滤；当前路由已存在，但前端主路径尚未依赖它。

所有 SSE 收到 `[DONE]` 后关闭连接，再 GET 对应详情接口重同步。

## 7. params 与 artifact

`POST /tasks` 的 `params` 是 free-form；字段说明从 `GET /commands/{cmd}/schema` 动态取，不要硬编码。

artifact 统一按 URL 渲染：

| kind | 渲染建议 |
|---|---|
| `script` / `text` | Markdown / 纯文本 |
| `audio` | `<audio>` |
| `video` | `<video>` |
| `image` | `<img>` |
| `data` | JSON 面板 |
| `dir` | 调 `GET /artifacts/dir/{relpath}` 展开 |

`/artifacts/files/{relpath}` 覆盖 `state/` 与 `video-jobs/` 白名单根；`/jobs/{job_id}/files/{relpath}` 只服务 web 画布的 `video-jobs/{job_id}/`。

## 8. 决策

app 的同意/拒绝走 `/tasks/{id}/review`，只落人工决策，不自动改任务状态、不触发重跑。

```js
await fetch(`/tasks/${id}/review`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ decision: 'approved', note: '配音再快点' }),
});
```

`decision` 取 `approved | rejected`；再次 POST 会覆盖最后决策。`note_origin=machine` 表示模板生成意见，不冒充人工训练样本。

## 9. 错误与状态码

- `POST /tasks` 未知 cmd → 404；建成功 → 201。
- `/instances` 状态机冲突 → 409；坏 recipe / body → 400；实例或 step 不存在 → 404。
- 产物路由：不存在 404；越界或非白名单根 → 403；目录当文件取 → 400。
- 任务失败：详情接口返回 `status="failed"` + `error`；SSE 也会发 error 事件。
