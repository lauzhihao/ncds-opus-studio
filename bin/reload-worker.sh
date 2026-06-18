#!/usr/bin/env bash
# 重启 nof-worker(launchd 托管的离线任务执行体)。真身在 bin/install_nof_worker.sh。
# worker 不绑 8810/5173，故无需 kill 端口(那个在 reload-server.sh)。
# 用法:
#   ./bin/reload-worker.sh        # 普通 shell
#   !bin/reload-worker.sh         # claude 输入框 bash 模式(不过模型)
#   /reload-worker                # claude slash command(haiku 4.5)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/bin/install_nof_worker.sh" restart
