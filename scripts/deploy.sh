#!/usr/bin/env bash
# 本地一键部署:推送当前分支到 origin,然后 SSH 到 154 触发服务器 native 更新。
# 服务器端真正干活的是 scripts/server_deploy.sh(本脚本只负责 push + 触发)。
#
# 用法(在 Mac 仓库根执行):
#   ./scripts/deploy.sh           # push 当前分支 + 远程 ff-only 更新部署
#   ./scripts/deploy.sh --hard    # 远程 git reset --hard(服务器有本地改动/分支被 rebase 时用)
#
# 可用环境变量覆盖目标:
#   DEPLOY_SSH=root@154.201.79.83   DEPLOY_DIR=/root/projects/ncds-opus-studio
set -euo pipefail

SERVER="${DEPLOY_SSH:-root@154.201.79.83}"
REMOTE_DIR="${DEPLOY_DIR:-/root/projects/ncds-opus-studio}"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

HARD=""
[ "${1:-}" = "--hard" ] && HARD="--hard"

echo "[deploy] 本地分支: $BRANCH -> $SERVER:$REMOTE_DIR"

# 提醒未提交改动(只推 commit,工作区改动不会上服务器)
if [ -n "$(git status --porcelain)" ]; then
  echo "[deploy] WARN: 工作区有未提交改动,只会部署已 commit 的内容"
fi

echo "[deploy] git push origin $BRANCH"
git push origin "$BRANCH"

echo "[deploy] 触发服务器更新部署"
# shellcheck disable=SC2029  # 故意在本地展开变量传给远端
ssh "$SERVER" "cd '$REMOTE_DIR' && ./scripts/server_deploy.sh --branch '$BRANCH' $HARD"

echo "[deploy] 完成。线上: https://opus.vooice.tech/studio/"
