# 前端集成指南 · ncds-opus-studio HTTP API

给前端 / app 接手者：端点签名的真源是 `GET /openapi.json` 和 `/docs`（Swagger UI）。本文只补 OpenAPI 表达不了的当前资源边界、SSE 信封、artifact 约定和实际消费者。

## 1. 当前入口关系（2026-07-31）

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

`/studio` 当前通过 `web/src/api/client.ts` 调 `/jobs`。它是作品/内容视角：一个 job 对应一条 final_preview 画布，节点状态、画布位置、episode、预览和本地文件都围绕 `job_id`。

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
| `GET` | `/preview/{job_id}` | final_preview 预览 iframe |

当前 strangler 行为：`NOF_ENGINE_NODES` 未设置时，`lines/storyboard/tts/image/render` 经 facade 走 engine；`asr` 因需要步内增量与后台 enrich，仍走 legacy `_execute_asr_collect`；`rw` 因需要逐模型实时 `model_progress/drafts`，仍走 legacy `_execute_rw`。

`GET /jobs/{job_id}/events` 推的是画布节点事件；客户端断线后应重新 `GET /jobs/{job_id}` 对齐全量状态。

### film domain 契约

film 先由沈括的 `asr` 节点生成 ASR 候选，再通过稀疏全画面采样定位字幕区域，以 0.5fps（每 2 秒 1 帧）对画面下方 ROI 做 OCR。OCR 只负责排除原片对白、验证旁白和附加文本冲突复核建议，不能静默覆盖 ASR，也不能独立新增稿件。每个成功的 `outputs.collected[]` entry 都使用 `film_script_source.v3`，并且 `text` 与 `commentary_script.txt` 完全一致；这只完成沈括的原料提取，画布仍展示并保留鬼谷子、柳永、吴道子、伯牙和卧龙的普通后续生产链，需逐节点操作而不会自动续跑。

`film_source` 固定为：

```json
{
  "mode": "film_script_source",
  "version": 3,
  "profile": "commentary_only",
  "language": "zh-CN",
  "video": "state/works/douyin/123/video.mp4",
  "video_sha256": "...",
  "asr_timeline": "state/works/douyin/123/asr_timeline.json",
  "raw_observations": {"json": ".../v3.raw_observations.json", "count": 702, "backend": "rapidocr-onnxruntime-ppocrv6-tiny", "roi": {"x": 0, "y": 0.7, "width": 1, "height": 0.29}},
  "tracks": {"json": ".../v3.tracks.json", "counts": {"commentary": 420, "film_dialogue": 95, "watermark": 12, "unknown": 175}},
  "commentary_script": {"json": ".../v3.commentary.json", "srt": ".../v3.commentary.srt", "txt": ".../v3.commentary.txt", "report": ".../v3.commentary.report.json", "cue_count": 310, "quality_status": "review", "publishable": false},
  "quality_status": "review",
  "publishable": false,
  "draft_text": "可审核草稿全文..."
}
```

`clean.json` cue 至少有 `cue_id/start_ms/end_ms/text/source_cue_ids/confidence`；`source_cue_ids` 不为空，且相邻 cue 的标准化文本不重复。校正优先 AGY，整批失败后从首批改用 Codex/SCodex，再失败才使用 Opus；全部 backend 不可用时保留确定性 baseline 并标记 `needs_review`。`raw_ocr` 是不可覆盖的观测产物。
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

当前 `RECIPE_REGISTRY` 注册：

- `final_preview`：既有 web 成片链。
- `film_highlight_v1`：消费人工确认的 highlight plan/EDL，用干净原片生成短版；它独立于影视字幕采集脚本。

两条 film recipe 当前是 backend walking skeleton，尚未接入 web/app UI。driver 必须在
`script_review`（仅完整版）、`highlight_plan`（仅短版）、`edl_review`、`voice_review`
处调用 approve；自动原片视觉匹配、在线 TTS/voice cloning、远端 GPU 调度不在 v1。

### film rebuild step 契约

| step | 必填 `step_inputs` | 关键输出 |
|---|---|---|
| `source` | `job_dir, reference_path, master_path`；可选 `master_audio_stream`（audio ordinal，默认 0） | `source_manifest_path, reference_asset, master_asset, artifacts` |
| `storyboard` | `job_dir, source_manifest_path, edl_path`；可选 `profile` | `edl_manifest_path, edl_artifact, frame_count, duration_seconds, qa` |
| `tts` | `job_dir, voice_path, edl_manifest_path`；可选 `subtitle_path` | `voice_manifest_path, voice_artifact, subtitle_artifact` |
| `render` | `job_dir, source_manifest_path, edl_manifest_path, voice_manifest_path`；可选 `render_profile` | `render_manifest_path, render_artifact, output_path, expected_frames` |
| `quality` | `job_dir, render_manifest_path` | `qa_report_path, qa_artifact, status, checks, warnings` |

`storyboard` 接受旧 `edit_decision_list[].source_{start,end}_ms` 或
`segments[].source_{start,end}_frame`，统一写成 `film_frame_edl.v1`：fps 使用
`{numerator, denominator}`，帧区间严格为半开 `[start, end)`。非单调剧情剪辑不会被一概拒绝；
没有 `intentional` 标记的 backward/overlap、低置信匹配及超过 profile 的单段会进入 `qa.status=review`。

每个 film artifact ref 至少包含：

```json
{
  "artifact_id": "a_...",
  "kind": "film_frame_edl",
  "schema_version": "artifact_ref.v1",
  "uri": "film_rebuild/storyboard/frame_edl.json",
  "sha256": "...",
  "size_bytes": 1234,
  "producer_step": "storyboard",
  "producer_version": "film_rebuild_mvp.v1",
  "input_artifact_ids": ["a_reference", "a_master", "a_edl_input"],
  "metadata": {}
}
```

`uri` 相对 `job_dir`，manifest 可随 job 目录重定位；source 输入在 job 外时保存相对引用，迁移
job 时调用方需保持输入资产的相对布局或重新绑定。render 只从 `film_master` 取画面和原声音轨，
不会从 `film_reference` 取 bed。多音轨 master 把选中的 audio ordinal/codec/language 固化在
master artifact metadata，render 严格复用该 ordinal。ASS/SRT 默认尝试烧录；本机 FFmpeg 没有
libass `subtitles` filter 时保留 subtitle artifact 并返回 warning，调用方可设置
`render_profile.require_burned_subtitles=true` 将其升级为硬失败。输出 MP4 只保留成片 video/audio；
原片 metadata、chapter、subtitle、timecode/data stream 不继承到成片。

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
