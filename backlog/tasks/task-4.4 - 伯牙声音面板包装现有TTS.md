---
id: task-4.4
title: 伯牙声音面板包装现有 TTS
status: To Do
assignee: []
created_date: '2026-06-23'
labels:
  - frontend
  - pipeline
  - boya
dependencies:
  - task-4.1
parent_task_id: task-4
priority: high
---

## Description

伯牙第一版负责"声音"，但不复用旧 `commands/boya.py` 冷链。web 端应把当前 `tts` 节点包装成伯牙面板：展示配音状态、音频结果、失败重试、完成后进入画面资产补齐/成片流程。

建议实现方向：

- 复用现有 `TtsResultPanel` 或其内部能力，外层以伯牙命名、职责、状态文案呈现。
- 面板主信息围绕声音结果：生成中、成功、失败、重试、音频预览/下载。
- 伯牙成功后，如果当前 DAG 仍要求 `image` 在 `tts` 之后执行，应给前端状态推进提供明确信号：下一步是补齐吴道子画面资产或自动继续生成画面。
- 不在第一版加入 BGM、SFX、多音色编排、音乐版权库等新能力。

## Non-goals

- 不调用旧 `commands/boya.py`。
- 不新增独立 audio library 扫描链路。
- 不改 TTS provider、voice catalog 或音频合成底层参数，除非现有面板展示必须读取。

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 web 端伯牙节点打开后显示声音生产面板，而不是泛化技术节点面板
- [ ] #2 当前 `tts` 节点 running/succeeded/failed/retry 状态都能正确呈现
- [ ] #3 TTS 成功后能预览或下载主要音频产物；没有产物时给出明确空态
- [ ] #4 伯牙完成后，用户能明确进入下一步：补齐吴道子画面资产或进入 render 前检查
- [ ] #5 代码中不新增对旧 `commands/boya.py`、旧 audio_lib 扫描或 figure_talk 音频床的依赖
<!-- AC:END -->
