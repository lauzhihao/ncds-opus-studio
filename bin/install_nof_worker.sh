#!/usr/bin/env bash
# 把 nof-worker（离线任务执行进程，S3 worker 拆分）注册为 macOS launchd 用户级自启动。
# 用法：
#   ./install_nof_worker.sh install     # 写入 plist 并加载
#   ./install_nof_worker.sh uninstall   # 卸载并删除 plist
#   ./install_nof_worker.sh status      # 查看 launchd 注册状态
#   ./install_nof_worker.sh logs        # tail -n 50 nof-worker 日志
#   ./install_nof_worker.sh restart     # 卸载后重新加载
set -euo pipefail

LABEL="tech.ncds.opus-factory.nof-worker"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python3}"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
LOG_DIR="$PROJECT_ROOT/state"
STDOUT_LOG="$LOG_DIR/nof_worker.out.log"
STDERR_LOG="$LOG_DIR/nof_worker.err.log"

cmd="${1:-}"
case "$cmd" in
  ""|-h|--help)
    cat <<EOF
usage: $0 {install|uninstall|restart|status|logs}

install    write \$HOME/Library/LaunchAgents/$LABEL.plist and load it
uninstall  unload nof-worker and remove plist
restart    uninstall + install in one shot
status     show launchctl print info for $LABEL
logs       tail -n 50 nof-worker stdout and stderr logs

env:
  PYTHON_BIN  python interpreter used in plist (default: $PROJECT_ROOT/.venv/bin/python3)

# 依赖：nof-worker 启动前需 Redis 已就绪(brew services start redis)；KeepAlive 会在 redis 未起时自动重启重试
EOF
    exit 0
    ;;
esac

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "error: launchd is macOS only; current OS is $(uname -s)" >&2
    exit 1
  fi
}

write_plist() {
  mkdir -p "$PLIST_DIR" "$LOG_DIR"
  cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>-m</string>
        <string>ncds_opus_factory.server.worker</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_ROOT</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>StandardOutPath</key>
    <string>$STDOUT_LOG</string>
    <key>StandardErrorPath</key>
    <string>$STDERR_LOG</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF
  echo "wrote $PLIST_PATH"
}

unload_if_loaded() {
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || \
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    echo "unloaded $LABEL"
  fi
}

case "$cmd" in
  install)
    require_macos
    if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
      echo "error: python3 not found; set PYTHON_BIN explicitly" >&2
      exit 1
    fi
    unload_if_loaded
    write_plist
    launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
    echo "loaded $LABEL"
    echo "logs: $STDERR_LOG"
    ;;
  uninstall)
    require_macos
    unload_if_loaded
    if [[ -f "$PLIST_PATH" ]]; then
      rm -f "$PLIST_PATH"
      echo "removed $PLIST_PATH"
    fi
    ;;
  restart)
    require_macos
    unload_if_loaded
    write_plist
    launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
    echo "reloaded $LABEL"
    ;;
  status)
    require_macos
    if launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null; then
      :
    else
      echo "$LABEL is not loaded"
    fi
    ;;
  logs)
    for f in "$STDOUT_LOG" "$STDERR_LOG"; do
      if [[ -f "$f" ]]; then
        echo "=== $f ==="
        tail -n 50 "$f"
      fi
    done
    ;;
  *)
    echo "unknown command: $cmd" >&2
    exit 2
    ;;
esac
