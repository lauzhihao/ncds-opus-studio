#!/usr/bin/env bash
# 在 154 服务器上 native(systemd) 部署 / 更新 ncds-opus-studio。
# 幂等:首次部署与日常更新同一个脚本,重复跑安全。
#
# 为什么 native 而非 docker:call_opus/_call_opus_for_rw/_call_opus_judge 裸调本机
# opus 启动器(~/.sclaude/bin/opus),容器里没这二进制 -> 吴道子/鬼谷子/预筛/柳永去AI味/
# rw润色/卧龙复盘全挂。native 跑在宿主,worker 进程直接用宿主已装好的 opus 启动器。
#
# 用法(在服务器仓库根执行,需 root):
#   ./scripts/server_deploy.sh                 # git ff-only 更新 + 部署
#   ./scripts/server_deploy.sh --branch main   # 指定分支
#   ./scripts/server_deploy.sh --hard          # git reset --hard origin/<branch>(丢弃服务器本地改动)
#   ./scripts/server_deploy.sh --no-pull       # 跳过 git,只重装依赖/重建/重启(调试用)
set -euo pipefail

# --------------------------------------------------------------------------
# 0) 路径与可调参数(均可用环境变量覆盖)
# --------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$REPO_ROOT/.venv}"
PY="$VENV/bin/python3"
UV="${UV_BIN:-/root/.local/bin/uv}"
PY_VERSION="${PY_VERSION:-3.12}"
SCLAUDE_BIN="${SCLAUDE_BIN:-/root/.sclaude/bin}"   # worker PATH 必须含此目录,否则裸调 opus 解析不到
FFMPEG_BIN="${FFMPEG_BIN:-/usr/local/bin/ffmpeg}"
REDIS_URL_HINT="${REDIS_URL_HINT:-redis://127.0.0.1:6379/5?protocol=2}"  # 宿主 redis 5.0.3 不支持 RESP3,须 protocol=2
SERVER_HOST="${NOF_SERVER_HOST:-127.0.0.1}"        # 只绑环回,宿主 nginx 反代
SERVER_PORT="${NOF_SERVER_PORT:-8810}"

BRANCH="develop"
DO_PULL=1
GIT_HARD=0

while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="${2:?--branch needs a value}"; shift 2 ;;
    --no-pull) DO_PULL=0; shift ;;
    --hard) GIT_HARD=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "[deploy] unknown arg: $1" >&2; exit 2 ;;
  esac
done

log(){ printf '[deploy] %s\n' "$*"; }
die(){ printf '[deploy][ERROR] %s\n' "$*" >&2; exit 1; }

cd "$REPO_ROOT"
log "repo=$REPO_ROOT branch=$BRANCH pull=$DO_PULL hard=$GIT_HARD"

# --------------------------------------------------------------------------
# 1) Preflight:缺 uv/ffmpeg 自动补;node/redis 必须预装(host 策略,不替用户决定)
# --------------------------------------------------------------------------
if [ ! -x "$UV" ] && ! command -v uv >/dev/null 2>&1; then
  log "uv 未装,自动安装"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
[ -x "$UV" ] || UV="$(command -v uv)"

if [ ! -x "$FFMPEG_BIN" ] && ! command -v ffmpeg >/dev/null 2>&1; then
  log "ffmpeg 未装,安装 johnvansickle 静态构建 -> /usr/local/bin"
  tmp="$(mktemp -d)"
  curl -LsS https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o "$tmp/ff.tar.xz"
  tar xf "$tmp/ff.tar.xz" -C "$tmp"
  d="$(find "$tmp" -maxdepth 1 -type d -name 'ffmpeg-*-amd64-static' | head -1)"
  install -m755 "$d/ffmpeg" /usr/local/bin/ffmpeg
  install -m755 "$d/ffprobe" /usr/local/bin/ffprobe
  rm -rf "$tmp"
fi

command -v node >/dev/null 2>&1 || die "node 未装(>=18),先装 node 再重跑"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
[ "$NODE_MAJOR" -ge 18 ] || die "node 版本过低($(node -v 2>/dev/null)),vite5 构建需 >=18"
command -v redis-cli >/dev/null 2>&1 || die "redis-cli 未装,宿主需有 redis 服务"
redis-cli -h 127.0.0.1 ping >/dev/null 2>&1 || die "宿主 redis 不可达(127.0.0.1:6379),先起 redis"

# .env 是机器相关 + 含 secrets,部署脚本绝不覆盖,只校验存在性与已知坑
[ -f "$REPO_ROOT/.env" ] || die ".env 缺失($REPO_ROOT/.env),先放好 .env 再部署"
if grep -q '^REDIS_URL=' "$REPO_ROOT/.env" && ! grep -qE '^REDIS_URL=.*protocol=2' "$REPO_ROOT/.env"; then
  log "WARN: .env 的 REDIS_URL 未带 ?protocol=2;若宿主 redis<6.0 worker 会 ping 失败(建议: $REDIS_URL_HINT)"
fi

# --------------------------------------------------------------------------
# 2) git 同步(ff-only 默认;--hard 丢弃服务器本地改动;--no-pull 跳过)
# --------------------------------------------------------------------------
if [ "$DO_PULL" -eq 1 ]; then
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  if [ "$GIT_HARD" -eq 1 ]; then
    log "git reset --hard origin/$BRANCH"
    git reset --hard "origin/$BRANCH"
  else
    git -c advice.detachedHead=false pull --ff-only origin "$BRANCH" \
      || die "git pull --ff-only 失败(服务器有本地改动或分支被 rebase);确认后用 --hard 重跑"
  fi
fi
log "HEAD=$(git rev-parse --short HEAD) $(git log -1 --pretty=%s | cut -c1-60)"

# --------------------------------------------------------------------------
# 3) venv + python 依赖(CPU torch 守卫:无 GPU,避免默认拉 CUDA 版)
# --------------------------------------------------------------------------
if [ ! -x "$PY" ]; then
  log "建 venv(Python $PY_VERSION)"
  "$UV" python install "$PY_VERSION"
  "$UV" venv --python "$PY_VERSION" "$VENV"
fi
if ! "$PY" -c 'import torch' >/dev/null 2>&1; then
  log "装 CPU 版 torch"
  "$UV" pip install --python "$PY" torch --index-url https://download.pytorch.org/whl/cpu
fi
# funasr(说话人分离)运行时硬 import torchaudio 但未在其包元数据里声明;
# 必须和 torch 同源(CPU index)装,混 PyPI 版会 ABI 不匹配报 undefined symbol。
if ! "$PY" -c 'import torchaudio' >/dev/null 2>&1; then
  log "装 CPU 版 torchaudio(funasr 运行时需要)"
  "$UV" pip install --python "$PY" torchaudio --index-url https://download.pytorch.org/whl/cpu
fi
log "装/更新 core + factory(editable)"
"$UV" pip install --python "$PY" -e packages/core
"$UV" pip install --python "$PY" -e .

# --------------------------------------------------------------------------
# 4) web/dist:lock 变了或缺 node_modules 才 npm ci;每次都 build
#    (/studio 静态挂载在 server import 时定死,build 完必须重启 server -> 见步骤 6)
# --------------------------------------------------------------------------
WEB_LOCK_HASH_FILE="$REPO_ROOT/web/node_modules/.deploy_lock_hash"
WEB_LOCK_NOW="$(sha256sum "$REPO_ROOT/web/package-lock.json" | awk '{print $1}')"
if [ ! -d "$REPO_ROOT/web/node_modules" ] || [ "$(cat "$WEB_LOCK_HASH_FILE" 2>/dev/null)" != "$WEB_LOCK_NOW" ]; then
  log "web npm ci(lock 变化/首次)"
  ( cd "$REPO_ROOT/web" && npm ci )
  echo "$WEB_LOCK_NOW" > "$WEB_LOCK_HASH_FILE"
else
  log "web 依赖无变化,跳过 npm ci"
fi
# 原子构建:先构建到 dist.tmp,成功才换进 web/dist。
# 关键:vite 默认会先清空 outDir,若直接写 web/dist 中途失败(类型错误/依赖问题/node 不兼容)
# 会留下空/残缺 dist 把 /studio 弄挂。构建到临时目录则失败时 live dist 纹丝不动,部署干净中止。
log "构建 web/dist(原子:dist.tmp -> dist)"
( cd "$REPO_ROOT/web" && rm -rf dist.tmp && npm run build -- --outDir dist.tmp --emptyOutDir )
[ -f "$REPO_ROOT/web/dist.tmp/index.html" ] || die "web 构建产物缺 index.html(构建失败),live dist 未动,部署中止"
rm -rf "$REPO_ROOT/web/dist.old"
[ -d "$REPO_ROOT/web/dist" ] && mv "$REPO_ROOT/web/dist" "$REPO_ROOT/web/dist.old"
mv "$REPO_ROOT/web/dist.tmp" "$REPO_ROOT/web/dist"
rm -rf "$REPO_ROOT/web/dist.old"

# 根目录 node runner(.mjs)依赖:lock 变了才装
ROOT_LOCK_HASH_FILE="$REPO_ROOT/node_modules/.deploy_lock_hash"
ROOT_LOCK_NOW="$(sha256sum "$REPO_ROOT/package-lock.json" 2>/dev/null | awk '{print $1}')"
if [ -f "$REPO_ROOT/package-lock.json" ] && { [ ! -d "$REPO_ROOT/node_modules" ] || [ "$(cat "$ROOT_LOCK_HASH_FILE" 2>/dev/null)" != "$ROOT_LOCK_NOW" ]; }; then
  log "根 node runner 依赖 npm ci(lock 变化/首次)"
  npm ci --omit=dev || npm install --omit=dev
  echo "$ROOT_LOCK_NOW" > "$ROOT_LOCK_HASH_FILE"
fi

# --------------------------------------------------------------------------
# 5) systemd 单元(幂等覆盖):server 纯入队 / worker 唯一执行(PATH 打头 sclaude bin)
# --------------------------------------------------------------------------
mkdir -p "$REPO_ROOT/state/tasks" "$REPO_ROOT/state/video-jobs"
SERVER_PATH="$VENV/bin:/usr/local/bin:/usr/bin:/bin"
WORKER_PATH="$SCLAUDE_BIN:$VENV/bin:/usr/local/bin:/usr/bin:/bin"

cat > /etc/systemd/system/nof-server.service <<EOF
[Unit]
Description=ncds-opus-factory nof-server (producer :$SERVER_PORT)
After=network-online.target redis.service
Wants=network-online.target
Requires=redis.service

[Service]
Type=simple
User=root
WorkingDirectory=$REPO_ROOT
EnvironmentFile=$REPO_ROOT/.env
Environment=HOME=/root
Environment=NOF_SERVER_HOST=$SERVER_HOST
Environment=NOF_SERVER_PORT=$SERVER_PORT
Environment=PATH=$SERVER_PATH
ExecStart=$VENV/bin/nof-server
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/nof-worker.service <<EOF
[Unit]
Description=ncds-opus-factory nof-worker (executor; opus on PATH)
After=network-online.target redis.service nof-server.service
Wants=network-online.target
Requires=redis.service

[Service]
Type=simple
User=root
WorkingDirectory=$REPO_ROOT
EnvironmentFile=$REPO_ROOT/.env
Environment=HOME=/root
Environment=PATH=$WORKER_PATH
ExecStart=$VENV/bin/nof-worker
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nof-server.service nof-worker.service >/dev/null 2>&1 || true

# --------------------------------------------------------------------------
# 6) 重启:先 server(纯入队,不打断在跑任务)后 worker
#    注意:worker 重启会打断在跑任务,但 worker 启动时会 recover 积压任务重新入队。
# --------------------------------------------------------------------------
log "重启 nof-server"
systemctl restart nof-server.service
sleep 3
log "重启 nof-worker(在跑任务会被 recover 重新入队)"
systemctl restart nof-worker.service
sleep 4

# --------------------------------------------------------------------------
# 7) 健康检查
# --------------------------------------------------------------------------
fail=0
sa="$(systemctl is-active nof-server.service || true)"
wa="$(systemctl is-active nof-worker.service || true)"
log "server=$sa worker=$wa"
[ "$sa" = active ] || { fail=1; log "WARN: nof-server 非 active"; }
[ "$wa" = active ] || { fail=1; log "WARN: nof-worker 非 active"; }

code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$SERVER_PORT/studio/" || true)"
log "GET /studio/ -> $code"
[ "$code" = 200 ] || { fail=1; log "WARN: /studio 非 200"; }

if journalctl -u nof-worker.service --since '30 sec ago' --no-pager 2>/dev/null | grep -q 'redis OK'; then
  log "worker redis OK"
else
  fail=1
  log "WARN: worker 日志未见 'redis OK'(可能 redis 协议/连接问题)"
  journalctl -u nof-worker.service -n 8 --no-pager 2>/dev/null | tail -8
fi

if [ "$fail" -ne 0 ]; then
  die "部署完成但健康检查有告警,见上"
fi
log "DONE. 部署成功,全部健康检查通过。"
