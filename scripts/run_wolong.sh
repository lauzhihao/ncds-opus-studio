#!/usr/bin/env bash
set -euo pipefail
# 卧龙先生 — 抖音认知内容工厂操盘手(CEO)启动器。
# 用 opus(sclaude) headless 拉起 opus 4.8,自主编排鬼谷子(选题)→柳永(成稿+质检),产待验收清单。
# 全程走 Claude 订阅(sclaude 账号池),不碰 API。
#
# 用法: bash scripts/run_wolong.sh [条数=3] [对标数据.json] [已发选题,逗号分隔]

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOP_FILE="$HERE/scripts/wolong_sop.md"
[ -f "$SOP_FILE" ] || { echo "缺 SOP: $SOP_FILE" >&2; exit 1; }

COUNT="${1:-3}"
BENCHMARK="${2:-$HERE/state/benchmark/author_26991299823/all_posts.json}"
AVOID="${3:-为什么你做得最多却总在原地踏步,把自己活成棋子}"

SOP="$(cat "$SOP_FILE")"
TASK="# 本轮任务
- 工作目录(命令在此跑): ${HERE}
- 对标数据: ${BENCHMARK}
- 产出条数: ${COUNT}
- 必须避开的已发选题: ${AVOID}

现在开始,按你的 5 步流程执行。"

cd "$HERE"
exec opus launch --no-resume -- \
  -p "${SOP}

${TASK}" \
  --model opus \
  --allowedTools "Bash" "Read" "Write" "Edit" \
  --permission-mode bypassPermissions \
  --add-dir "$HERE"
