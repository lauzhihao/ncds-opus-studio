# 前端集成指南 · ncds-opus-studio HTTP API

给前端 agent：在手机 / iPad 上控制内容工厂（5 个中国风 agent + 操盘手 + 底层命令），
任务在 server 跑，端上只负责 **下指令 + 看进度 + 审看产物**。

> **端点签名的真源 = `GET /openapi.json` 和 `/docs`（Swagger UI）**，由 FastAPI 自动生成、
> 永远和代码同步。本文档只补 OpenAPI 表达不了的部分：动态 params、SSE 信封、artifact 渲染、
> 标准流程、各 agent 脾气。**不要去读 server 的 Python 推这些**。

---

## 1. 起 server

```bash
# 安装（一次）
pip install -e .            # 装 fastapi/uvicorn/sse-starlette/pydantic/python-dotenv 等

# 启动（默认 0.0.0.0:8810）
nof-server
# 或
NOF_SERVER_PORT=8810 PYTHONPATH=src python3 -m ncds_opus_factory.server.app
```

- **CORS 全开**（`allow_origins=["*"]`）→ 前端 dev server 可直接跨域调，无需代理。
- 常用 env：`NOF_SERVER_HOST` / `NOF_SERVER_PORT` / `NOF_STATE_DIR`（任务存储）/
  `NOF_VIDEO_JOBS_DIR` / `NOF_ARTIFACTS_ROOT`（产物根，默认仓库根）。
- **`.env` 坑**：server 启动时从仓库根加载 `.env`（含 `DASHSCOPE_API_KEY` 等）。
  **纯 UI 联调不需要真 key** —— `GET /commands`、schema、`POST /tasks` 返回 201、SSE 这些
  契约在没 key 时全部成立；只有**真跑一条 agent 到底**（boya/shenkuo/wudaozi/tts 触 DashScope，
  liuyong/guiguzi 触 scodex，wolong 触 opus）才需要 `.env` + 对应 CLI 在 PATH。
- `/studio` 是仓库自带的 SPA 挂载点（prod 读 `web/dist`，`NOF_DEV=1` 反代 vite）；
  你做的前端可以独立部署，也可以替换它。

---

## 2. 资源模型：command（能做什么） vs task（做了一次）

两者是**不同资源**，URL 严格分离：

| 资源 | 端点 | 说明 |
|---|---|---|
| 命令清单 | `GET /commands` | catalog，渲染"选哪个 agent" |
| 命令表单 | `GET /commands/{cmd}/schema` | 该命令要填什么 → 渲染输入表单 |
| 建任务 | `POST /tasks` | body `{cmd, params}` → **201 + `Location: /tasks/{id}`** |
| 任务列表 | `GET /tasks` | 所有实例（最新在前） |
| 任务详情 | `GET /tasks/{task_id}` | 含 `result` + `artifacts` + `review` |
| 任务进度 | `GET /tasks/{task_id}/events` | **SSE** |
| 提交决策 | `POST /tasks/{task_id}/review` | body `{decision, note?}` → 同意/拒绝 + 备注 |
| 读产物 | `GET /artifacts/files/{relpath}` | md/mp3/mp4/png/json，**支持 Range** |
| 列目录 | `GET /artifacts/dir/{relpath}` | 浏览采集目录 / 分镜实例 / 待验收清单 |

> 旧端点 `POST /tasks/{cmd}`、`GET /tasks/{cmd}/schema` 已废弃（405/404），别用。

---

## 3. 标准流程（一次任务的生命周期）

```
GET /commands                         → 列出 agent，按 group 分组（agent / primitive）
GET /commands/{cmd}/schema            → 拿字段，渲染表单
POST /tasks {cmd, params}             → 201，从 Location 或 body.task_id 拿 id
GET /tasks/{id}/events  (SSE)         → 实时进度，直到收到 [DONE]
GET /tasks/{id}                       → 终态详情，读 artifacts 渲染产物
```

最小示例：

```js
// 1) 选题表单字段
const schema = await (await fetch(`${API}/commands/liuyong/schema`)).json();
// schema.fields = [{name:'topic', label:'选题', type:'string', required:true, ...}, ...]

// 2) 建任务
const r = await fetch(`${API}/tasks`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ cmd: 'liuyong', params: { topic: '同事阴阳你怎么办' } }),
});
const { task_id } = await r.json();   // r.status === 201

// 3) 订阅进度
const es = new EventSource(`${API}/tasks/${task_id}/events`);
es.onmessage = (e) => {
  if (e.data === '[DONE]') { es.close(); loadDetail(task_id); return; }
  const ev = JSON.parse(e.data);      // TaskEvent
  if (ev.type === 'progress') appendLog(ev.text);
  if (ev.type === 'error')    showError(ev.error);
};
```

---

## 4. SSE 信封（OpenAPI 不描述流式格式）

`GET /tasks/{id}/events`：**先回放历史事件，再 tail 新增，终态后发 `[DONE]`**。
每条 SSE 消息的 `data:` 要么是一个 `[DONE]` 字面串，要么是一个 **TaskEvent** JSON：

```jsonc
{ "type": "progress", "ts": 1733740800123, "text": "质检[gpt5]: pass - ..." }
{ "type": "done",     "ts": 1733740900456, "result": { /* run 返回值，同 GET /tasks/{id}.result */ } }
{ "type": "error",    "ts": 1733740900456, "error": "RuntimeError: ..." }
```

- `type ∈ progress | done | error`；`ts` 是毫秒。
- 收到 `[DONE]` 即关闭 EventSource，再 `GET /tasks/{id}` 取终态（含 `artifacts`）。
- **不要轮询死等**：liuyong / wolong 是分钟级任务，靠 SSE 看进度。

---

## 5. params 是动态的（关键）

`POST /tasks` 的 `params` 在 OpenAPI 里只是 `object`（free-form），**真正的字段说明书在
`GET /commands/{cmd}/schema`，运行时取**。字段结构：

```jsonc
{
  "cmd": "liuyong",
  "label": "柳永 · 编剧+质检",
  "group": "agent",
  "summary": "...",
  "fields": [
    { "name": "topic", "label": "选题", "type": "string", "required": true, "help": "一句话想法" },
    { "name": "user_requirements", "label": "附加创作要求", "type": "text", "required": false }
  ]
}
```

`type` 词表（决定渲染哪种控件）：

| type | 控件 |
|---|---|
| `string` | 单行输入 |
| `text` | 多行文本域 |
| `int` / `float` | 数字输入 |
| `bool` | 开关 |
| `string[]` | 多值（逗号分隔或多输入框） |
| `enum` | 下拉（选项在字段的 `enum` 数组里） |

字段可带 `default` / `enum` / `help`。前端按 schema 动态渲染，**不要硬编码各命令的字段**。

---

## 6. artifact 渲染约定（终态产物）

`GET /tasks/{id}` 完成态返回 `artifacts: [{ label, kind, url, path }]`。按 `kind` 决定怎么渲染：

| kind | 含义 | 渲染建议 |
|---|---|---|
| `script` | 脚本 `.md` | Markdown 阅读器（可编辑后 `PUT /jobs/{job}/files/...` 回写） |
| `audio` | `.mp3` 等 | `<audio>`，url 支持 Range 可拖动 |
| `video` | `.mp4` 等 | `<video>`，同上 |
| `image` | `.png/.jpg` | `<img>` |
| `data` | `.json` | 折叠 JSON / 质检面板 |
| `dir` | 目录 | `GET` 该 url（`/artifacts/dir/...`）拿 `entries`，做文件浏览 |
| `text`/`file` | 其它 | 纯文本 / 下载 |

`url` 已是可直接 GET 的相对路径（`/artifacts/files/...` 或 `/artifacts/dir/...`）。
目录列表项：`{ name, is_dir, size, kind, url }`。

---

## 7. 各 agent 脾气（建 UI 时要知道）

| cmd | group | 关键 params | 耗时 | 产物 | 注意 |
|---|---|---|---|---|---|
| `guiguzi` | agent | `benchmark_path`(必填) | 秒~十秒 | 选题库 topics.json | benchmark 来自沈括采集 |
| `liuyong` | agent | `topic`(必填) | **分钟级** | 脚本 .md + 质检 .qc.json | 内含打回重写循环，靠 SSE |
| `wudaozi` | agent | `script_path` 或 `script_text` | 十秒级 | storyboard.json / beats.js | **mp4 要再发一个 `render` 任务**，wudaozi 本身不出片 |
| `boya` | agent | `job_dir`(必填) | 十秒级 | master.mp3 + audio_plan.json | job 目录需先有 audio/ 或 beats |
| `shenkuo` | agent | `author` 或 `aweme` | 分钟级 | 采集目录(mp4/转写/抠图) | 触 TikHub，需 token |
| `wolong` | agent | `count` | **分钟级，重** | 待验收清单目录 | 拉起 opus 自主编排，最重 |

底层命令（`wst/tst/vid/asr/rw/tts/render/render_015`）= primitive，前端可放进"高级"区。

**一个常见组合（成片）**：`liuyong`(脚本) → `wudaozi`(分镜) → `render`(出 mp4) → `boya`(配音混音)。
端上是多个任务串起来，不是一个调用。

---

## 8. 决策（同意 / 拒绝 + 备注）

移动端「点同意/拒绝」走一个独立端点，与任务执行解耦 —— 它不改任务状态、不触发重跑，
只把一条人工决策落到 `state/tasks/{id}/review.json`。

```js
// 点「同意」并附一句语音/打字备注
await fetch(`${API}/tasks/${id}/review`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ decision: 'approved', note: '配音再快点' }),
});
// → 200 { decision, note, reviewed_at }
```

- `decision ∈ approved | rejected`；`note` 可选（适合塞语音转写 / 修改意见，可当下一轮任务的 params 喂回去）。
- **幂等覆盖**：再次 POST 即改判，最后一次为准。
- `GET /tasks/{id}` 终态详情多一个 `review` 字段（未决为 `null`）。
- `GET /tasks` 列表里每条 meta 多一个 `decision`（`approved`/`rejected`/`null`），
  做「待我验收」收件箱时一次拉到，不用逐条查详情。
- 任务不存在 → 404。

## 9. 错误与状态码

- 任务 `status ∈ pending | running | completed | failed`（见 TaskMeta / 详情）。
- `POST /tasks` 未知 cmd → 404；建成功 → 201。
- 产物路由：不存在 404；非白名单根（只 `state/` 和 `video-jobs/`）或越界 → 403；目录当文件取 → 400。
- 任务执行失败：`GET /tasks/{id}` 的 `status="failed"` + `error` 文本；SSE 也会发 `error` 事件。
