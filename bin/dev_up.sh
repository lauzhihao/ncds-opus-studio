#!/usr/bin/env bash
# 一键起停「前后端 HMR 三件套」(dev 用)：vite dev + nof-server(NOF_DEV=1 + --reload)。
# - 前端改 .tsx/.scss -> vite HMR 自动热更新(免 build 免刷)；后端改 .py -> uvicorn --reload 自动重载。
# - 访问入口：http://localhost:8810/studio/ (带尾斜杠走 dev 反代，HMR WS 同走 8810)。
# - 本脚本不碰 nof-worker(离线任务执行体)。
# - dev 专用：正式部署仍要 `cd web && npm run build` 生成 dist(生产不走 vite)。
#
# 通过 screen 后台运行进程，完全脱离 shell 会话（即使 shell 被 kill 也不影响）。
#
# 用法：
#   ./dev_up.sh up        # 确保 redis -> 起 vite -> 起 nof-server(dev)
#   ./dev_up.sh down      # 停 vite + nof-server(不动 worker / redis)
#   ./dev_up.sh restart   # down + up
#   ./dev_up.sh status    # 查四件套(redis/vite/nof-server/worker)状态
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_UVICORN="$PROJECT_ROOT/.venv/bin/uvicorn"
WEB_DIR="$PROJECT_ROOT/web"
STATE_DIR="$PROJECT_ROOT/state"
HOST="${NOF_SERVER_HOST:-0.0.0.0}"
PORT="${NOF_SERVER_PORT:-8810}"
VITE_PORT=5173
VITE_LOG="$STATE_DIR/vite-dev.out.log"
SERVER_LOG="$STATE_DIR/nof-server.out.log"
SCREEN_VITE="dev-vite"
SCREEN_NOF="dev-nof"

log() { echo "[dev_up] $*"; }

port_listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

ensure_redis() {
  if redis-cli ping >/dev/null 2>&1; then
    log "redis: up"
    return
  fi
  log "redis: down, starting via brew services..."
  if command -v brew >/dev/null 2>&1; then
    brew services start redis >/dev/null 2>&1 || true
  fi
  for _ in 1 2 3 4 5; do
    sleep 1
    if redis-cli ping >/dev/null 2>&1; then
      log "redis: up"
      return
    fi
  done
  log "ERROR: redis still down (try: brew services start redis OR redis-server)" >&2
  exit 1
}

start_vite() {
  if port_listening "$VITE_PORT"; then
    log "vite: already running"
    return
  fi
  mkdir -p "$STATE_DIR"
  screen -dmS "$SCREEN_VITE" bash -c \
    "cd '$WEB_DIR' && NOF_HMR_PORT='$PORT' npm run dev > '$VITE_LOG' 2>&1"
  local i
  for i in $(seq 1 12); do
    if port_listening "$VITE_PORT"; then
      log "vite: up (:$VITE_PORT, log: $VITE_LOG)"
      return 0
    fi
    sleep 1
  done
  log "ERROR: vite not listening on :$VITE_PORT after 12s, see $VITE_LOG" >&2
  exit 1
}

start_server() {
  mkdir -p "$STATE_DIR"
  screen -dmS "$SCREEN_NOF" bash -c \
    "NOF_DEV=1 exec '$VENV_UVICORN' ncds_opus_factory.server.app:app --host '$HOST' --port '$PORT' --reload --reload-dir src > '$SERVER_LOG' 2>&1"
  local i
  for i in $(seq 1 10); do
    if port_listening "$PORT"; then
      log "nof-server: up (NOF_DEV=1 + --reload, :$PORT)"
      return 0
    fi
    sleep 1
  done
  log "ERROR: nof-server didn't start on :$PORT within 10s, see $SERVER_LOG" >&2
  exit 1
}

worker_status() {
  if pgrep -f "ncds_opus_factory.server.worker" >/dev/null 2>&1; then
    log "nof-worker: up (untouched by this script)"
  else
    log "nof-worker: DOWN -> offline tasks won't execute (start: bin/install_nof_worker.sh restart)"
  fi
}

cmd="${1:-up}"
case "$cmd" in
  up)
    ensure_redis
    start_vite
    start_server
    worker_status
    log "ready -> http://localhost:$PORT/studio/  (front HMR + back --reload; build-free)"
    ;;
  down)
    screen -S "$SCREEN_VITE" -X quit 2>/dev/null || true
    screen -S "$SCREEN_NOF" -X quit 2>/dev/null || true
    log "down complete (worker + redis left running)"
    ;;
  restart)
    "$0" down || true
    sleep 1
    "$0" up
    ;;
  status)
    redis-cli ping >/dev/null 2>&1 && log "redis: up" || log "redis: down"
    port_listening "$VITE_PORT" && log "vite: up (:$VITE_PORT)" || log "vite: down"
    port_listening "$PORT" && log "nof-server: up (:$PORT)" || log "nof-server: down"
    worker_status
    ;;
  *)
    echo "usage: $0 {up|down|restart|status}" >&2
    exit 1
    ;;
esac
