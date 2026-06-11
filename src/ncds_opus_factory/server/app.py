"""FastAPI app 入口。

启动：
    nof-server                          # pyproject scripts 注册的命令
    uvicorn ncds_opus_factory.server.app:app --host 0.0.0.0 --port 8810

服务暴露（详见各 routes/*.py）：
    GET  /health
    commands : GET /commands, GET /commands/{cmd}/schema
    tasks    : GET/POST /tasks, GET /tasks/{id}, GET /tasks/{id}/events(SSE), POST /tasks/{id}/review
    jobs     : /jobs 及 nodes/* 节点操作（routes/pipelines.py + routes/jobs.py），SSE /jobs/{id}/events
    pipelines: GET /pipelines, GET /pipelines/{id}, GET /pipelines/{id}/cover
    templates: GET /templates, GET /templates/{name}/episode.json
    preview  : GET /preview/{job_id}/* 合成预览 + 编辑写盘端点
    artifacts: GET /artifacts/{dir,files}/{relpath}（白名单产物服务，移动端审看）
    mock     : POST /mock/ensure
    studio   : /studio SPA（prod 静态 web/dist；NOF_DEV=1 反代 vite）
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

from ncds_opus_factory.server.routes import artifacts as artifacts_routes
from ncds_opus_factory.server.routes import commands as commands_routes
from ncds_opus_factory.server.routes import jobs as jobs_routes
from ncds_opus_factory.server.routes import mock as mock_routes
from ncds_opus_factory.server.routes import pipelines as pipelines_routes
from ncds_opus_factory.server.routes import preview as preview_routes
from ncds_opus_factory.server.routes import subscriptions as subscriptions_routes
from ncds_opus_factory.server.routes import tasks as tasks_routes
from ncds_opus_factory.server.routes import templates as templates_routes
from ncds_opus_factory.server.state import LABELS, RUNNER, STATE_DIR, STORE
from ncds_opus_factory.server.subscriptions import subscription_loop, subscriptions_path

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

app.include_router(commands_routes.router)
app.include_router(tasks_routes.router)
app.include_router(subscriptions_routes.router)
app.include_router(templates_routes.router)
app.include_router(jobs_routes.router)
app.include_router(artifacts_routes.router)
app.include_router(pipelines_routes.router)
app.include_router(preview_routes.router)
app.include_router(mock_routes.router)


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


# ---- 弃用素材定时清除 ----
# 沈括的决策语义是「通过/弃用」(没有打回重做):弃用=rejected 进已归档,
# 保留一段时间允许「拉回待验收」,超期由这里的协程整目录清掉(只删任务记录,
# 不动 state/benchmark 下共享的已下载素材)。
DISCARD_TTL_HOURS = float(os.environ.get("NOF_DISCARD_TTL_HOURS", "168"))   # 默认保留 7 天
# cron 刷新任务独立 TTL:订阅传感器每天产十几条任务记录,自动归档后无人再看,
# 比人工弃用件清得更快(默认 3 天)。案卷照例先于删除存在。
CRON_TTL_HOURS = float(os.environ.get("NOF_CRON_TTL_HOURS", "72"))
_DISCARD_SWEEP_INTERVAL_S = 3600


def backfill_labels_once() -> int:
    """案卷对账:凡有决策(review.json)而无案卷的任务,幂等补写一份。返回补写数。

    吃下两类缺口:P1 上线前的存量决策(现成的种子语料)、review 路由落卷失败
    被吞掉的样本。每小时随清扫跑一轮,复盘(P4)只读 labels/,缺卷即缺样本。
    """
    filled = 0
    for meta in STORE.list_tasks():
        if meta.decision is None or LABELS.exists(meta.task_id):
            continue
        review = STORE.get_review(meta.task_id)
        if review is None:
            continue
        try:
            LABELS.write(meta, review, STORE.get_result(meta.task_id))
            filled += 1
        except Exception:  # noqa: BLE001 — 单条失败不影响整轮
            logger.exception("[sweep] 案卷回填失败 %s", meta.task_id)
    return filled


def sweep_discarded_once() -> int:
    """清一轮:沈括 rejected 且超过保留期的任务。返回删除数。"""
    import shutil
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(hours=DISCARD_TTL_HOURS)
    removed = 0
    for meta in STORE.list_tasks():
        if meta.cmd != "shenkuo" or meta.decision != "rejected":
            continue
        review = STORE.get_review(meta.task_id)
        if review is None or not review.reviewed_at:
            continue
        try:
            ts = datetime.fromisoformat(review.reviewed_at)
        except ValueError:
            continue
        if ts < cutoff:
            # 删除前确认案卷已存在(决策=标注,负样本是卧龙复盘的训练数据,
            # 任务目录可以清,标签不能丢)。补写失败则本轮跳过,下轮再试。
            try:
                if not LABELS.exists(meta.task_id):
                    LABELS.write(meta, review, STORE.get_result(meta.task_id))
            except Exception:  # noqa: BLE001
                logger.exception("[sweep] 案卷补写失败,跳过删除 %s", meta.task_id)
                continue
            shutil.rmtree(STORE.task_dir(meta.task_id), ignore_errors=True)
            removed += 1
            logger.info("[sweep] 清除弃用沈括任务 %s (reviewed_at=%s)", meta.task_id, review.reviewed_at)
    return removed


def sweep_cron_once() -> int:
    """清一轮:cron 刷新任务终态后超过 CRON_TTL_HOURS 的记录。返回删除数。

    订阅传感器的任务自动归档(reviewer=system),没有人工价值,只占列表;
    有决策的照例先确保案卷存在再删(自动归档的 system 案卷复盘本就不学)。
    """
    import shutil
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(hours=CRON_TTL_HOURS)
    removed = 0
    for meta in STORE.list_tasks():
        # 只清订阅刷新任务:别让将来其他 cron 触发的任务静默继承 72h 删除策略
        if (
            meta.source != "cron"
            or meta.cmd != "shenkuo"
            or not meta.params.get("refresh_only")
            or meta.status not in ("completed", "failed", "cancelled")
        ):
            continue
        # 只清纯机器闭环的记录:被用户撤销(review=None,等重审)或人工改判
        # (reviewer=user)的任务移交人工清扫节奏(sweep_discarded_once 的 168h)
        review = STORE.get_review(meta.task_id)
        if review is None or review.reviewer != "system":
            continue
        ref = meta.finished_at or meta.created_at
        try:
            ts = datetime.fromisoformat(ref)
        except ValueError:
            continue
        if ts >= cutoff:
            continue
        try:
            if not LABELS.exists(meta.task_id):
                LABELS.write(meta, review, STORE.get_result(meta.task_id))
        except Exception:  # noqa: BLE001
            logger.exception("[sweep] cron 案卷补写失败,跳过删除 %s", meta.task_id)
            continue
        shutil.rmtree(STORE.task_dir(meta.task_id), ignore_errors=True)
        removed += 1
    return removed


async def _discard_sweeper() -> None:
    import asyncio

    while True:
        try:
            # 先对账补卷,再清扫——回填和守门双保险,标签先于删除存在
            filled = backfill_labels_once()
            if filled:
                logger.info("[sweep] 本轮回填 %d 份案卷", filled)
            n = sweep_discarded_once()
            if n:
                logger.info("[sweep] 本轮清除 %d 条弃用任务", n)
            c = sweep_cron_once()
            if c:
                logger.info("[sweep] 本轮清除 %d 条 cron 刷新记录", c)
        except Exception:  # noqa: BLE001 — 清扫失败不影响服务
            logger.exception("[sweep] discard sweep failed")
        await asyncio.sleep(_DISCARD_SWEEP_INTERVAL_S)


@app.on_event("startup")
async def _startup_log() -> None:
    import asyncio

    logger.info(
        "[nof-server] ready. state_dir=%s commands=%s",
        STATE_DIR,
        RUNNER.list_commands(),
    )
    # 调度器:恢复积压(重启后队列在内存里已蒸发) + 按 per-cmd 额度拉起 worker
    recovered = RUNNER.recover_and_start()
    if recovered:
        logger.info("[nof-server] 重启恢复: %d 个积压任务重新入队", recovered)
    asyncio.create_task(_discard_sweeper())
    logger.info("[sweep] 弃用清扫已启动: TTL=%.0fh, cron TTL=%.0fh, 间隔=%ds",
                DISCARD_TTL_HOURS, CRON_TTL_HOURS, _DISCARD_SWEEP_INTERVAL_S)
    # 订阅传感器(NOF_SUBSCRIPTIONS=0 停用;无订阅文件时空转)
    if os.environ.get("NOF_SUBSCRIPTIONS", "1") != "0":
        asyncio.create_task(subscription_loop(RUNNER, STORE, subscriptions_path(STATE_DIR)))


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
