#!/usr/bin/env bash
# codex 适配 shim：伪装成 codex 供 runCodexCli(video_rewrite_runner.mjs)调用，
# 内部转发给账号感知的 scodex —— 这样核心文件一行不改，就能让奥格威走 scodex。
#
# runCodexCli 的调用形如：
#   <this> -a never exec --skip-git-repo-check --ephemeral -s read-only -m <model> --json <prompt>
# scodex 顶层只认自己的子命令(launch/exec/...)，不认 codex 的全局选项(如 -a never)；
# 而 `scodex exec ...` 会先切到额度最佳账号、再透传给 `codex exec ...`。
# 所以这里剥掉 exec 之前的全局选项，从 exec 起原样转发给 scodex。
set -euo pipefail

fwd=()
seen_exec=0
for arg in "$@"; do
  if [[ $seen_exec -eq 0 && "$arg" == "exec" ]]; then
    seen_exec=1
  fi
  if [[ $seen_exec -eq 1 ]]; then
    fwd+=("$arg")
  fi
done

if [[ $seen_exec -eq 0 ]]; then
  # 理论上 runCodexCli 一定带 exec；兜底原样透传。
  exec scodex "$@"
fi

exec scodex "${fwd[@]}"
