---
id: task-3.8
title: 编码规范收口（硬编码/吞异常/asr config-lie/shim）
status: Done
assignee: []
created_date: '2026-06-19 13:46'
labels:
  - convention
  - hygiene
dependencies: []
parent_task_id: task-3
priority: low
---

## Description

一批低风险、可安全自动修复的零散卫生项（见 CODE-REVIEW §4.2/§4.3）：

1. **硬编码模型名/超时**（C4）：`claude-opus-4-8` 字面量散在 `pipeline_runner.py:2237/2338/2605/2950`；
   超时 3600/900/600/60 手写。→ 模型名引 `MODEL_CANDIDATES`/`opus_cli` 常量；超时抽具名常量
   `RW_LLM_TIMEOUT_SEC`/`ASR_PROC_TIMEOUT_SEC`。
2. **/tmp/gpt-image 硬编码**（C7）：6+ 处各自定义 DEFAULT_OUTPUT_ROOT。→ core 内统一
   `GPT_IMAGE_OUTPUT_ROOT`（env 可覆盖，默认 `/tmp/gpt-image`），范式参考 `render.py:31` 的
   `os.environ.get("NOF_RENDER_NODE_PATH", ...)`。
3. **dev_proxy.py 3 处吞异常**（C8）：`:116/155/159` `except Exception: pass` → 至少 `logging.debug`。
   （pipeline_runner:3033 的宽吞已在审查中修复。）
4. **asr config-lie**：`pipeline_runner.py:149` 默认 `_all` 含 "asr"，但 `:1306` 用 `!= "asr"` 一票否决
   → asr 永走 legacy。注释（:144-148）与实际分叉判据不一致。→ 从 `_all` 移除 "asr" + 修注释
   （**行为零变化**，asr 本就被 1306 强制 legacy）。
5. **factory primitive shim**（D3）：`commands/{wst,tst,vid,render}.py` 自标"P5 清理时删除"，生产侧零
   import（已核：`commands/__init__.py` 不引用、PRIMITIVE_REGISTRY 直接来自 core）。→ 可删；render_final_preview/
   tts shim 有活调用方需先改直连 core。
6. **低优**：`Dockerfile:33` pip→注明 deprecated；`edit-server.py`→`edit_server.py`（查引用后改）。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 模型名/超时抽常量收口，587 passed
- [x] #2 gpt_image 输出根统一常量（env 可覆盖、默认值不变），587 passed
- [x] #3 dev_proxy 吞异常补 debug 日志
- [x] #4 asr config-lie：_all 去 "asr" + 修注释（行为零变化，跑测验证）
- [x] #5 删 wst/tst/vid/render 四 shim（确认零引用后），587 passed
<!-- AC:END -->

## 完成记录

- 2026-06-22：`pipeline_runner.py` 收口 Opus 默认模型与 ASR/RW/TTS/image 超时常量；默认 engine 节点集合移除 `asr` 并修正文档注释，行为保持 asr legacy。
- 2026-06-22：新增 `ncds_opus_core.gpt_image.paths.GPT_IMAGE_OUTPUT_ROOT`，统一 generate/edit 与 web image 节点输出根；默认 `/tmp/gpt-image`，可用 `NOF_GPT_IMAGE_OUTPUT_DIR` 覆盖。
- 2026-06-22：`dev_proxy.py` websocket close 清理吞异常改为 `logger.debug`；删除零活引用的 factory primitive shim：`wst.py`、`tst.py`、`vid.py`、`render.py`。
- 额外修复：恢复调度的真实 task_id 白名单兼容当前 `<cmd>_<ms><hex>` 形态，避免 `TaskRunner.recover_and_start()` 跳过真实 pending/running；测试中补齐当前第三个 guiguzi/final_preview 候选模型的 stub/断言，避免误触外部 CLI 或绑定过时双模型假设。
- 验证：`python3 -m py_compile ...`、针对性 `pytest`、`git diff --check`、全量 `.venv/bin/python3 -m pytest -q` 均通过；全量结果 `587 passed, 173 warnings in 23.52s`。
