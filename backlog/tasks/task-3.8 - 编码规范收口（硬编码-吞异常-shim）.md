---
id: task-3.8
title: 编码规范收口（硬编码/吞异常/asr config-lie/shim）
status: To Do
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
   import（已核：`commands/__init__.py` 不引用、PRIMITIVE_REGISTRY 直接来自 core）。→ 可删；render_015/
   tts shim 有活调用方需先改直连 core。
6. **低优**：`Dockerfile:33` pip→注明 deprecated；`edit-server.py`→`edit_server.py`（查引用后改）。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 模型名/超时抽常量收口，586 passed
- [ ] #2 gpt_image 输出根统一常量（env 可覆盖、默认值不变），586 passed
- [ ] #3 dev_proxy 吞异常补 debug 日志
- [ ] #4 asr config-lie：_all 去 "asr" + 修注释（行为零变化，跑测验证）
- [ ] #5 删 wst/tst/vid/render 四 shim（确认零引用后），586 passed
<!-- AC:END -->
