"""FastAPI app 入口。

启动：
    nof-server                          # pyproject scripts 注册的命令
    uvicorn ncds_opus_factory.server.app:app --host 0.0.0.0 --port 8810

服务暴露（详见各 routes/*.py）：
    GET  /health
    commands : GET /commands, GET /commands/{cmd}/schema
    tasks    : GET/POST /tasks, GET /tasks/{id}, GET /tasks/{id}/events(SSE), POST /tasks/{id}/review
    instances: GET/POST /instances, GET /instances/{id}(+/runnable/events SSE), POST .../steps/{sid}/{run,approve,reset}, POST .../finalize
    jobs     : /jobs 及 nodes/* 节点操作（routes/pipelines.py + routes/jobs.py），SSE /jobs/{id}/events
    pipelines: GET /pipelines, GET /pipelines/{id}, GET /pipelines/{id}/cover
    templates: GET /templates, GET /templates/{name}/episode.json
    preview  : GET /preview/{job_id}/* 合成预览 + 编辑写盘端点
    materials: GET /materials（素材索引占位；未来接数据库/向量库 + 对象存储）
    artifacts: GET /artifacts/{dir,files}/{relpath}（白名单产物服务，移动端审看）
    mock     : POST /mock/ensure
    auth     : GET /api/auth/me, GET /api/auth/google/login|callback, POST /api/auth/logout
    studio   : /studio SPA（prod 静态 web/dist；NOF_DEV=1 反代 vite）
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ncds_opus_factory.server.auth import current_user, should_require_auth
from ncds_opus_factory.server.routes import accounts as accounts_routes
from ncds_opus_factory.server.routes import artifacts as artifacts_routes
from ncds_opus_factory.server.routes import auth as auth_routes
from ncds_opus_factory.server.routes import commands as commands_routes
from ncds_opus_factory.server.routes import instances as instances_routes
from ncds_opus_factory.server.routes import jobs as jobs_routes
from ncds_opus_factory.server.routes import materials as materials_routes
from ncds_opus_factory.server.routes import mock as mock_routes
from ncds_opus_factory.server.routes import pipelines as pipelines_routes
from ncds_opus_factory.server.routes import preview as preview_routes
from ncds_opus_factory.server.routes import rounds as rounds_routes
from ncds_opus_factory.server.routes import subscriptions as subscriptions_routes
from ncds_opus_factory.server.routes import tasks as tasks_routes
from ncds_opus_factory.server.routes import templates as templates_routes
from ncds_opus_factory.server.routes import works as works_routes
from ncds_opus_factory.server.maintenance import (
    backfill_labels_once,  # re-export: 保持对外接口兼容（test_labels 经 app_mod 调）
    sweep_cron_once,       # re-export: 同上
    sweep_discarded_once,  # re-export: 同上
)
from ncds_opus_factory.server.state import AUTH_CONFIG, AUTH_STORE, LABELS, RUNNER, STATE_DIR, STORE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def _startup_log() -> None:
    # S3 步6（切换点）：8810 已瘦身为纯 producer + serve。
    # 不再在此做 recover_and_start / 起 loop / 挂 on_terminal——
    # 所有 worker 职责（任务执行、订阅/retro/planner/discard loop）已移入 nof-worker 进程。
    # 8810 只负责：HTTP 路由 / SSE 文件 tail / POST 入队（lpush）/ GET serve 状态。
    logger.info(
        "[nof-server] ready. state_dir=%s commands=%s auth_enabled=%s",
        STATE_DIR,
        RUNNER.list_commands(),
        AUTH_CONFIG.enabled,
    )


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _startup_log()
    yield


app = FastAPI(
    title="ncds-opus-studio HTTP server",
    description="5+ commands (wst/tst/vid/asr/rw/tts/render) exposed as async tasks + SSE",
    version="0.1.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """配置 Google OAuth 后拦截业务 API；未配置时全站放行（本地开发默认）。"""
    user = current_user(AUTH_STORE, request) if AUTH_CONFIG.enabled else None
    request.state.auth_user = user
    if should_require_auth(request.url.path, request.method, AUTH_CONFIG):
        if user is None:
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return await call_next(request)


app.include_router(auth_routes.router)
app.include_router(commands_routes.router)
app.include_router(accounts_routes.router)
app.include_router(tasks_routes.router)
app.include_router(instances_routes.router)
app.include_router(subscriptions_routes.router)
app.include_router(rounds_routes.router)
app.include_router(templates_routes.router)
app.include_router(jobs_routes.router)
app.include_router(artifacts_routes.router)
app.include_router(pipelines_routes.router)
app.include_router(preview_routes.router)
app.include_router(mock_routes.router)
app.include_router(works_routes.router)
app.include_router(materials_routes.router)


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

    # SPA fallback：真实静态文件（如 public/neng.png）优先，否则 index.html
    @app.get("/studio/{full_path:path}")
    async def studio_spa(full_path: str) -> FileResponse:
        candidate = (_STUDIO_DIST / full_path).resolve()
        try:
            candidate.relative_to(_STUDIO_DIST.resolve())
        except ValueError:
            return FileResponse(_STUDIO_DIST / "index.html")
        if candidate.is_file():
            return FileResponse(candidate)
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
