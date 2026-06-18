#!/usr/bin/env bash
# 本地 HMR 模式重启 nof-server：强杀残留端口(8810/5173) -> 起 HMR 三件套(redis + vite + NOF_DEV=1 --reload)。
# HMR = 改前端 .tsx/.scss 存盘即热更新(免 build)、改后端 .py uvicorn 自动 reload；访问入口 http://localhost:8810/studio/
# 真身是 bin/dev_up.sh，这里只在它前面加一道 kill -9 强清端口，确保旧进程绝不赖着占端口。
# 用法:
#   ./bin/reload-server.sh        # 普通 shell(建议交互终端跑,常驻进程靠 nohup+disown detach)
#   !bin/reload-server.sh         # claude 输入框 bash 模式(不过模型)
#   /reload-server                # claude slash command(haiku 4.5)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${NOF_SERVER_PORT:-8810}"

# 强杀占用 8810(后端) / 5173(vite) 的残留进程(等价 kill -9 `lsof -t -i:PORT`,空列表不报错)
for p in "$PORT" 5173; do
  pids="$(lsof -t -i:"$p" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    echo "[reload-server] killed :$p (pids: $(echo $pids | tr '\n' ' '))"
  fi
done

# 起 HMR 三件套(redis + vite:5173 + NOF_DEV=1 --reload 的 nof-server)；dev_up 内部 nohup+disown detach
exec "$ROOT/bin/dev_up.sh" up
