"""AI 味检测器 —— 质检闸门的"大脑"。

吸收 dbskill 的 dbs-ai-check 22 特征,但**裁剪到抖音口播体裁**:
- 体裁豁免:段段金句(#13)、钩子+痛点+承诺三件套(#16)是短视频体裁要求,不判(否则会阉割爆款骨架);
- 密度类:像"不是X而是Y"这种,鲁迅李敖都用,低密度是正常修辞,**超阈值才算**(ai-check #8);
- 硬禁类:跨体裁的"执行层光滑病"(翻译腔/套路连接词/升维腔/你值得式祝福),命中即扣。

只做程序化粗检(快、可量化、可自动打回);更隐蔽的语义 AI 味交给后续 LLM 自检层。
"""
from __future__ import annotations

import re
from typing import Any

# 密度类:正常修辞,出现次数 >= 阈值才判为 AI 味(基于一篇 1500-2000 字长口播)
DENSITY_RULES: list[tuple[str, str, int]] = [
    ("不是X而是Y", r"不是[^，。！？\n]{1,30}[，,]?\s*而是", 3),
    ("不是X你是Y", r"不是[^，。！？\n]{1,24}[，,]\s*[你他它]?是[^。！？\n]{1,28}", 2),
    ("X不是A、B才是", r"不是[^，。！？\n]{1,20}[，,]\s*[^，。！？\n]{1,18}才是", 2),
    ("不一定是…反而", r"不一定是[^。！？\n]{1,28}反而", 1),
    ("从来不是", r"从来?不[是会]", 1),
    ("机制命名仪式", r"(?:起(?:个)?名(?:字)?(?:叫)?|称(?:之)?为|我叫它|我管它叫)", 3),
]

# 硬禁类:跨体裁的 AI 口癖,命中即扣(severity: high=几乎只有AI这么写)
HARD_RULES: list[tuple[str, str, str]] = [
    ("套路连接词", r"然而[，,]|事实上[，,]|值得注意的是|换句话说|不可否认|与此同时", "high"),
    ("升维腔", r"本质上|归根结底|从某种意义上(?:说|讲)?|说到底", "high"),
    ("你值得式祝福", r"愿你|你值得拥有|与(?:你|君)共勉|共勉", "mid"),
    ("翻译腔", r"对[^，。！？\n]{1,12}进行[一了]?|作为一(?:名|个|种)[^，。！？\n]{0,8}的", "mid"),
]


def scan(text: str) -> dict[str, Any]:
    """扫描文本,返回 {verdict, density, hard, summary}。verdict='fail' 表示该打回重写。"""
    hits_density: list[dict[str, Any]] = []
    for name, pat, thr in DENSITY_RULES:
        ms = re.findall(pat, text)
        if len(ms) >= thr:
            hits_density.append({"rule": name, "count": len(ms), "threshold": thr, "samples": ms[:3]})

    hits_hard: list[dict[str, Any]] = []
    for name, pat, sev in HARD_RULES:
        ms = re.findall(pat, text)
        if ms:
            hits_hard.append({"rule": name, "count": len(ms), "severity": sev, "samples": ms[:3]})

    # 判定:任何超阈密度 或 任何 high 硬禁 -> 打回
    fail = bool(hits_density) or any(h["severity"] == "high" for h in hits_hard)
    return {
        "verdict": "fail" if fail else "pass",
        "density": hits_density,
        "hard": hits_hard,
        "summary": f"密度超阈 {len(hits_density)} 类 / 硬禁命中 {len(hits_hard)} 类",
    }


if __name__ == "__main__":
    import sys

    raw = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else sys.stdin.read()
    report = scan(raw)
    print(f"[verdict] {report['verdict']} -- {report['summary']}")
    for h in report["density"]:
        print(f"  [密度] {h['rule']}: {h['count']} 次(阈值 {h['threshold']}) 例: {h['samples']}")
    for h in report["hard"]:
        print(f"  [硬禁/{h['severity']}] {h['rule']}: {h['count']} 次 例: {h['samples']}")
