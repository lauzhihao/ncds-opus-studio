"""/guiguzi —— 鬼谷子:选题官 agent（两步流·评论驱动双模型）。

不固定赛道、不套死公式。每个对标账号、每部作品的爆款原因都可能不一样,所以选题分两步:

  1. **analyze(找原因)**：双模型并行,从提取文案 + 高赞评论里**反推爆款原因**,产出结构化
     分析（爆款原因 / 目标受众 / 可复制钩子 / 建议方向）。不预设任何赛道。
  2. **generate_topics(出选题)**：以一份（用户可编辑后选定的）分析为指导,双模型并行,逐条
     评论锚定出新选题。赛道/方向都来自分析,不再钉死"职场/认知/成长 + 社交博弈公式"。

中间允许用户介入:前端先展示双模型分析,用户改完、2 选 1,再执行选题。``run()`` 把两步
端到端串起来（默认取 opus 分析,缺则 deepseek）,供卧龙/CLI/mock 自动跑。

opus 走 common.opus_cli.call_opus,deepseek 走 common.deepseek_cli.call_deepseek。
单模型失败不拖垮另一模型。选题入库走 topic_store（向后兼容 result["topics"]/["out"]）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.common import topic_store
from ncds_opus_factory.common.deepseek_cli import call_deepseek
from ncds_opus_factory.common.opus_cli import call_opus

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("NOF_SCOUT_TIMEOUT", "900"))
MAX_ITEMS = 5  # 与前端"最多选 5 条评论"对齐;调用方应已限,这里兜底截断
MODELS = ("opus", "deepseek")

ProgressFn = Callable[[str], None]
# 单模型 caller:吃 prompt 文本,返回模型原始输出文本(失败抛 RuntimeError)。
CallerFn = Callable[[str], str]


def _noop(_text: str) -> None:
    return None


def _callers(timeout_seconds: int) -> dict[str, CallerFn]:
    env = os.environ.copy()
    return {
        "opus": lambda p: call_opus(p, timeout_seconds=timeout_seconds, env=env),
        "deepseek": lambda p: call_deepseek(p, timeout_seconds=timeout_seconds),
    }


def _normalize_items(items: Any, *, require_comment: bool = True) -> list[dict[str, str]]:
    """把入参规整成 [{"text": 提取文案, "comment": 评论}, ...],截断到 MAX_ITEMS。

    默认(评论驱动):缺 comment 的条目丢弃;缺 text 允许(原文可空,模型只凭评论判断)。
    require_comment=False(无评论「直接拆解原文」):保留有 text 无 comment 的条目,comment 置空。
    """
    out: list[dict[str, str]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        comment = str(it.get("comment") or "").strip()
        text = str(it.get("text") or "").strip()
        if comment:
            out.append({"text": text, "comment": comment})
        elif not require_comment and text:
            out.append({"text": text, "comment": ""})
        else:
            continue
        if len(out) >= MAX_ITEMS:
            break
    return out


def _parse_arr(text: str) -> list[dict[str, Any]]:
    """从模型输出里抠出 JSON 数组(容错代码块/前后缀)。"""
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        try:
            arr = json.loads(text[start:end + 1])
            return arr if isinstance(arr, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _parse_obj(text: str) -> dict[str, Any]:
    """从模型输出里抠出 JSON 对象(容错代码块/前后缀)。"""
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _source_blocks(items: list[dict[str, str]]) -> tuple[str, str]:
    """把 items 拼成「原文块(去重) + 编号评论列表」两段,analyze/generate 共用。"""
    texts: list[str] = []
    text_idx: dict[str, int] = {}
    for it in items:
        t = it["text"]
        if t not in text_idx:
            text_idx[t] = len(texts)
            texts.append(t)
    blocks = "\n\n".join(
        f"【原文{i + 1}】\n{t or '(无提取文案,仅凭评论判断)'}" for i, t in enumerate(texts)
    )
    comment_lines = "\n".join(
        f"{j}. (对应原文{text_idx[it['text']] + 1}) {it['comment']}"
        for j, it in enumerate(items, 1)
        if it["comment"]  # 无评论「直接拆解」时跳过,comment_lines 收成空串
    )
    return blocks, comment_lines


# ============================================================ 第一步：找爆款原因
def _normalize_analysis(obj: dict[str, Any]) -> dict[str, Any]:
    """规整分析结构:四字段齐全,hooks 收成 list[str]。"""
    hooks = obj.get("hooks")
    if isinstance(hooks, str):
        hooks = [h.strip() for h in hooks.splitlines() if h.strip()]
    elif isinstance(hooks, list):
        hooks = [str(h).strip() for h in hooks if str(h).strip()]
    else:
        hooks = []
    return {
        "hook_reason": str(obj.get("hook_reason") or "").strip(),
        "audience": str(obj.get("audience") or "").strip(),
        "hooks": hooks,
        "direction": str(obj.get("direction") or "").strip(),
    }


# 「爆款」是抖音内容行业术语,这里给模型一句注解,避免不同模型对它的理解有偏差。
BAOKUAN_DEF = "播放/点赞/评论等数据显著高于平均、跑出来的热门作品"


def _works_noun(items: list[dict[str, str]]) -> str:
    """按去重后的原文篇数决定量词:单篇用「这部作品」,多篇用「这批作品」。"""
    return "这部作品" if len({it["text"] for it in items}) <= 1 else "这批作品"


def _build_analyze_prompt(items: list[dict[str, str]]) -> str:
    blocks, comment_lines = _source_blocks(items)
    has_comments = bool(comment_lines.strip())  # 无评论「直接拆解」时只凭原文
    single = len({it["text"] for it in items}) <= 1
    works = _works_noun(items)
    if has_comments:
        intro = (
            "下面是一部对标作品的提取文案,以及它的高赞评论。"
            if single else
            "下面是若干对标作品的提取文案,以及它们的高赞评论。"
        )
        signal = "高赞评论是观众的真实反应,暴露了作品到底戳中了什么。\n"
        task = f"任务:**反推{works}为什么成为爆款**——不要套任何固定赛道或公式,完全从原文和评论里看它为什么火。\n\n"
        source_tail = blocks + "\n\n【高赞评论】\n" + comment_lines
    else:
        intro = (
            "下面是一部对标作品的提取文案。"
            if single else
            "下面是若干对标作品的提取文案。"
        )
        signal = ""
        task = f"任务:**反推{works}为什么成为爆款**——不要套任何固定赛道或公式,完全从原文里看它为什么火。\n\n"
        source_tail = blocks
    return (
        f"你是抖音爆款拆解专家。{intro}\n"
        f"(这里「爆款」指{BAOKUAN_DEF}。)\n"
        + signal
        + task
        + "输出严格的 JSON 对象,字段固定:\n"
        '{"hook_reason":"爆款原因:核心吸引力/为什么火(2-3句,具体到内容,不要空话)",'
        '"audience":"目标受众:谁在看、被戳中的是谁(1句)",'
        '"hooks":["可复制钩子:能照搬到新作品的具体套路/结构/表达,3-5条,每条一句"],'
        '"direction":"衍生选题建议方向:基于以上,新作品该往哪个方向做(1-2句)"}\n'
        "只输出 JSON 对象,不要解释、不要 markdown 代码块。\n\n"
        + source_tail
    )


def _analyze_for_model(
    model: str, caller: CallerFn, items: list[dict[str, str]], on_progress: ProgressFn,
) -> dict[str, Any]:
    prompt = _build_analyze_prompt(items)
    on_progress(f"鬼谷子: {model} 分析爆款原因中...")
    raw = caller(prompt)
    analysis = _normalize_analysis(_parse_obj(raw))
    on_progress(f"鬼谷子: {model} 分析完成")
    return analysis


def analyze(
    items: list[dict[str, str]] | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
    *,
    require_comment: bool = True,
) -> dict[str, dict[str, Any]]:
    """第一步:双模型并行反推爆款原因。返回 {opus:{analysis,error}, deepseek:{analysis,error}}。

    require_comment=False:无评论「直接拆解」,仅凭原文(items 为 [{text, comment:''}, ...])。
    """
    norm = _normalize_items(items, require_comment=require_comment)
    if not norm:
        raise ValueError("guiguzi.analyze: items 为空,需要原文或带评论的 [{text, comment}, ...]")
    on_progress(f"鬼谷子: {len(norm)} 条素材 -> opus/deepseek 双模型并行分析爆款原因...")
    callers = _callers(timeout_seconds)
    out: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(callers)) as ex:
        futs = {m: ex.submit(_analyze_for_model, m, fn, norm, on_progress)
                for m, fn in callers.items()}
        for m, fut in futs.items():
            try:
                out[m] = {"analysis": fut.result(), "error": None}
            except Exception as exc:  # noqa: BLE001 — 单模型失败收成 error,不抛
                out[m] = {"analysis": None, "error": f"{type(exc).__name__}: {exc}"}
                on_progress(f"鬼谷子: {m} 分析失败 - {exc}")
    return out


# ============================================================ 第二步：出选题
def _format_analysis(analysis: dict[str, Any] | None) -> str:
    a = _normalize_analysis(analysis or {})
    hooks = "；".join(a["hooks"]) if a["hooks"] else "(无)"
    return (
        f"- 爆款原因: {a['hook_reason'] or '(无)'}\n"
        f"- 目标受众: {a['audience'] or '(无)'}\n"
        f"- 可复制钩子: {hooks}\n"
        f"- 建议方向: {a['direction'] or '(无)'}"
    )


SOURCE_PLACEHOLDER = "$source"  # 选题模板里「原文」的占位符:展示给用户时不输出原文,执行时再替换

# 无评论「直接拆解」出选题时,没有评论数当锚点,改为自由产出固定条数。
NO_COMMENT_TOPIC_COUNT = 5


def _build_topic_prompt_template(items: list[dict[str, str]], analysis: dict[str, Any] | None) -> str:
    """拼凑选题提示词模板:分析 + 任务 + JSON schema + $source(原文占位) + 编号评论。

    后端不再硬编码完整 prompt——模板拼好后展示给用户编辑;原文用 $source 占位(不输出原文,
    它可能作为参考输入),执行时 _render_topic_prompt 把 $source 换成真实原文块。

    两套任务:有评论 → 逐条锚定评论出题;无评论(直接拆解) → 仅凭原文 + 分析自由出 N 个选题。
    """
    _, comment_lines = _source_blocks(items)
    has_comments = bool(comment_lines.strip())
    works = _works_noun(items)
    if has_comments:
        n = len(items)
        return (
            f"你是抖音选题官。下面给出对标作品的提取文案、高赞评论,以及对{works}为什么成为爆款({BAOKUAN_DEF})的分析。\n"
            "请严格以【爆款原因分析】为指导(受众/钩子/方向都来自它),不要自己另设赛道、不要套固定公式。\n"
            "任务:针对【每一条评论】,结合原文与分析,产出一个可直接做的全新选题。\n"
            f"必须产出恰好 {n} 个选题,第 i 个选题锚定第 i 条评论(原文+分析作背景),不要合并、不要漏。\n\n"
            "【爆款原因分析】\n" + _format_analysis(analysis) + "\n\n"
            "输出严格的 JSON 数组,长度 " + str(n) + ",每项字段固定:\n"
            '{"index":<对应评论编号 1..' + str(n) + '>,"title":"新选题(一句话,带钩子感,像爆款标题)",'
            '"angle":"切入角度(一句话,这题从哪个口子讲)",'
            '"why":"为什么可能爆(1句,贴合上面分析的钩子/受众)","potential":7}\n'
            "potential 是 1-10 的爆款潜力分。只输出 JSON 数组,不要解释、不要 markdown 代码块。\n\n"
            "【对标原文】\n" + SOURCE_PLACEHOLDER + "\n\n【高赞评论】\n" + comment_lines
        )
    # 无评论:不锚定评论(评论可能误导选题),仅凭原文 + 分析自由出固定条数,角度各异。
    n = NO_COMMENT_TOPIC_COUNT
    return (
        f"你是抖音选题官。下面给出对标作品的提取文案,以及对{works}为什么成为爆款({BAOKUAN_DEF})的分析。\n"
        "请严格以【爆款原因分析】为指导(受众/钩子/方向都来自它),不要自己另设赛道、不要套固定公式。\n"
        f"任务:结合原文与分析,产出 {n} 个角度各异的全新选题,覆盖分析里不同的钩子/方向,不要互相重复。\n"
        f"必须产出恰好 {n} 个选题。\n\n"
        "【爆款原因分析】\n" + _format_analysis(analysis) + "\n\n"
        "输出严格的 JSON 数组,长度 " + str(n) + ",每项字段固定:\n"
        '{"index":<1..' + str(n) + '>,"title":"新选题(一句话,带钩子感,像爆款标题)",'
        '"angle":"切入角度(一句话,这题从哪个口子讲)",'
        '"why":"为什么可能爆(1句,贴合上面分析的钩子/受众)","potential":7}\n'
        "potential 是 1-10 的爆款潜力分。只输出 JSON 数组,不要解释、不要 markdown 代码块。\n\n"
        "【对标原文】\n" + SOURCE_PLACEHOLDER
    )


def _render_topic_prompt(template: str, items: list[dict[str, str]]) -> str:
    """把模板里的 $source 占位替换成真实原文块(去重)。模板没有 $source 则原样返回。"""
    blocks, _ = _source_blocks(items)
    return template.replace(SOURCE_PLACEHOLDER, blocks)


def _gen_for_model(
    model: str, caller: CallerFn, items: list[dict[str, str]],
    prompt: str, on_progress: ProgressFn, *, n_topics: int | None = None,
) -> list[dict[str, Any]]:
    """单模型出题:用给定 prompt 产 N 个选题,补 source_model。

    有评论:按 index/位置对齐回评论,补 anchor_comment。
    无评论(n_topics 指定):不锚定评论,顺序收至多 n_topics 个,anchor_comment 留空。
    """
    on_progress(f"鬼谷子: {model} 出题中...")
    raw = caller(prompt)
    parsed = _parse_arr(raw)
    has_comments = any(it["comment"] for it in items)
    if not has_comments:
        limit = n_topics or NO_COMMENT_TOPIC_COUNT
        out_free: list[dict[str, Any]] = []
        for t in parsed:
            if not isinstance(t, dict) or not str(t.get("title") or "").strip():
                continue
            out_free.append({
                "title": str(t.get("title")).strip(),
                "angle": str(t.get("angle") or "").strip(),
                "why": str(t.get("why") or "").strip(),
                "potential": t.get("potential", 5),
                "anchor_comment": "",  # 无评论:不锚定
                "source_model": model,
            })
            if len(out_free) >= limit:
                break
        on_progress(f"鬼谷子: {model} 产出 {len(out_free)} 个选题")
        return out_free
    # 按 index 对齐(模型可能乱序/缺漏);拿不到 index 的退化为按出现顺序填空位
    by_index: dict[int, dict[str, Any]] = {}
    leftover: list[dict[str, Any]] = []
    for t in parsed:
        if not isinstance(t, dict) or not str(t.get("title") or "").strip():
            continue
        idx = t.get("index")
        if isinstance(idx, int) and 1 <= idx <= len(items) and idx not in by_index:
            by_index[idx] = t
        else:
            leftover.append(t)
    out: list[dict[str, Any]] = []
    for i, it in enumerate(items, 1):
        t = by_index.get(i) or (leftover.pop(0) if leftover else None)
        if t is None:
            continue  # 该评论该模型没出题,不硬造
        out.append({
            "title": str(t.get("title")).strip(),
            "angle": str(t.get("angle") or "").strip(),
            "why": str(t.get("why") or "").strip(),
            "potential": t.get("potential", 5),
            "anchor_comment": it["comment"],  # 锚定评论以入参为准,不信模型回显
            "source_model": model,
        })
    on_progress(f"鬼谷子: {model} 产出 {len(out)} 个选题")
    return out


def generate_topics(
    items: list[dict[str, str]] | None = None,
    analysis: dict[str, Any] | None = None,
    prompt: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
    *,
    require_comment: bool = True,
) -> dict[str, Any]:
    """第二步:双模型并行出选题。merge topic_store。

    prompt 传入(用户编辑后的提示词模板,含 $source 占位)则用它;否则按 analysis 拼默认模板。
    两个模型用同一份(替换 $source 后的)prompt。返回里带 prompt(模板,含 $source)供前端回显/再编辑。
    返回 {candidates, topics(扁平), out, added, prompt}(向后兼容卧龙/artifacts)。

    require_comment=False:无评论「直接拆解」,仅凭原文+分析自由出固定条数(不逐条锚定评论)。
    """
    norm = _normalize_items(items, require_comment=require_comment)
    if not norm:
        raise ValueError("guiguzi.generate_topics: items 为空,需要原文或带评论的 [{text, comment}, ...]")
    has_comments = any(it["comment"] for it in norm)
    n = len(norm) if has_comments else NO_COMMENT_TOPIC_COUNT
    template = prompt if (prompt and prompt.strip()) else _build_topic_prompt_template(norm, analysis)
    exec_prompt = _render_topic_prompt(template, norm)
    label = f"{len(norm)} 条评论" if has_comments else f"{len(norm)} 篇原文(无评论)"
    on_progress(f"鬼谷子: {label} -> opus/deepseek 双模型并行,各出 {n} 个选题...")
    callers = _callers(timeout_seconds)
    candidates: dict[str, dict[str, Any]] = {}
    n_topics = None if has_comments else n
    with ThreadPoolExecutor(max_workers=len(callers)) as ex:
        futs = {m: ex.submit(_gen_for_model, m, fn, norm, exec_prompt, on_progress, n_topics=n_topics)
                for m, fn in callers.items()}
        for m, fut in futs.items():
            try:
                candidates[m] = {"topics": fut.result(), "error": None}
            except Exception as exc:  # noqa: BLE001 — 单模型失败收成 error,不抛
                candidates[m] = {"topics": [], "error": f"{type(exc).__name__}: {exc}"}
                on_progress(f"鬼谷子: {m} 失败 - {exc}")

    flat = [t for m in MODELS for t in candidates.get(m, {}).get("topics", [])]
    merged = topic_store.merge(flat, source="guiguzi")
    on_progress(f"鬼谷子: 入库 {merged['added']} 条新选题,跳过 {merged['skipped']} 条重复")
    return {
        "candidates": candidates,
        "topics": flat,
        "out": str(topic_store.default_topics_path()),
        "added": merged["added"],
        "prompt": template,
    }


def _pick_analysis(analyses: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """端到端自动选一份分析:优先 opus,缺则 deepseek,都没有则 None(出题不带指导)。"""
    for m in MODELS:
        a = (analyses.get(m) or {}).get("analysis")
        if a:
            return a
    return None


def run(
    items: list[dict[str, str]] | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """端到端:analyze(找原因) → 自动取 opus 分析(缺则 deepseek) → generate_topics(出选题)。

    供卧龙/CLI/自动场景用(无人工介入)。前端走 analyze / generate_topics 两步交互版。
    返回在 generate_topics 结果上附 analysis(双模型分析) + chosen_analysis(自动选定那份)。
    """
    analyses = analyze(items, timeout_seconds=timeout_seconds, on_progress=on_progress)
    chosen = _pick_analysis(analyses)
    result = generate_topics(items, chosen, timeout_seconds=timeout_seconds, on_progress=on_progress)
    result["analysis"] = analyses
    result["chosen_analysis"] = chosen
    return result


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof guiguzi", description="鬼谷子: 两步流双模型选题官")
    parser.add_argument("--items", help="JSON: [{\"text\":...,\"comment\":...}, ...](与 --items-file 二选一)")
    parser.add_argument("--items-file", help="含上述 JSON 的文件路径")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    if args.items_file:
        raw = Path(args.items_file).read_text(encoding="utf-8")
    elif args.items:
        raw = args.items
    else:
        parser.error("需要 --items 或 --items-file")
        return 2
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        parser.error(f"items 不是合法 JSON: {exc}")
        return 2

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(items=items, timeout_seconds=args.timeout, on_progress=on_progress)
    chosen = result.get("chosen_analysis") or {}
    print("\n===== 鬼谷子·爆款原因(自动选定) =====")
    print(_format_analysis(chosen))
    print("\n===== 鬼谷子选题(双模型) =====")
    for model in MODELS:
        cand = result["candidates"].get(model, {})
        if cand.get("error"):
            print(f"\n[{model}] 失败: {cand['error']}")
            continue
        print(f"\n[{model}] {len(cand.get('topics', []))} 个选题:")
        for i, t in enumerate(cand.get("topics", []), 1):
            print(f"  ({i}) (潜力 {t.get('potential')}) {t.get('title')}")
            print(f"      锚定评论: {t.get('anchor_comment')}")
    print(f"\n选题库已落盘: {result['out']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
