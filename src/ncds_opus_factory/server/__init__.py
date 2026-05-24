"""HTTP 服务包装层。

把 5 个命令（wst/tst/vid/asr/rw）以及未来追加的 tts/render 暴露为
统一的「提交任务 + SSE 拉进度」HTTP 协议，供 daoer 等远端调用方使用。

入口：`nof-server`（pyproject.toml scripts 注册）等同于
`uvicorn ncds_opus_factory.server.app:app --host 0.0.0.0 --port 8810`。
"""
