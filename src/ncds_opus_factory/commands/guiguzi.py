"""/guiguzi —— 鬼谷子:选题官 agent。

不靠 LLM 凭空想选题(那又是 AI 味),而是**从真爆款数据里提炼母题,迁移成本赛道的新选题**。
输入对标号的作品数据(downloader 拉的 all_posts.json)→ 筛 top 爆款 → scodex 提炼母题 +
迁移成目标赛道新选题(自动避开已发过的)→ 按爆款潜力排序,产出选题库。

选题库 top 几条可直接喂给柳永(liuyong)出脚本。复用 scodex(走 codex_scodex_shim.sh),不碰飞书。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
SHIM = ROOT / "scripts" / "codex_scodex_shim.sh"
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("NOF_SCOUT_TIMEOUT", "900"))

# 选题公式(来自 douyin_cog 内核,见 scripts/douyin_cog_kernel.mjs)
TOPIC_FORMULA = (
    "【困境】目标人群的具体社交/认知困境 + 【反常识断言】一句颠覆认知的解法 + "
    "【可照搬台词】3-4 个'遇到 X 就说 Y'。三类母题按爆款度:社交反击/博弈(最爆) > 认知升维 > 社交好感。"
    "落地型选题才爆,空泛大道理不爆。"
)

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


_GROWTH_KW = ["认知", "破局", "思维", "成长", "自我提升", "赚钱", "搞钱", "格局", "情绪", "社交",
              "说话", "接话", "沟通", "自律", "习惯", "心态", "内耗", "底层逻辑", "人性", "清醒",
              "执行力", "见世面", "边界", "课题分离"]
_BEAUTY_KW = ["颜值", "护肤", "皮肤", "祛痘", "闭口", "黑头", "养发", "护发", "头发", "变好看",
              "变美", "身材", "护眼", "近视", "瘦", "素颜", "气质", "穿搭", "美白", "抗老",
              "眼袋", "发量", "控糖"]


def _classify(desc: str) -> str:
    """轻量赛道分类:不依赖文件预存 cat,鬼谷子自己判断对标作品属于哪类。"""
    g = sum(k in desc for k in _GROWTH_KW)
    b = sum(k in desc for k in _BEAUTY_KW)
    if g > b and g > 0:
        return "growth"
    if b > g and b > 0:
        return "beauty"
    return "other"


def _scodex(prompt: str, env: dict, timeout_seconds: int) -> str:
    """走 scodex shim 调 codex,返回 agent_message 文本。"""
    cmd = [str(SHIM), "-a", "never", "exec", "--skip-git-repo-check", "--ephemeral",
           "-s", "read-only", "-m", "gpt-5.5", "--json", prompt]
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=timeout_seconds, text=True, capture_output=True)
    out = ""
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = obj.get("item") or {}
        if obj.get("type") == "item.completed" and item.get("type") == "agent_message":
            out = item.get("text", "") or out
    return out.strip()


def _parse_topics(text: str) -> list[dict[str, Any]]:
    """从模型输出里抠出 JSON 数组(容错代码块/前后缀)。"""
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        try:
            arr = json.loads(text[start:end + 1])
            return arr if isinstance(arr, list) else []
        except json.JSONDecodeError:
            return []
    return []


def run(
    benchmark_path: str,
    niche: str = "职场/认知/成长",
    avoid: list[str] | None = None,
    top_n: int = 10,
    category: str = "growth",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """从对标作品数据提炼母题、迁移成 niche 赛道的新选题,产出选题库。"""
    avoid = avoid or []
    posts = json.loads(Path(benchmark_path).read_text(encoding="utf-8"))
    pool = [p for p in posts if (category is None or _classify(p.get("desc", "")) == category)]
    pool.sort(key=lambda x: x.get("digg", 0), reverse=True)
    top = pool[:12]
    if not top:
        raise ValueError(f"对标数据里没有 cat={category} 的作品: {benchmark_path}")

    bench_list = "\n".join(f'- [{p.get("digg", 0)}赞] {(p.get("desc") or "")[:50]}' for p in top)
    avoid_text = "；".join(avoid) if avoid else "(无)"
    prompt = (
        "你是抖音选题官。下面是一个认知爆款号的 top 爆款标题(带点赞数)。\n"
        "任务:先提炼每条的「母题」(核心困境 + 反常识点),再把母题迁移成【" + niche + "】赛道能做的全新选题。\n\n"
        "选题公式:" + TOPIC_FORMULA + "\n"
        "必须避开这些已经做过的选题(母题相同也算撞,要换角度):" + avoid_text + "\n\n"
        "输出严格的 JSON 数组,每项字段固定:\n"
        '{"title":"新选题(一句话,带钩子感,像爆款标题)","motif":"母题(困境+反常识点)",'
        '"source":"对标来源标题","why":"为什么可能爆(1句,落地型/反击感/反常识)","potential":7}\n'
        "potential 是 1-10 的爆款潜力分。产出 " + str(top_n) + " 条,按 potential 从高到低排。\n"
        "只输出 JSON 数组,不要解释、不要 markdown 代码块。\n\n"
        "对标 top 爆款:\n" + bench_list
    )

    on_progress(f"鬼谷子: 读 {len(top)} 条对标爆款({category}),生成选题中(scodex)...")
    env = os.environ.copy()
    raw = _scodex(prompt, env, timeout_seconds)
    topics = _parse_topics(raw)
    topics.sort(key=lambda t: t.get("potential", 0), reverse=True)
    on_progress(f"鬼谷子: 产出 {len(topics)} 条选题")

    out_dir = ROOT / "state" / "benchmark" / "topics"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "topics.json"
    out_json.write_text(json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"topics": topics, "out": str(out_json), "raw_len": len(raw)}


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof guiguzi", description="鬼谷子: 选题官 agent")
    parser.add_argument("--benchmark", required=True, help="对标作品数据 all_posts.json 路径")
    parser.add_argument("--niche", default="职场/认知/成长", help="目标赛道")
    parser.add_argument("--avoid", default="", help="已发选题,逗号分隔(避免撞题)")
    parser.add_argument("--top", type=int, default=10, help="产出选题数")
    parser.add_argument("--category", default="growth", help="筛对标作品的分类(growth/beauty/other);空=不筛")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    avoid = [a.strip() for a in args.avoid.split(",") if a.strip()]
    result = run(
        benchmark_path=args.benchmark,
        niche=args.niche,
        avoid=avoid,
        top_n=args.top,
        category=(args.category or None),
        timeout_seconds=args.timeout,
        on_progress=on_progress,
    )
    print(f"\n===== 鬼谷子选题库({len(result['topics'])} 条) =====")
    for i, t in enumerate(result["topics"], 1):
        print(f"\n[{i}] (潜力 {t.get('potential')}) {t.get('title')}")
        print(f"    母题: {t.get('motif')}")
        print(f"    对标: {t.get('source')}")
        print(f"    why : {t.get('why')}")
    print(f"\n选题库已落盘: {result['out']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
