#!/usr/bin/env bash
# 一键起停「前后端 HMR 三件套」(dev 用)：vite dev + nof-server(NOF_DEV=1 + --reload)。
# - 前端改 .tsx/.scss -> vite HMR 自动热更新(免 build 免刷)；后端改 .py -> uvicorn --reload 自动重载。
# - 访问入口：http://localhost:8810/studio/ (带尾斜杠走 dev 反代，HMR WS 同走 8810)。
# - 本脚本不碰 nof-worker(离线任务执行体)，只查它在不在跑并提示；worker 起停走 install_nof_worker.sh。
# - dev 专用：正式部署仍要 `cd web && npm run build` 生成 dist(生产不走 vite)。
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

VITE_LOG="$STATE_DIR/vite-dev.out.log"
SERVER_LOG="$STATE_DIR/nof-server.out.log"
VITE_PID_FILE="$STATE_DIR/vite-dev.pid"
SERVER_PID_FILE="$STATE_DIR/nof-server.pid"

SERVER_PATTERN="uvicorn ncds_opus_factory.server.app:app"

log() { echo "[dev_up] $*"; }

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

kill_pattern() {
  # $1 = pgrep pattern, $2 = human name
  local pids
  pids="$(pgrep -f "$1" || true)"
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    log "$2: stopped (pids: $pids)"
  else
    log "$2: not running"
  fi
}

start_vite() {
  if pgrep -f "node .*/web/node_modules/.bin/vite" >/dev/null 2>&1; then
    log "vite: already running"
    return
  fi
  mkdir -p "$STATE_DIR"
  # HMR WS 走 8810(访问入口是 nof-server 反代)，见 web/vite.config.ts
  ( cd "$WEB_DIR" && NOF_HMR_PORT="$PORT" nohup npm run dev > "$VITE_LOG" 2>&1 < /dev/null & echo $! > "$VITE_PID_FILE"; disown 2>/dev/null || true )
  sleep 2
  if pgrep -f "node .*/web/node_modules/.bin/vite" >/dev/null 2>&1; then
    log "vite: up (:5173, log: $VITE_LOG)"
  else
    log "ERROR: vite failed to start, see $VITE_LOG" >&2
    exit 1
  fi
}

start_server() {
  kill_pattern "$SERVER_PATTERN" "nof-server (old)"
  sleep 1
  mkdir -p "$STATE_DIR"
  NOF_DEV=1 nohup "$VENV_UVICORN" ncds_opus_factory.server.app:app \
    --host "$HOST" --port "$PORT" --reload --reload-dir src \
    > "$SERVER_LOG" 2>&1 < /dev/null &
  echo $! > "$SERVER_PID_FILE"
  disown 2>/dev/null || true
  sleep 4
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/studio/" || true)"
  if [[ "$code" == "200" ]]; then
    log "nof-server: up (NOF_DEV=1 + --reload, :$PORT, /studio/ -> $code)"
  else
    log "WARN: nof-server /studio/ returned $code (vite up? see $SERVER_LOG)"
  fi
}

worker_status() {
  if pgrep -f "ncds_opus_factory.server.worker" >/dev/null 2>&1; then
    log "nof-worker: up (untouched by this script)"
  else
    log "nof-worker: DOWN -> offline tasks won't execute (start: scripts/install_nof_worker.sh restart)"
  fi
}

status() {
  redis-cli ping >/dev/null 2>&1 && log "redis: up" || log "redis: down"
  pgrep -f "node .*/web/node_modules/.bin/vite" >/dev/null 2>&1 && log "vite: up (:5173)" || log "vite: down"
  pgrep -f "$SERVER_PATTERN" >/dev/null 2>&1 && log "nof-server: up (:$PORT)" || log "nof-server: down"
  worker_status
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
    kill_pattern "node .*/web/node_modules/.bin/vite" "vite"
    kill_pattern "$SERVER_PATTERN" "nof-server"
    log "down complete (worker + redis left running)"
    ;;
  restart)
    "$0" down || true
    sleep 1
    "$0" up
    ;;
  status)
    status
    ;;
  *)
    echo "usage: $0 {up|down|restart|status}" >&2
    exit 1
    ;;
esac
