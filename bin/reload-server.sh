#!/usr/bin/env bash
# 本地 HMR 模式重启 nof-server：kill -9 强清 server/vite -> 起 HMR 三件套(redis + vite + NOF_DEV=1 --reload)。
# HMR = 改前端 .tsx/.scss 存盘即热更新(免 build)、改后端 .py uvicorn 自动 reload；访问入口 http://localhost:8810/studio/
# 真身是 bin/dev_up.sh，这里只在它前面做硬重启清理，确保旧进程/SSE 长连接不会卡在 shutdown。
# 用法:
#   ./bin/reload-server.sh        # 普通 shell(建议交互终端跑,常驻进程靠 nohup+disown detach)
#   !bin/reload-server.sh         # claude 输入框 bash 模式(不过模型)
#   /reload-server                # claude slash command(haiku 4.5)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${NOF_SERVER_PORT:-8810}"
VITE_PORT=5173

kill_pids() {
  local label="$1"
  local pids="$2"
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    echo "[reload-server] kill -9 $label (pids: $(echo "$pids" | tr '\n' ' '))"
  fi
}

kill_port_listener() {
  local port="$1"
  local pids
  pids="$(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true)"
  kill_pids ":$port" "$pids"
}

kill_matching_processes() {
  local label="$1"
  local pattern="$2"
  local pids
  pids="$(
    ps -axo pid=,command= \
      | awk -v pat="$pattern" -v self="$$" '$1 != self && index($0, pat) {print $1}' \
      | sort -u
  )"
  kill_pids "$label" "$pids"
}

kill_screen_session() {
  local name="$1"
  local sessions
  sessions="$(screen -ls 2>/dev/null | awk -v name="$name" '$1 ~ "\\." name "$" {print $1}' || true)"
  if [ -z "$sessions" ]; then
    return
  fi
  local session pid
  for session in $sessions; do
    pid="${session%%.*}"
    kill_pids "screen:$name" "$pid"
  done
}

# 1) 先杀监听端口的 server/vite。只杀 LISTEN，避免误杀浏览器客户端连接。
for p in "$PORT" "$VITE_PORT"; do
  kill_port_listener "$p"
done

# 2) 再杀 uvicorn reloader 父/子进程。它可能不再 LISTEN，但仍会卡住 screen/session。
kill_matching_processes "nof-server" "ncds_opus_factory.server.app:app"

# 3) 最后杀 dev_up 创建的 screen session 外壳，避免 stale session 影响下一次启动。
kill_screen_session "dev-nof"
kill_screen_session "dev-vite"

sleep 0.2

# 起 HMR 三件套(redis + vite:5173 + NOF_DEV=1 --reload 的 nof-server)；dev_up 内部 nohup+disown detach
exec "$ROOT/bin/dev_up.sh" up
