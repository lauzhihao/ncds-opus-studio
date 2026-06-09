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
# 后 3 条 + 前 2 条的扩充词摘自 Humanizer-zh(op7418, 基于维基百科"AI 写作特征"),
# 已裁到口播体裁:只取跨体裁通用的显性口癖,Humanizer 的书面排版模式(破折号/粗体/标题)一律不取。
HARD_RULES: list[tuple[str, str, str]] = [
    # 套路连接词:书面 discourse marker,真人口播不这么起句(扩充自 Humanizer #7 AI 词汇)
    ("套路连接词", r"然而[，,]|事实上[，,]|值得注意的是|换句话说|不可否认|与此同时|此外[，,]|综上所述|总而言之", "high"),
    # 升维腔:把具体事拔高成抽象大词(扩充自 Humanizer #1 夸大意义 / #7 至关重要)
    ("升维腔", r"本质上|归根结底|从某种意义上(?:说|讲)?|说到底|至关重要|不可或缺|标志着|见证了|发挥(?:着)?[^，。！？\n]{0,6}(?:关键|重要|核心)(?:性)?作用", "high"),
    ("你值得式祝福", r"愿你|你值得拥有|与(?:你|君)共勉|共勉", "mid"),
    ("翻译腔", r"对[^，。！？\n]{1,12}进行[一了]?|作为一(?:名|个|种)[^，。！？\n]{0,8}的", "mid"),
    # AI书面词:几乎只有 LLM/书面这么写,真人口播不会说(Humanizer #7/#3)
    ("AI书面词", r"深入(?:探讨|剖析|了解)|彰显|赋能|应运而生|淋漓尽致|不可磨灭|相得益彰", "high"),
    # 模糊归因:空泛背书,AI 味+正确性双重问题;记 mid 不一票否决,留给语义层判断(Humanizer #5)
    ("模糊归因", r"研究(?:表明|显示|发现|指出)|专家(?:认为|指出|表示|建议)|数据(?:表明|显示)|有(?:研究|调查|报告)(?:表明|显示)|科学(?:研究|家)(?:表明|发现|认为)", "mid"),
    # 对话腔残留:codex 把 chatbot 口吻漏进脚本,脚本绝不该有,命中即扣(Humanizer #19-21)
    ("对话腔残留", r"希望(?:这|对你|能)[^，。！？\n]{0,6}(?:帮助|帮到你)|如果你(?:需要|想|有任何)[^，。！？\n]{0,8}(?:帮助|问题|可以(?:告诉|问)我)|作为(?:一个)?(?:AI|人工智能|语言模型|大语言模型)|截至我(?:的|最后)|根据我(?:的训练|最后一次)", "high"),
]


# 招数计数一致性(校准期只标注不打回):标题承诺的招数应 = 正文实际给的招数。
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _to_int(s: str | None) -> int | None:
    if not s:
        return None
    return int(s) if s.isdigit() else _CN_NUM.get(s)


def _count_consistency(text: str) -> dict[str, Any]:
    """抽'标题承诺招数'与'正文实际招数'比对。校准期只标注,不参与 verdict。

    柳永踩过的坑:标题写'3句'正文却给4招、结尾速记又收回3句。
    标题数字 vs 正文'第N句/招'最大序号 不等即标记;信息不全(标题无数字/正文无序号)则跳过。
    """
    first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
    mt = re.search(r"([0-9]+|[一二三四五六七八九十])\s*(?:句|招|个|点|步|式|条|种)", first_line)
    title_n = _to_int(mt.group(1)) if mt else None
    body_nums: set[int] = set()
    for m in re.finditer(r"第\s*([0-9]+|[一二三四五六七八九十])\s*(?:句|招)", text):
        n = _to_int(m.group(1))
        if n:
            body_nums.add(n)
    body_n = max(body_nums) if body_nums else None
    consistent = title_n is not None and body_n is not None and title_n == body_n
    if title_n is None or body_n is None:
        note = f"招数计数:标题={title_n}/正文={body_n}(信息不全,跳过)"
    elif consistent:
        note = f"招数一致({title_n}招)"
    else:
        note = f"招数不一致:标题承诺{title_n}招/正文实际{body_n}招"
    return {"title_n": title_n, "body_n": body_n, "consistent": consistent, "note": note}


def scan(text: str) -> dict[str, Any]:
    """扫描文本,返回 {verdict, density, hard, structure, summary}。verdict='fail' 表示该打回重写。

    structure 是招数计数一致性的标注,校准期只记录、不影响 verdict、不触发打回。
    """
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
