"""柳永质检第 2 层 —— rubric 正向质量分(opus 他评)。

第 1 层 ai_taste 是 regex 负向闸门(抓显性 AI 口癖,一票否决);
这一层是正向质量打分,补 regex 够不着的语义维度——尤其"节奏"(句子长短变化),
那是口播命门却是 regex 盲区。

设计取舍(见 docs / 决策记录):
- judge 用 opus(跨模型他评,避开 gpt-5.5 评自己的盲区);
- 仅标注不打回(校准期):只产出分数+issues,不自动重写,行为零风险;
- rubric 是 Humanizer 50 分制的"口播体裁改造版",护栏写死防止惩罚爆款骨架
  (金句/三段/反差钩子/长口播/逐字台词一律不扣)。

common 层不依赖 server:opus 调用在本模块自带(仿 server/pipeline_runner.py 的范例)。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

OPUS_MODEL = "claude-opus-4-7"
DEFAULT_TIMEOUT_SECONDS = 600
DIMENSIONS: tuple[str, ...] = ("节奏", "真实性", "精炼度", "直接性", "信任度")

# 阈值 -> 评级(校准期默认值,待用真稿校准后回填)
GRADE_EXCELLENT = 45
GRADE_GOOD = 35


def available() -> bool:
    """本机 opus 启动器是否就绪。不可用时第 2 层优雅降级、不报错。"""
    return shutil.which("opus") is not None


def grade_of(total: int) -> str:
    """总分映射评级。"""
    if total >= GRADE_EXCELLENT:
        return "优秀"
    if total >= GRADE_GOOD:
        return "良好"
    return "需重修"


def build_rubric_prompt(text: str) -> str:
    """构造 opus judge 的打分 prompt:5 维口播版 + 体裁护栏 + JSON 输出契约。

    护栏是关键——把 Humanizer 的"中立条目"默认值改成"口播台词"默认值,
    否则 rubric 会反过来惩罚柳永的爆款骨架(删金句/打破三段那个坑)。
    """
    return "\n".join([
        "你是抖音知识口播脚本的资深质检员。给下面这条口播稿按 5 个维度打分(每维 1-10,总分 50)。",
        "这是【口播台词】不是书面文章,评判标准必须按口语体裁,不是按维基百科中立条目。",
        "",
        "== 5 个维度 ==",
        "1. 节奏(最该看):句子长短是否交错?全是长句=书面感=AI 味,扣分;短句顿挫+长句铺陈混合=高分。",
        "2. 真实性:像真人开口说话吗?有锋芒/有观点/有口语语气=高分;每句都一样的机械主谓宾循环=扣分。",
        "3. 精炼度:有没有注水/车轱辘话/为凑时长的废话?注意长不是罪、注水才是,信息密度高的长稿=高分。",
        "4. 直接性:钩子和台词段是否开门见山不铺垫?注意'机制命名'段允许必要的逻辑铺陈,不要因此扣分。",
        "5. 信任度:有没有无意义的过度叮嘱/反复解释同一点?注意逐字台词'遇到X就说Y+为什么'是体裁刚需,不扣;只罚车轱辘重复。",
        "",
        "== 硬性护栏(违反就是你评错了) ==",
        "- 金句收尾、3-4 招分点结构、冒犯式反差钩子、1500 字以上长口播、可照搬的逐字台词:这些是爆款要素,一律不许扣分,缺了反而要扣。",
        "- 不要因为'像营销/有锋芒/有情绪/有攻击性'扣分——口播就是要这些。",
        "- 你只打分 + 列问题,绝对不要改写、不要给修改后的稿子。",
        "",
        "== 输出格式(只输出一个 JSON 对象,不要 markdown 代码块、不要别的话) ==",
        '{"dims":{"节奏":N,"真实性":N,"精炼度":N,"直接性":N,"信任度":N},"issues":["问题1","问题2"]}',
        "issues 是具体可改的问题(2-5 条),指明在第几段/哪一句。",
        "",
        "== 待评稿 ==",
        text,
    ])


def _extract_first_json_object(s: str) -> str | None:
    """从字符串里抽第一个括号配平的 {...} 块(容忍前后解释文字 / ```json``` 包裹)。"""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        c = s[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def parse_rubric_output(raw: str) -> dict[str, Any]:
    """从 opus 的 result 文本里抽评分 JSON。

    容错:裹 ```json``` 代码块、前后带解释文字。total 一律以 dims 之和为准
    (不信任模型自己的加法)。抽不出或维度不全 -> raise ValueError(由 score 兜底降级)。
    """
    if not raw or not raw.strip():
        raise ValueError("rubric 输出为空")
    blob = _extract_first_json_object(raw)
    if blob is None:
        raise ValueError(f"未找到 JSON 对象: {raw[:120]}")
    obj = json.loads(blob)
    dims_raw = obj.get("dims") or {}
    if not isinstance(dims_raw, dict) or not all(d in dims_raw for d in DIMENSIONS):
        raise ValueError(f"dims 维度不全: {dims_raw}")
    dims = {d: int(round(float(dims_raw[d]))) for d in DIMENSIONS}
    total = sum(dims.values())  # 以 dims 之和为准,防 opus 算错
    issues = obj.get("issues") or []
    if not isinstance(issues, list):
        issues = [str(issues)]
    return {"dims": dims, "total": total, "issues": [str(x) for x in issues][:8]}


def _call_opus_judge(prompt: str, timeout_seconds: int) -> str:
    """走本机 opus 启动器 -> claude CLI,返回 result 文本。

    仿 server/pipeline_runner.py 的 _call_opus_for_rw:--no-resume / --no-session-persistence
    / stdin=DEVNULL 防会话污染;逐行扫 stdout 取最后一条 type=result 的 result。
    """
    args = [
        "opus", "launch", "--no-resume", "--",
        "-p", prompt,
        "--output-format", "json",
        "--model", OPUS_MODEL,
        "--permission-mode", "bypassPermissions",
        "--tools", "",
        "--no-session-persistence",
    ]
    proc = subprocess.run(
        args, capture_output=True, text=True,
        timeout=timeout_seconds, stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise RuntimeError(f"opus judge exited {proc.returncode}: {tail}")
    final_text = ""
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "result":
            continue
        if payload.get("is_error"):
            raise RuntimeError(f"opus judge error: {payload.get('result')}")
        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            final_text = result.strip()
    if not final_text:
        raise RuntimeError(f"opus judge empty; stdout tail={proc.stdout[-200:]}")
    return final_text


def score(text: str, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """对成稿打 rubric 质量分。

    opus 不可用 / 调用失败 / 解析失败 -> 优雅降级,返回 available=False+skipped,
    绝不抛异常(第 2 层是仅标注的附加信息,不能因它拖垮整个柳永流程)。
    """
    if not available():
        return {"available": False, "skipped": "opus 不可用(本机无 opus 启动器)"}
    try:
        raw = _call_opus_judge(build_rubric_prompt(text), timeout_seconds)
        parsed = parse_rubric_output(raw)
    except Exception as exc:  # 降级:judge 失败不拖垮主流程
        return {"available": False, "skipped": f"rubric 打分失败: {exc}"}
    total = parsed["total"]
    return {
        "available": True,
        "dims": parsed["dims"],
        "total": total,
        "grade": grade_of(total),
        "issues": parsed["issues"],
        "calibration": "校准期默认阈值(45优秀/35良好),分数仅供参考,待真稿校准",
    }


if __name__ == "__main__":
    import sys

    raw = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else sys.stdin.read()
    print(json.dumps(score(raw), ensure_ascii=False, indent=2))
