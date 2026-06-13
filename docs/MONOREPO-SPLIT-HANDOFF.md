# P1 续作交接（monorepo 拆分 · 给接手 agent 的 runbook）

> ⛔ **方向已转（2026-06-13）**：三包对等拆分作废，§3 的 P2 交底**不再执行**。权威设计转
> **[PRODUCTION-ENGINE-DESIGN.md](PRODUCTION-ENGINE-DESIGN.md)**（统一生产引擎 + 两视图）。
> 本文档 §0–§2 仍是 P1（已完成、300/0）的真实 as-built 记录，保留参考。

> 状态：**2026-06-13。P1 全部完成并 committed（P0 + P1.1–P1.7 + P1.x），全量 300 passed / 0 failed。**
> P1.5 / P1.6 / P1.7 / P1.x 已落地（见 §2 各项 ✅ + commit）；另把 boya 的 `amix normalize`
> 老 ffmpeg 兼容修掉，基线从「298 passed + 2 boya 预存失败」推进到「**300 passed / 0 failed**」。
> 下一期是 **P2（factory 物理迁入 `packages/factory/` + AGENT 层归位）**，见 [设计 §5](MONOREPO-SPLIT-DESIGN.md)。
> 权威设计：[MONOREPO-SPLIT-DESIGN.md](MONOREPO-SPLIT-DESIGN.md)——**以 §9.4 的 as-built 纠正为准**
> （§1/§2 的原始边界里"asr/rw=core"已被 §9.4 推翻）。路径清单：[MONOREPO-SPLIT-PATHS.md](MONOREPO-SPLIT-PATHS.md)。

## 0. 当前状态

`ncds_opus_core` 已成形（纯净、零反向依赖）：
```
packages/core/src/ncds_opus_core/
├── common/    node_runtime·tts_provider·public_upload·lark_cli·cancel·paths.repo_root()
├── gpt_image/ 4 网关 + script_path()
├── templates/ paper_card_talk_015 + template_dir()/templates_root()
└── commands/  wst·tst·vid·tts·render·render_015  + render_runner.mjs
packages/core/package.json   # puppeteer-core/puppeteer-screen-recorder（仅 render_runner 需要）
```
老位置 `src/ncds_opus_factory/...` 全部留了 **sys.modules 转发 shim**，所以 factory 的现有
import 与 `COMMAND_REGISTRY` 不变、照常工作（P5 才清 shim）。

## 1. 执行套路（每个增量都照这个走，前 7 个 commit 是范例）

1. **`git mv`** 模块到 core 目标位置（保留 git 历史）。
2. **老位置写 shim**（统一模板，对所有 import 形式透明）：
   ```python
   """DEPRECATED 转发 shim（P1.x）：已迁至 ncds_opus_core.<...>。P5 删。"""
   import sys as _sys
   from ncds_opus_core.<...> import <mod> as _mod
   _sys.modules[__name__] = _mod
   ```
3. **改 core 内模块的 import**：凡 `from ncds_opus_factory.*` 一律改成 `ncds_opus_core.*`
   （否则 core→factory 反向依赖，违规）。
4. **repoint 外部消费者**：用 core 的定位器（`script_path`/`template_dir`/`repo_root` 等），
   **不要逐处 +1 改 `parents[N]`**。
5. **重装 core**（注册新子模块/资源）：
   ```bash
   uv pip install --python .venv/bin/python3 -e packages/core
   ```
6. **验证**：① core 纯净 `grep -rnE "^\s*(from|import)\s+ncds_opus_factory" packages/core/src` **必须空**；
   ② 双路径 import（core 直 import + factory shim 指向同一对象）；③ `COMMAND_REGISTRY` 完整。
7. **全量测试绿**：`.venv/bin/python3 -m pytest -q` → **300 passed / 0 failed**。
   （此前的「2 个 boya 预存失败」已修：`boya._mix_audio` 现检测 amix 是否支持 `normalize`，
   老 ffmpeg 4.1 走 `,volume=N` 等价兜底。基线现为全绿——passed 掉数或冒新失败都算回归。）
8. **单独 commit**（中文 message + `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`）。

不变式：`grep` core 纯净必须空；studio 与 factory 互不 import；venv 装包只用 `uv pip install
--python .venv/bin/python3`（**不要** `uv sync`，会清掉 whisper 等重依赖）。

## 2. P1 余下增量（精确 spec · **全部已落地**）

> ✅ **四项均已完成并 committed**（2026-06-13）。下面 spec 保留作 **as-built 记录**；实施时实测
> 纠正了几处消费者清单（见各项「实测纠正」），以纠正为准。
> - **P1.6** `9dce05c` · **P1.5** `89616d2` · **P1.7** `fdc9f2c` · **P1.x** `822ca72`

### ✅ P1.6 收尾 —— rewrite 引擎进 core（**只引擎，不碰命令**）`9dce05c`
- **搬这 4 个**到 `packages/core/src/ncds_opus_core/runners/`（新建 `runners/__init__.py` +
  `runner_path(name)` 定位器，仿 `gpt_image/__init__.py:script_path`）：
  `scripts/{content_rewrite_runner,video_rewrite_runner,rewrite_profiles,douyin_cog_kernel}.mjs`。
  它们的内部相对 import（`./video_rewrite_runner.mjs` 等）**一起搬就仍有效**（同目录）。
- **不要搬**：`asr.py/rw.py` 命令、`asr_command_runner.mjs`、`rewrite_command_runner.mjs`、
  `video_job_worker.mjs`、`feishu_sdk_adapter.mjs`、`lark_cli.mjs`——**全留 factory**（§9.4）。
  **feishu 切尾已作废**（rewrite_command_runner 留 factory，feishu→factory 合法）。
- **消费者**（⚠️ **实测纠正：实为 3 个，非本节原写的 2 个**）：
  - ~~studio `pipeline_runner._execute_rw` spawn `content_rewrite_runner.mjs`~~ —— **stale，已非消费者**：
    studio rw 节点早改内联 4 模型并行（`pipeline_runner.py:1902` 注释「替代旧 content_rewrite_runner.mjs 路径」），不再 spawn 本链。
  - **factory `commands/liuyong.py:27`**（原文漏列）：spawn-by-path → 改用 `ncds_opus_core.runners.runner_path("content_rewrite_runner.mjs")`。
  - **factory `scripts/video_job_worker.mjs:23`**（原文漏列）：跨包 ESM import `video_rewrite_runner.mjs` → 同下相对路径。
  - **factory**：`scripts/rewrite_command_runner.mjs:18` `import { runContentRewrite } from
    './content_rewrite_runner.mjs'` —— 跨包 ESM import。**唯一开放小项**，二选一：
    (a) 相对路径 `../packages/core/src/ncds_opus_core/runners/content_rewrite_runner.mjs`
    （能用、ugly、依赖 monorepo 布局）；(b) npm workspace 让 core runners 按包名解析（更正、要配
    workspace）。建议先 (a) 打通、P5 升 (b)。
- **`.mjs` 测试**：`scripts/*rewrite*.test.mjs` 是 Node 测试（pytest 不跑）。搬完用
  `node --check <file>` 验语法；能跑 `node --test` 更好。

### ✅ P1.5 —— pipelines（DAG 类型）进 core（简单）`89616d2`
> 实测纠正：消费者实为 **3 个**，原文漏列 `server/routes/pipelines.py:48`（除 `pipeline_runner.py` 与 `mock.py`）。老位置留 package shim（`_sys.modules` 交换）。
- **搬** `src/ncds_opus_factory/pipelines/`（`__init__.py`+`types.py`+`paper_card_talk_015.py`）
  → `packages/core/src/ncds_opus_core/pipelines/`。
- ⚠️ **别碰 repo 根 `pipelines/douyin_processing/`**——**零消费者**（已 grep 确认），是删除候选、
  与本增量无关。
- **消费者**：`pipeline_runner.py:37` `from ncds_opus_factory.pipelines import
  PIPELINE_REGISTRY, PipelineDef, get_pipeline`、`server/mock.py` `pipelines.get_pipeline`
  → 改指 `ncds_opus_core.pipelines`（或留 shim 自动转发）。

### ✅ P1.7 —— skills 路径修复（**不搬 skills/**）`fdc9f2c`
- skills/ 是含 SKILL.md 的混合大杂烩（10 子目录），**不进任何包、留 repo 根**（§9.4 + 用户裁定）。
- P1.7 = 把 `parents[N]/skills/...` 改成 `repo_root()/"skills"/...` + `NOF_VIDEO_PIPELINE_SCRIPT`
  env 兜底。主要 1 处（studio）：`pipeline_runner.py:1207`
  `pipeline_script = repo_root / "skills" / "video-pipeline" / "scripts" / "video_pipeline.py"`
  （这里的 `repo_root` 是局部 `parents[3]`，改用 `ncds_opus_core.common.paths.repo_root()`）。
  factory 侧 `video_job_worker.mjs:53` 等留 factory、本期不必动（它在 factory 内 parents 仍对）。

### ✅ P1.x —— registry / schemas / cli 拆分（6 primitive 全进 core 后做）`822ca72`
> 落地：core `PRIMITIVE_REGISTRY/SCHEMAS`(6) + `nof-core` 入口；factory `AGENT_REGISTRY`(8=6 agent+asr+rw)
> + `build_full_registry()` + `get_schema` merge 全集；`COMMAND_REGISTRY/SCHEMAS` 留向后兼容别名。
> 关键判断：schema 二分**按 §9.4 归属切（core 严格 6）、不按 `group` 字段**（asr/rw 的 `group="primitive"` 是 UI 语义，命令归 factory）。
- `PRIMITIVE_REGISTRY`（core）= **6 个**：wst/tst/vid/tts/render/render_015（**asr/rw 不在内**，
  它们是 factory 命令）。
- `AGENT_REGISTRY`（factory）= guiguzi/liuyong/wudaozi/boya/shenkuo/wolong **+ asr + rw**。
- factory 的 `commands/__init__.py` 提供 `build_full_registry()` = merge core PRIMITIVE + factory
  AGENT，喂给 `state.py` 的 RUNNER（口径不变）。
- `command_schemas.py` 按 group 二分（PRIMITIVE→core / AGENT→factory）；`cli.py` 同理。
- 详见 [MONOREPO-SPLIT-DESIGN.md](MONOREPO-SPLIT-DESIGN.md) §2 bridge 表。

## 3. P2 执行交底（拆 factory · 给接手 agent）

> 决策已定，见 [设计 §9.6](MONOREPO-SPLIT-DESIGN.md)：**全拆 + shim**、`reading_confidence` 已删、
> `AGENT_REGISTRY/SCHEMAS` 已在 P1.x 建好（只随包搬）。下面是落地细节与必须先定的机制点。

### 3.0 ⚠️ 先定机制：同名包跨两目录的冲突（P2 第一步，别照搬 P1 shim）
P1 的 shim 能用，是因为模块**迁到了另一个包名**（`ncds_opus_core`），老位置 `ncds_opus_factory.X`
转发到 `ncds_opus_core.X`。**P2 不一样**：factory-only 模块迁后**仍叫 `ncds_opus_factory.*`**，
而 studio/bridge 还留在 `src/ncds_opus_factory/`——**同一个 import 名 `ncds_opus_factory` 会同时存在于
`src/` 和 `packages/factory/src/` 两处**。两个 editable 包同名会在 site-packages 互相覆盖，**不能裸装**。
三选一（**建议 A**）：
- **A（推荐）PEP 420 namespace**：把 `ncds_opus_factory` 改成 namespace 包（删顶层
  `src/ncds_opus_factory/__init__.py` 或声明 namespace），两个目录都进 `sys.path`，子模块按目录合并解析——
  `ncds_opus_factory.commands`(packages/factory) 与 `ncds_opus_factory.server.pipeline_runner`(src/) 共存。
  渐进拆分天然支持；P5 studio/bridge 也迁走后回收 namespace。**先 spike 验证 import 解析再铺开**。
- **B 一次性全挪**：P2 把**整棵** `src/ncds_opus_factory/`（含 studio/bridge）挪进 `packages/factory/src/`，
  P3 再把 studio 从 packages/factory **抽出**到 packages/studio。单一位置、无同名冲突，但 P2 动的文件面更大。
- **C 暂不物理拆**：P2 只做逻辑归位（已在 P1.x 完成），物理移动推迟到 P3/P4 一次做。最稳但 P2 近乎空转。

> ⚠️ 这一步没定之前别 `git mv`。它决定了后面所有「迁往」路径是否成立。

### 3.1 模块归期分配表（P2 只搬 factory-only；studio→P3、bridge→P4 不碰）
`src/ncds_opus_factory/server/` 一棵树混了三类，**P2 只动 factory-only**：

| 模块 | 端 | 期 | 依赖单例 | P2 动作 |
|---|---|---|---|---|
| `task_runner` `task_store` `rounds_gate` `label_store` `planner` `retro_trigger` `subscriptions` `mock_agents` `schemas` | factory | **P2** | RUNNER/STORE/LABELS | 迁 `packages/factory/.../server/` |
| `command_schemas.py` | factory | 已 P1.x | — | 已 import core PRIMITIVE_SCHEMAS，随包搬 |
| `pipeline_runner` `storyboard_director` `mock` `dev_proxy` | studio | P3 | PIPELINE_RUNNER | **不碰** |
| `app.py` `state.py` `artifacts.py` | **bridge** | P4 | both | **不碰**（`state.py:16,34` 仍 import pipeline_runner、建 PIPELINE_RUNNER——这正是 P2 退出标准只核「包内」的原因）|

`routes/`（12 个）：

| route | 端 | 期 | 依赖单例 |
|---|---|---|---|
| `commands` `tasks` `rounds` `subscriptions` | factory | **P2** | RUNNER/STORE/LABELS |
| `jobs` `pipelines` `preview` `templates` `mock` | studio | P3 | PIPELINE_RUNNER/VIDEO_JOBS_DIR |
| `artifacts` | bridge(三分) | P4 | both 根（共用 core `artifact_url`）|

### 3.2 路径改法（迁包时一起做，别留 parents[3]）
factory 迁 `packages/factory/src` 后比根布局**多 2 层**，所有 `parents[3]` 断。统一改法 =
`from ncds_opus_core.common.paths import repo_root`（P1.7 已示范，factory 直接复用、勿重造），
state 产物根强制 `NOF_STATE_DIR` fail-fast。**逐文件清单见 [PATHS.md §1/§2](MONOREPO-SPLIT-PATHS.md)**
（已把误标「asr/rw→core」的两行更正为 factory）。重点：
- `commands/{shenkuo,wolong,wolong_rounds,guiguzi,prescreen,boya,wudaozi,liuyong}.py`、
  `common/{round_store,topic_store,rubric_store,tikhub_client}.py`、`server/{planner}.py`：parents[3]→`repo_root()`/env。
- `wudaozi.py:33-34` 硬编码字面 `src/ncds_opus_factory/templates/{figure_talk,stickman}` → `importlib.resources` 取包内资源。
- skills 路径（shenkuo `skills/tingwu-asr`、`video_job_worker.mjs` `skills/video-pipeline`）：skills/ 留 repo 根，改 `repo_root()/"skills"/...` + env（同 P1.7）。

### 3.3 入口 / workspace / lock（P2 末尾）
`nof`/`nof-server` 入口随 factory 迁入 `packages/factory/pyproject.toml`；根 `pyproject` 退化为 workspace
协调器；`[tool.uv.workspace] members` 显式列 `["packages/core","packages/studio","packages/factory"]`；
顺带定 `uv.lock` 入库（现被 `.gitignore` 忽略）。

### 3.4 退出标准（修正版）
factory 独立 import；**`grep` studio 名(`pipeline_runner`/`storyboard_director`)在 `packages/factory/src` 包内为空**
（不是 `factory/` 字面——bridge `state.py` 留根包到 P4）；`grep ncds_opus_factory packages/core/src` 空；
factory 仅单向依赖 core；**pytest 全绿（基线 300/0）**。

## 4. 容易踩的坑
- core 内 `from ncds_opus_factory.*` = 违规，每个增量后 grep 核一遍。
- 加了 core 子模块/子包后**必须重装 core** 才 importable。
- `*_test.py` 暂留原位（经 shim 仍绿），测试迁移是 P5 的独立小活，别在搬代码时顺手挪（会牵动
  pytest 跨包发现）。
- 渲染相关需要 `node_modules`（`npm install` 落 `packages/core/`），但 pytest 不跑渲染，**测试不受影响**。
