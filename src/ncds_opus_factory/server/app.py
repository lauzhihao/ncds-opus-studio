"""FastAPI app 入口。

启动：
    nof-server                          # pyproject scripts 注册的命令
    uvicorn ncds_opus_factory.server.app:app --host 0.0.0.0 --port 8810

服务暴露：
    GET  /health
    GET  /tasks
    POST /tasks/{cmd}
    GET  /tasks/{task_id}
    GET  /tasks/{task_id}/events     (SSE)
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ncds_opus_factory.server.routes import tasks as tasks_routes
from ncds_opus_factory.server.state import RUNNER, STATE_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


app = FastAPI(
    title="ncds-opus-studio HTTP server",
    description="5+ commands (wst/tst/vid/asr/rw/tts/render) exposed as async tasks + SSE",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks_routes.router)


@app.get("/health")
async def health_check() -> dict:
    return {
        "status": "ok",
        "state_dir": str(STATE_DIR),
        "commands": RUNNER.list_commands(),
    }


@app.on_event("startup")
async def _startup_log() -> None:
    logger.info(
        "[nof-server] ready. state_dir=%s commands=%s",
        STATE_DIR,
        RUNNER.list_commands(),
    )


def cli_main() -> None:
    """`nof-server` 入口。读取 NOF_SERVER_HOST / NOF_SERVER_PORT 环境变量。"""
    import uvicorn

    host = os.environ.get("NOF_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("NOF_SERVER_PORT", "8810"))
    uvicorn.run(
        "ncds_opus_factory.server.app:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    cli_main()
