"""/wudaozi —— 吴道子：抖音认知成片的「美术 / 视觉」agent。

冷链标记（task-3.7，2026-06-22）：这是旧 figure_talk/剪影成片实现，仍可经 CLI、
`/tasks`、`/commands` 与 app catalog 触达，但**不是**当前 web/engine 主生产链。
当前主链是 paper_card_talk_015 的 storyboard -> tts -> image -> render_015。后续重写
吴道子/伯牙/render 下游时应直接替换这条冷链，不必为旧实现做兼容层。

吴道子,画圣 —— 把柳永的文字口播稿「分镜」成可渲染的剪影成片。

设计原则(和柳永/伯牙对仗):
- 不生成、不下载素材。人物剪影一律从**用户维护的本地库**(assets/figure_lib)里「按语义选用」。
- 分工:opus 只做导演判断(切句 + 概括每句语义/强调词);**选剪影/图标/动效走规则打分**
  (确定性、可解释、库扩了自动覆盖)。和伯牙 select_bgm/plan_sfx 一样"接口稳定,后续可换 LLM"。
- 硬闸门:beat 的 zh 拼起来必须≈原稿(不丢句/不改词),不过线就把 opus 打回重切一次。
- 产物 = 一个可直接渲染的 figure_talk 实例(beats.js/json + storyboard.json + beats.qc.json + 复制好素材)。

输入:柳永成稿(纯文字口播 .md / 或直接传文本)。
库契约见 assets/figure_lib/README.md;渲染模板见 templates/figure_talk/README.md。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.common.opus_cli import call_opus

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LIBRARY = ROOT / "assets" / "figure_lib"
TEMPLATE_DIR = ROOT / "src" / "ncds_opus_factory" / "templates" / "figure_talk"
ICONS_JS = ROOT / "src" / "ncds_opus_factory" / "templates" / "stickman" / "icons.js"
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("NOF_WUDAOZI_TIMEOUT", "600"))

# 赛道词表(从 storyboard 的 tag 里粗猜 scene,给 select_figure 当线索;和 boya._guess_scene 对仗)。
SCENES = ("认知", "职场", "成长", "心理", "健康")
# beat 类型 -> Ken Burns 动效。body 句左右交替平移,避免每句都一样的呆板缩放。
KIND_TO_MOTION = {"hook": "zoom-in", "reveal": "zoom-in", "golden": "still", "close": "zoom-out"}

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


def _build_job_id() -> str:
    return f"WDZ_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


# --------------------------------------------------------------------------- #
# 输入:读脚本
# --------------------------------------------------------------------------- #
def read_script(script_path: str | Path | None = None, script_text: str | None = None) -> tuple[str, str]:
    """读柳永稿,返回 (正文, 标题提示)。

    柳永稿格式:首个非空行是标题,其后空行再正文。把标题单独抽出当 title 提示,
    正文(剥 markdown # 标题/空行)用于 opus 切句 + 不丢句校验 —— 标题不计入正文,避免被读两遍。
    """
    if script_text is not None:
        text = script_text
    elif script_path is not None:
        text = Path(script_path).read_text(encoding="utf-8")
    else:
        raise ValueError("需要 script_path 或 script_text 之一")

    lines = text.splitlines()
    nonempty_idx = [i for i, ln in enumerate(lines) if ln.strip()]
    title_hint = ""
    body_start = 0
    if nonempty_idx:
        first = lines[nonempty_idx[0]].strip().lstrip("#").strip()
        if len(first) <= 25:  # 短 = 标题特征
            title_hint = first
            body_start = nonempty_idx[0] + 1
    body = "\n".join(
        ln.strip() for ln in lines[body_start:]
        if ln.strip() and not ln.strip().startswith("#")
    )
    return body, title_hint


_PUNCT = re.compile(r"[\s，。、；：！？“”‘’（）()\[\]【】—…·.,!?;:\"'-]+")


def _normalize(s: str) -> str:
    """去标点空白,用于不丢句校验的字符级比对。"""
    return _PUNCT.sub("", s or "")


# --------------------------------------------------------------------------- #
# 库与图标清单加载
# --------------------------------------------------------------------------- #
def load_library(library_dir: Path) -> list[dict]:
    """读 figure_lib/library.json 的 figures 列表。文件不在就报清楚指引(对仗 boya.load_library)。"""
    manifest = library_dir / "library.json"
    if not manifest.exists():
        raise FileNotFoundError(
            f"剪影库清单不存在: {manifest}\n"
            f"  先把人物剪影放进 {library_dir / 'figures'},再跑:\n"
            f"  python3 scripts/scan_figure_lib.py            # 扫真素材\n"
            f"  python3 scripts/scan_figure_lib.py --placeholders   # 无真素材时造占位库打通管线"
        )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return data.get("figures", []) or []


_ICON_RE = re.compile(
    r'"([a-zA-Z0-9_-]+)":\s*\{[^}]*?enter:\s*"(\w+)"[^}]*?keywords:\s*\[([^\]]*)\]'
)


def load_icons_catalog(icons_js: Path = ICONS_JS) -> list[dict]:
    """从 icons.js(AUTO-GENERATED)正则抽 {id, enter, keywords},给 select_icons 用。

    不 import JS、不另维护副本:icons.js 是真源,扩图标后这里自动跟上。
    """
    if not icons_js.exists():
        return []
    text = icons_js.read_text(encoding="utf-8")
    catalog: list[dict] = []
    for cid, enter, kwstr in _ICON_RE.findall(text):
        kws = re.findall(r'"([^"]+)"', kwstr)
        catalog.append({"id": cid, "enter": enter, "keywords": kws})
    return catalog


# --------------------------------------------------------------------------- #
# opus 分镜(切句 + 概括语义;唯一的 LLM 步骤)。原走 codex(scodex shim),订阅失效后改 opus
# --------------------------------------------------------------------------- #
def _build_prompt(script_body: str, title_hint: str, extra_warning: str = "") -> str:
    hint = f"\n建议标题(可参考/改写,<=14字):{title_hint}\n" if title_hint else "\n"
    warn = f"\n【上一轮问题,务必修正】{extra_warning}\n" if extra_warning else ""
    return (
        "你是抖音认知口播视频的分镜师。把下面这篇口播稿切成逐句分镜,每句对应一个画面。\n\n"
        "【硬规则(违反则作废)】\n"
        "- zh 字段必须是原稿的【连续逐字切分】:把全文不重不漏地切成若干句,不得增、删、改、缩写任何字(标点可省略)。\n"
        "- 按【完整句子或自然意群】切,一个分镜 = 一个完整意思(约 15-40 字),【不要按逗号切成半句碎片】。\n"
        "  正例:原文「打工人,被领导临时塞活,你一开口说我尽量,就已经把锅接到自己怀里了」=一个意群,切成 1 个分镜。\n"
        "  反例(禁止):把它切成「打工人」「被领导临时塞活」「你一开口说我尽量」这种逗号碎片。\n"
        "- 全片通常 20-50 个分镜;只有句子特别长时才在自然停顿处断成两段。\n\n"
        "【每句输出字段】\n"
        "- zh: 这句台词原文\n"
        "- concept: 这句画面的核心概念(一句话,中文)\n"
        "- keywords: 3-6 个语义关键词(中文,用于匹配画面素材),如 [\"手机\",\"分心\",\"拖延\"]\n"
        "- emphasis: 0-3 个最该用小图标强调的词\n"
        "- kind: hook(开场钩子,仅第一句)/reveal(反转或反常识)/golden(金句)/close(收尾,仅最后一句)/body(其余)\n"
        "- title: 仅第一句给(一句话标题,<=14字)\n"
        "- tag: 仅第一句给(赛道标签,如 \"职场 · 认知\")\n\n"
        "只输出 JSON 数组,不要任何解释。\n"
        + hint + warn +
        "\n【原稿】\n" + script_body
    )


def _extract_json_array(text: str) -> list[dict]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    i, j = text.find("["), text.rfind("]")
    if i >= 0 and j > i:
        text = text[i : j + 1]
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("opus 输出不是 JSON 数组")
    return data


def storyboard_via_codex(
    script_body: str,
    title_hint: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    extra_warning: str = "",
    on_progress: ProgressFn = _noop,
) -> list[dict]:
    """调 opus 切句分镜,返回 storyboard 列表。

    原走 codex(gpt-5.5,scodex shim);codex 订阅失效后改调本机 opus(claude-opus-4-8 + effort max)。
    函数名保留 storyboard_via_codex 不变(run() 与测试按名引用)。call_opus 空输出即抛,无需再判空。
    """
    prompt = _build_prompt(script_body, title_hint, extra_warning)
    on_progress("吴道子调 opus 分镜中...")
    out = call_opus(prompt, timeout_seconds=timeout_seconds)
    return _extract_json_array(out)


# --------------------------------------------------------------------------- #
# 选用:剪影 / 图标 / 动效(规则版;后续可换 LLM,接口不变。对仗 boya.select_bgm)
# --------------------------------------------------------------------------- #
def select_figure(library: list[dict], keywords: list[str], scene: str | None) -> dict | None:
    """按 keywords 交集 + scene 命中 + concept 子串打分,选最贴的主体剪影。零命中返回 None。"""
    if not library:
        return None
    kset = set(keywords or [])

    def score(f: dict) -> int:
        s = len(kset & set(f.get("keywords") or [])) * 2
        if scene and scene in (f.get("scene") or []):
            s += 1
        concept = f.get("concept", "")
        if concept and any((k in concept) or (concept in k) for k in kset):
            s += 1
        return s

    best = max(library, key=score)
    if score(best) <= 0:
        return None
    chosen = dict(best)
    chosen["_score"] = score(best)
    return chosen


def select_icons(catalog: list[dict], emphasis: list[str], max_n: int = 2) -> list[str]:
    """按强调词匹配图标 keywords,选 0-max_n 个点缀。宁缺毋滥。"""
    if not catalog or not emphasis:
        return []
    picked: list[str] = []
    for word in emphasis:
        for ic in catalog:
            kws = ic.get("keywords") or []
            if word in kws or any((word in k) or (k in word) for k in kws):
                if ic["id"] not in picked:
                    picked.append(ic["id"])
                break
        if len(picked) >= max_n:
            break
    return picked[:max_n]


def pick_motion(kind: str | None, index: int) -> str:
    """beat 类型 -> Ken Burns。body 句左右交替平移。"""
    if kind in KIND_TO_MOTION:
        return KIND_TO_MOTION[kind]
    return "pan-left" if index % 2 == 0 else "pan-right"


# --------------------------------------------------------------------------- #
# 校验:不丢句硬闸门(确定性) + 软标注(校准期仅提示)
# --------------------------------------------------------------------------- #
def check_coverage(beats: list[dict], script_body: str, threshold: float = 0.95) -> dict:
    """beat 的 zh 拼接 ≈ 原稿正文(去标点后字符级比对)。低于阈值 = 丢句/改词,判 fail。"""
    a = _normalize(script_body)
    b = _normalize("".join(bt.get("zh", "") for bt in beats))
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    verdict = "pass" if ratio >= threshold else "fail"
    return {"verdict": verdict, "ratio": round(ratio, 4), "src_chars": len(a), "beats_chars": len(b)}


def soft_checks(beats: list[dict]) -> list[str]:
    """分镜软质检:句数/单句长度/无主体剪影占比。校准期仅标注,不打回(和柳永/伯牙质检对仗)。"""
    notes: list[str] = []
    n = len(beats)
    if n < 4:
        notes.append(f"分镜偏少({n} 句),口播可能太短")
    if n > 60:
        notes.append(f"分镜偏多({n} 句),节奏可能太碎")
    no_fig = sum(1 for b in beats if not b.get("figure"))
    if no_fig:
        notes.append(f"{no_fig}/{n} 句无主体剪影(库内无匹配,纯字幕);可扩充 figure_lib")
    for i, b in enumerate(beats):
        if len(b.get("zh", "")) > 40:
            notes.append(f"beat#{i+1} 偏长({len(b['zh'])}字),建议再切")
    return notes


# --------------------------------------------------------------------------- #
# 组装 beats + storyboard 明细
# --------------------------------------------------------------------------- #
def _guess_scene(storyboard: list[dict]) -> str | None:
    for sb in storyboard:
        tag = sb.get("tag", "") or ""
        for s in SCENES:
            if s in tag:
                return s
    return None


def build_beats(
    storyboard: list[dict], library: list[dict], catalog: list[dict], scene: str | None
) -> tuple[list[dict], list[dict]]:
    """把 opus 分镜 + 规则选用组装成渲染用 beats 和带 reason 的 storyboard 明细。"""
    beats: list[dict] = []
    detail: list[dict] = []
    for i, sb in enumerate(storyboard):
        kws = sb.get("keywords") or []
        fig = select_figure(library, kws, scene)
        icons = select_icons(catalog, sb.get("emphasis") or [])
        motion = pick_motion(sb.get("kind"), i)

        beat: dict[str, Any] = {"zh": sb.get("zh", "")}
        if fig:
            beat["figure"] = "figures/" + Path(fig["file"]).name
        if icons:
            beat["icons"] = icons
        beat["motion"] = motion
        if sb.get("title"):
            beat["title"] = sb["title"]
        if sb.get("tag"):
            beat["tag"] = sb["tag"]
        if sb.get("kind"):
            beat["kind"] = sb["kind"]
        beats.append(beat)

        hit = sorted(set(kws) & set((fig.get("keywords") if fig else []) or []))
        detail.append({
            "i": i + 1,
            "zh": beat["zh"],
            "kind": sb.get("kind"),
            "concept": sb.get("concept"),
            "figure": fig["file"] if fig else None,
            "figure_reason": (f"keywords∩={hit} scene={scene or '-'} score={fig['_score']}"
                              if fig else "库内无匹配 -> 纯字幕"),
            "icons": icons,
            "motion": motion,
        })
    return beats, detail


# --------------------------------------------------------------------------- #
# 落盘:复制模板 + icons + 选中剪影,写 beats/storyboard/qc
# --------------------------------------------------------------------------- #
def write_instance(
    out_dir: Path, beats: list[dict], detail: list[dict], qc: dict, library_dir: Path
) -> None:
    out = Path(out_dir)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "player.js", "render.mjs", "tts_gen.py", "README.md"):
        shutil.copy(TEMPLATE_DIR / name, out / name)
    shutil.copy(TEMPLATE_DIR / "assets" / "styles.css", out / "assets" / "styles.css")
    if ICONS_JS.exists():
        shutil.copy(ICONS_JS, out / "icons.js")

    # 复制选中的剪影到实例 figures/(去重)
    figdir = out / "figures"
    figdir.mkdir(exist_ok=True)
    used: set[str] = set()
    for b in beats:
        if not b.get("figure"):
            continue
        name = Path(b["figure"]).name
        if name in used:
            continue
        used.add(name)
        src = library_dir / "figures" / name
        if src.exists():
            shutil.copy(src, figdir / name)

    beats_js = "/* AUTO-GENERATED by wudaozi(吴道子). 勿手改:重跑 nof wudaozi 覆盖。 */\n" \
               "window.BEATS = " + json.dumps(beats, ensure_ascii=False, indent=2) + ";\n"
    (out / "beats.js").write_text(beats_js, encoding="utf-8")
    (out / "beats.json").write_text(json.dumps(beats, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "storyboard.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "beats.qc.json").write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #
def run(
    script_path: str | Path | None = None,
    script_text: str | None = None,
    library_dir: str | Path = DEFAULT_LIBRARY,
    out_dir: str | Path | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """把一篇柳永稿分镜成可渲染的 figure_talk 实例。

    返回 {job_id, out_dir, beats, storyboard_path, qc}(同时落盘实例)。
    """
    body, title_hint = read_script(script_path, script_text)
    if not body.strip():
        raise ValueError("脚本正文为空")
    lib = Path(library_dir)
    library = load_library(lib)
    catalog = load_icons_catalog()
    on_progress(f"吴道子启动: 正文 {len(body)} 字 · 库 figures={len(library)} · icons={len(catalog)}")

    storyboard = storyboard_via_codex(body, title_hint, timeout_seconds=timeout_seconds, on_progress=on_progress)
    scene = _guess_scene(storyboard)
    beats, detail = build_beats(storyboard, library, catalog, scene)
    qc = check_coverage(beats, body)
    on_progress(f"分镜 {len(beats)} 句 · 不丢句校验 {qc['verdict']}(ratio={qc['ratio']}) · scene={scene or '-'}")

    # 硬闸门 fail -> 打回重切一次(把丢字情况喂回 opus)
    if qc["verdict"] == "fail":
        on_progress("不丢句校验 fail,打回 opus 重切一次...")
        storyboard = storyboard_via_codex(
            body, title_hint, timeout_seconds=timeout_seconds,
            extra_warning=f"上次切句后字符覆盖率仅 {qc['ratio']},说明丢了句或改了字。务必逐字不漏地切分原稿。",
            on_progress=on_progress,
        )
        scene = _guess_scene(storyboard)
        beats, detail = build_beats(storyboard, library, catalog, scene)
        qc = check_coverage(beats, body)
        on_progress(f"重切后: {len(beats)} 句 · 不丢句校验 {qc['verdict']}(ratio={qc['ratio']})")

    qc["warnings"] = soft_checks(beats)
    if qc["warnings"]:
        on_progress("分镜软质检: " + "; ".join(qc["warnings"]))

    job_id = _build_job_id()
    out = Path(out_dir) if out_dir else (ROOT / "state" / "figure_jobs" / job_id)
    write_instance(out, beats, detail, qc, lib)
    on_progress(f"吴道子完成: {out}")
    return {
        "job_id": job_id,
        "out_dir": str(out),
        "beats": beats,
        "storyboard_path": str(out / "storyboard.json"),
        "qc": qc,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof wudaozi", description="吴道子: 把柳永脚本分镜成剪影成片")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--script", help="柳永成稿路径(.md)")
    src.add_argument("--text", help="直接传入脚本文本")
    parser.add_argument("--library", default=str(DEFAULT_LIBRARY), help="本地剪影库目录")
    parser.add_argument("--out", default=None, help="输出实例目录(默认 state/figure_jobs/<job_id>)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        script_path=args.script,
        script_text=args.text,
        library_dir=args.library,
        out_dir=args.out,
        timeout_seconds=args.timeout,
        on_progress=on_progress,
    )
    print(json.dumps(
        {"job_id": result["job_id"], "out_dir": result["out_dir"],
         "beats": len(result["beats"]), "qc": result["qc"]["verdict"]},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
