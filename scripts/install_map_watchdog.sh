#!/usr/bin/env bash
# 把 scripts/map_project_watchdog.py 注册为 macOS launchd 用户级自启动任务。
# 用法：
#   ./install_map_watchdog.sh install     # 写入 plist 并加载
#   ./install_map_watchdog.sh uninstall   # 卸载并删除 plist
#   ./install_map_watchdog.sh status      # 查看 launchd 注册状态
#   ./install_map_watchdog.sh logs        # tail -n 50 看门狗日志
#   ./install_map_watchdog.sh restart     # 卸载后重新加载
set -euo pipefail

LABEL="tech.ncds.opus-factory.map-watchdog"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
LOG_DIR="$PROJECT_ROOT/state"
STDOUT_LOG="$LOG_DIR/map_watchdog.out.log"
STDERR_LOG="$LOG_DIR/map_watchdog.err.log"

cmd="${1:-}"
case "$cmd" in
  ""|-h|--help)
    cat <<EOF
usage: $0 {install|uninstall|restart|status|logs}

install    write \$HOME/Library/LaunchAgents/$LABEL.plist and load it
uninstall  unload service and remove plist
restart    uninstall + install in one shot
status     show launchctl print info for $LABEL
logs       tail -n 50 stdout and stderr logs

env:
  PYTHON_BIN  python interpreter used in plist (default: $(command -v python3))
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
        <string>$SCRIPT_DIR/map_project_watchdog.py</string>
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
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
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
