---
id: task-3.2
title: pipeline_runner god-object 拆分
status: In Progress
assignee: []
created_date: '2026-06-19 13:46'
labels:
  - refactor
  - maintainability
dependencies:
  - task-3.1
parent_task_id: task-3
priority: high
---

## Description

`server/pipeline_runner.py` 3172 行、单类 `PipelineRunner` 60+ 方法，混杂：job 状态机（_save/_load/
_emit）、SSE EventBus、跨进程 cancel 文件标记、6 步执行体（_execute_*）、engine 桥接（_execute_via_
engine/_engine_step_inputs）、3 类 agent 后台（enrich/refresh/guiguzi）、rw 模型重生+质检、mock 旁路。
是全仓最大债源（见 CODE-REVIEW §4.2 C1）。

## 关联 Findings
C1（god-object）/ C5（collect_one 235 行 + 5 个超长函数）/ C6（rewrite/refine 复制 5 次）/ A7（/jobs
端点散在 pipelines.py）/ C9（app models.dart 978 行、web HomePage.tsx 896 行，低优）。

## Implementation Plan（分期，不要求一次到位；blast radius 大，属 L2）

1. 抽 3 类 agent 后台任务（enrich/refresh/guiguzi）成独立模块——它们与 015 step 执行正交，最先抽、最安全。
2. engine 桥接（_execute_via_engine/_engine_step_inputs）收进 engine 侧 facade adapter。
3. 随 task-3.1 漂移收敛删 legacy `_execute_*`（待 DEC-2）。
4. 低风险局部：抽 `_assert_known_model` / `_rw_mock_short_circuit` 消 C6 复制（纯抽取，行为不变）。
5. C5：collect_one 等超长函数按子步骤拆 helper。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 agent 后台任务（enrich/refresh/guiguzi）抽出独立模块，pipeline_runner 行数显著下降，586 passed
- [x] #2 C6 复制抽成 helper（`_assert_known_model` + mock 短路），行为不变
- [ ] #3 每步拆分后回归 586 passed、web 全链路 UI 不退化
- [x] #4 拆分边界先对齐再动手（L2，prefer 小步多次）
<!-- AC:END -->

## 完成记录

- 2026-06-22：第一刀先做低风险局部抽取：`rewrite_rw_model` / `refine_rw_model` 共用 `_assert_known_model()` 与 `_rw_mock_short_circuit()`，保留原有错误语义和 mock015 短路行为。
- 验证：`pytest tests/server/test_pipeline_runner.py src/ncds_opus_factory/server/pipeline_runner_events_test.py src/ncds_opus_factory/server/pipelines_sse_test.py -q` 24 passed；`py_compile pipeline_runner.py` 与 `git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 598 passed, 173 warnings。
- 2026-06-22：第二刀以 mixin 机械搬移 agent 后台任务，新增 `server/pipeline_agent_tasks.py`，把 ASR enrich、沈括 refresh、鬼谷子 analyze/generate 后台任务从 `pipeline_runner.py` 拆出；`PipelineRunner` 继承 `PipelineAgentTasksMixin`，public methods 保持不变。
- 行数变化：`pipeline_runner.py` 从 3143 行降到 2779 行；新模块 `pipeline_agent_tasks.py` 393 行。
- 验证：`py_compile pipeline_runner.py pipeline_agent_tasks.py` 通过；focused tests `46 passed`；单独降级用例通过；全量 `.venv/bin/python3 -m pytest -q` 598 passed, 173 warnings。
- 2026-06-22：第三刀新增 `server/pipeline_engine_bridge.py` / `PipelineEngineBridgeMixin`，把 `attach_engine` / `_execute_via_engine` / `_engine_step_inputs` 从 `pipeline_runner.py` 拆出；`PipelineRunner` 继承 bridge + agent mixins，engine strangler 的 public 行为保持不变。
- 行数变化：`pipeline_runner.py` 从 2779 行降到 2707 行；新模块 `pipeline_engine_bridge.py` 88 行。
- 验证：`py_compile pipeline_runner.py pipeline_engine_bridge.py` 通过；focused tests `70 passed`；全量 `.venv/bin/python3 -m pytest -q` 598 passed, 173 warnings。
- 2026-06-22：第四刀处理 C5 的 `commands/shenkuo.collect_one` 超长函数，新增 `_CollectPaths` / `_CollectRun` / manifest helper，把路径规划、legacy 采纳、五条采集分支、manifest 写回拆出；`collect_one` 从约 235 行降到 47 行，public API 与并发编排顺序保持不变。
- 验证：`py_compile shenkuo.py` 通过；沈括 focused tests `23 passed`；pipeline/cancel focused tests `48 passed`；`git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 598 passed, 173 warnings。
- 2026-06-22：第五刀处理 C5 的 `PipelineRunner._execute_rw` 超长函数，新增 `_RwRun` 多模型运行上下文，把模型级状态推送、draft 增量写盘、单模型 run/QC、draft 排序汇总从 `_execute_rw` 拆出；`_execute_rw` 从约 120 行降到 45 行，public 行为和增量推送契约保持不变。
- 验证：`py_compile pipeline_runner.py` 通过；RW focused tests `64 passed`；pipeline/server focused tests `70 passed`；`git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 598 passed, 173 warnings。
- 2026-06-22：第六刀处理 C5 的 `PipelineRunner._execute_image` 超长函数，新增 `_ImageRun` 图片批量生成上下文，把 scene 出场序去重、容器图生成、简笔画生成、计数汇总从 `_execute_image` 拆出；`_execute_image` 从约 140 行降到 15 行，保留原有幂等、部分失败和进度文本语义。
- 验证：`py_compile pipeline_runner.py` 通过；新增 runner image tests，image focused tests `36 passed`；pipeline/server focused tests `73 passed`；`git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 601 passed, 173 warnings。
- 2026-06-22：第七刀处理 C5 的 `PipelineRunner._execute_storyboard` 超长函数，新增 `_StoryboardRun` director 分镜上下文，把 director 输入组装、prompt 构造、opus 调用、scene 回填、episode 写盘和汇总输出从 `_execute_storyboard` 拆出；`_execute_storyboard` 从约 70 行降到 18 行，保留 legacy storyboard 当前无 retry / 无 domain 注入行为。
- 验证：`py_compile pipeline_runner.py` 通过；新增 runner storyboard test，storyboard focused tests `59 passed`；pipeline/server focused tests `96 passed`；`git diff --check` 通过；全量首跑 1 个 order-sensitive `worker_test.py::test_maintenance_extracted` 波动失败，单独复跑和 worker_test 全过；二次全量 `.venv/bin/python3 -m pytest -q` 602 passed, 173 warnings。
- 2026-06-22：第八刀开始做文件尺寸实降，新增 `server/pipeline_image_tasks.py` / `PipelineImageRun`，把第六刀已成型的 image 运行上下文从 `pipeline_runner.py` 移出；`PipelineRunner._execute_image` 通过依赖注入传入 `_generate_scene_image`，避免反向 import 环且保留测试 monkeypatch 语义。
- 行数变化：`pipeline_runner.py` 从 2791 行降到 2635 行；新模块 `pipeline_image_tasks.py` 179 行。
- 验证：`py_compile pipeline_runner.py pipeline_image_tasks.py` 通过；image focused tests `37 passed`；pipeline/server focused tests `74 passed`；`git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 602 passed, 173 warnings。
- 2026-06-22：第九刀继续文件尺寸实降，新增 `server/pipeline_storyboard_tasks.py` / `PipelineStoryboardRun`，把第七刀已成型的 storyboard 运行上下文从 `pipeline_runner.py` 移出；`PipelineRunner._execute_storyboard` 通过依赖注入传入 `_call_opus_for_rw` 与 model id，避免反向 import 环且保留测试 monkeypatch 语义。
- 行数变化：`pipeline_runner.py` 从 2635 行降到 2575 行；新模块 `pipeline_storyboard_tasks.py` 81 行。
- 验证：`py_compile pipeline_runner.py pipeline_storyboard_tasks.py` 通过；storyboard focused tests `59 passed`；pipeline/server focused tests `96 passed`；`git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 602 passed, 173 warnings。
- 2026-06-22：第十刀继续文件尺寸实降，新增 `server/pipeline_rw_tasks.py` / `PipelineRwRun`，把第五刀已成型的 RW 多模型运行上下文从 `pipeline_runner.py` 移出；`PipelineRunner._execute_rw` 通过依赖注入传入 `MODEL_CANDIDATES`、`_ModelUnavailable`、`_invoke_rw_candidate` 与 `_apply_rw_qc`，避免反向 import 环且保留测试 monkeypatch 语义。
- 行数变化：`pipeline_runner.py` 从 2575 行降到 2468 行；新模块 `pipeline_rw_tasks.py` 130 行。
- 验证：`py_compile pipeline_runner.py pipeline_rw_tasks.py test_pipeline_runner.py` 通过；新增 runner RW 注入/写盘回归；RW focused tests `69 passed`；pipeline/server focused tests `103 passed`；`git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 603 passed, 173 warnings。
- 2026-06-22：第十一刀继续文件尺寸实降，新增 `server/pipeline_tts_tasks.py` / `PipelineTtsRun` 与 `server/pipeline_render_tasks.py` / `PipelineRenderRun`，把 legacy fallback 的 TTS 与 render 执行上下文一起移出；runner 通过依赖注入传入 `_run_tts_gen_015`、`_rebuild_tts_items_015` 与 `render_015.run`，保留 engine performer / cancel tests 依赖的共享 helper。
- 行数变化：`pipeline_runner.py` 从 2468 行降到 2420 行；新模块 `pipeline_tts_tasks.py` 65 行，`pipeline_render_tasks.py` 58 行。
- 验证：`py_compile pipeline_runner.py pipeline_tts_tasks.py pipeline_render_tasks.py test_pipeline_runner.py` 通过；新增 runner TTS/render 注入回归；TTS/render focused tests `55 passed`；pipeline/server focused tests `105 passed`；`git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 605 passed, 173 warnings。
- 2026-06-22：第十二刀拆底部 helper，新增 `server/pipeline_media_helpers.py`、`server/pipeline_asr_helpers.py`、`server/pipeline_rw_helpers.py`，把 TTS/ASR subprocess、ffmpeg 抽帧、生图、episode 读取、RW 候选模型调用、RW prompt/source/QC 从 `pipeline_runner.py` 移出；engine performer、mock 与相关测试同步改从 helper 模块取 seam，不再从 runner 拉业务 helper。
- 行数变化：`pipeline_runner.py` 从 2420 行降到 1761 行；新模块 `pipeline_media_helpers.py` 178 行，`pipeline_asr_helpers.py` 113 行，`pipeline_rw_helpers.py` 329 行。
- 验证：`py_compile` 通过；helper/runner/performer focused tests `93 passed`；pipeline/server focused tests `115 passed`；`git diff --check` 通过；全量 `.venv/bin/python3 -m pytest -q` 605 passed, 173 warnings。
- 2026-06-22：第十三刀拆 lines 执行上下文，新增 `server/pipeline_lines_tasks.py` / `PipelineLinesRun`，把 legacy `_execute_lines` 的 draft 校验、opus 调用、JSON 解析、beats 规整、模板 episode 合并和写盘移出；engine performer 同步从 lines 模块复用 `_build_lines_prompt` / `_load_template_episode`，runner 不再承载 lines 业务 helper。
- 行数变化：`pipeline_runner.py` 从 1761 行降到 1650 行；新模块 `pipeline_lines_tasks.py` 151 行。
- 验证：`py_compile` 通过；lines focused tests `72 passed`；pipeline/server focused tests `116 passed`；全量 `.venv/bin/python3 -m pytest -q` 606 passed, 173 warnings。
- 2026-06-22：第十四刀拆 ASR 快采执行上下文，新增 `server/pipeline_asr_tasks.py` / `PipelineAsrCollectRun`，把 `_execute_asr_collect` 的 URL 归集、沈括 collect_one 快采循环、单条失败兜底、实时 `outputs.collected` patch 和 `TaskCancelled` 透传语义移出；runner 只读取 job inputs 并注入 cancel flag / cancellable thread runner。
- 行数变化：`pipeline_runner.py` 从 1650 行降到 1595 行；新模块 `pipeline_asr_tasks.py` 94 行。
- 验证：`py_compile` 通过；ASR/cancel focused tests `38 passed`；pipeline/server focused tests `117 passed`；全量 `.venv/bin/python3 -m pytest -q` 607 passed, 173 warnings。
- 2026-06-22：第十五刀拆 RW/regen 用户操作，新增 `server/pipeline_rw_operations.py` / `PipelineRwOperationsMixin` 与 `server/pipeline_regen_operations.py` / `PipelineRegenOperationsMixin`，把 RW 单模型重写、rubric 优化、选稿、preview 生图重生、image/tts scene 级重生和 mock regen 短路从 runner 移出；`PipelineRunner` 继续暴露同名 public methods 供 routes 使用。
- 行数变化：`pipeline_runner.py` 从 1595 行降到 1093 行；新模块 `pipeline_rw_operations.py` 212 行，`pipeline_regen_operations.py` 284 行。
- 验证：`py_compile` 通过；operation focused tests `50 passed`；pipeline/server focused tests `119 passed`；全量 `.venv/bin/python3 -m pytest -q` 609 passed, 173 warnings。
- 2026-06-22：第十六刀拆 state/events/scheduler 基础设施，新增 `server/pipeline_models.py`、`server/pipeline_events.py`、`server/pipeline_state_store.py`、`server/pipeline_scheduler.py`，把 `NodeState`/`JobState`/`EventBus`、events.jsonl + progress patch、JobState 持久化/public state API、run_node/cancel/watchdog/mock/真实分发状态机移出；`pipeline_runner.py` 保留兼容导出、初始化和各 `_execute_*` 路由入口。
- 行数变化：`pipeline_runner.py` 从 1093 行降到 195 行；新模块 `pipeline_models.py` 60 行，`pipeline_events.py` 101 行，`pipeline_state_store.py` 237 行，`pipeline_scheduler.py` 289 行。
- 验证：`py_compile` 通过；state/events/scheduler focused tests `59 passed`；pipeline/server focused tests `123 passed`；全量 `.venv/bin/python3 -m pytest -q` 609 passed, 173 warnings。
- 2026-06-22：第十七刀检查并去重新拆出的 `pipeline*`：RW draft 写盘/QC 输入改用 `build_rw_draft` 共享；engine lines 复用 `_episode_from_lines_response`；engine tts/image/render performer 改为复用 `PipelineTtsRun` / `PipelineImageRun` / `PipelineRenderRun`；events progress/output patch 收敛到 `_mutate_node_and_emit`；regen real/mock 的 image item patch 收敛到共享 helper。
- 行数变化：`pipeline_runner.py` 维持 195 行；`engine/pipeline_performers_015.py` 从 780 行降到 605 行；本次代码净删 144 行。
- 验证：`ruff check` 与 `git diff --check` 通过；performer/runner/events focused tests `66 passed`；pipeline/server focused tests `123 passed`；全量 `.venv/bin/python3 -m pytest -q` 609 passed, 173 warnings。
