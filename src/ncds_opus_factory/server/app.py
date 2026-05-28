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

from pathlib import Path

# 在 import 任何读 os.environ 的模块之前先加载 .env —— 比如 commands/tts.py 顶层就要
# DASHSCOPE_API_KEY，pipeline_runner 也要 GPT_IMAGE2_*；放在最早处确保下游全部 import
# 都能拿到。仓库根的 .env 已在 .gitignore，不会进版本库。
_REPO_ROOT = Path(__file__).resolve().parents[3]
try:
    from dotenv import load_dotenv  # python-dotenv 已装
    load_dotenv(_REPO_ROOT / ".env", override=False)
except ImportError:
    # 没装 python-dotenv 也别炸 —— shell env 已经 export 的话同样工作
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ncds_opus_factory.server.routes import jobs as jobs_routes
from ncds_opus_factory.server.routes import pipelines as pipelines_routes
from ncds_opus_factory.server.routes import preview as preview_routes
from ncds_opus_factory.server.routes import tasks as tasks_routes
from ncds_opus_factory.server.routes import templates as templates_routes
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
app.include_router(templates_routes.router)
app.include_router(jobs_routes.router)
app.include_router(pipelines_routes.router)
app.include_router(preview_routes.router)


# ---------------------------------------------------------------------------
# Studio SPA：把 web/dist 挂到 /studio。
# - dev (NOF_DEV=1)：反代到 vite dev server，HMR WebSocket 同走 :8810 → 单端口体验
# - prod：访问 /studio/* → 静态文件；SPA 路由由前端 BrowserRouter 处理
# ---------------------------------------------------------------------------

_STUDIO_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
_DEV_MODE = os.environ.get("NOF_DEV") == "1"

if _DEV_MODE:
    from ncds_opus_factory.server.dev_proxy import build_router as _build_dev_proxy

    app.include_router(_build_dev_proxy())
    logger.info("[nof-server] NOF_DEV=1 → /studio/* proxied to vite dev server")
elif _STUDIO_DIST.exists():
    # /studio/assets/* 静态资源（vite 产物含 hash）
    app.mount(
        "/studio/assets",
        StaticFiles(directory=_STUDIO_DIST / "assets"),
        name="studio-assets",
    )

    @app.get("/studio")
    @app.get("/studio/")
    async def studio_root() -> FileResponse:
        return FileResponse(_STUDIO_DIST / "index.html")

    # SPA fallback：所有 /studio/<深层路径> 都返回 index.html，由前端路由解析
    @app.get("/studio/{full_path:path}")
    async def studio_spa(full_path: str) -> FileResponse:  # noqa: ARG001
        return FileResponse(_STUDIO_DIST / "index.html")
else:
    logger.info("[nof-server] web/dist not built; /studio not mounted")


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
