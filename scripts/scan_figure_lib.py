#!/usr/bin/env python3
"""扫描本地剪影库,生成/更新 library.json 骨架(给吴道子 wudaozi 选素材用)。

把人物剪影 figures/*.{png,jpg,svg,webp} 丢进 assets/figure_lib/ 后跑本脚本:
- 用 PIL 自动补 w/h(svg 从 viewBox/width-height 抠)
- 新文件补一份空标签骨架(keywords/scene/concept 留给人或吴道子后续填)
- **已存在的条目只更新尺寸,不覆盖你已填的标签**

另带 --placeholders:在空库里合成几张占位黑剪影(简单人形),
用于在没有真素材时把"脚本->选图->成片"端到端管线先跑通。

设计与 scripts/scan_audio_lib.py(伯牙音源库)完全对仗。

用法:
  python3 scripts/scan_figure_lib.py                 # 扫默认库 assets/figure_lib
  python3 scripts/scan_figure_lib.py --lib <dir>
  python3 scripts/scan_figure_lib.py --placeholders  # 先造占位素材再扫
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIB = ROOT / "assets" / "figure_lib"
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".svg")

# 占位剪影:每条 (文件名, 标签骨架)。图形都用同一个简笔人形(占位只验链路),靠标签区分语义。
PLACEHOLDER_FIGURES = [
    ("person_phone.png", {"keywords": ["手机", "分心", "低头", "沉迷", "刷手机"], "scene": ["认知", "职场"], "concept": "低头看手机"}),
    ("person_think.png", {"keywords": ["思考", "纠结", "疑问", "权衡", "犹豫"], "scene": ["认知"], "concept": "托腮思考"}),
    ("person_present.png", {"keywords": ["讲解", "表达", "观点", "说", "告诉"], "scene": ["认知", "职场"], "concept": "站立讲解"}),
    ("person_run.png", {"keywords": ["行动", "执行", "开始", "改变", "出发"], "scene": ["成长", "职场"], "concept": "迈步向前"}),
    ("person_tired.png", {"keywords": ["累", "内耗", "疲惫", "压力", "emo"], "scene": ["认知", "职场"], "concept": "低头疲惫"}),
]


# --------------------------------------------------------------------------- #
# 尺寸读取
# --------------------------------------------------------------------------- #
_SVG_VIEWBOX = re.compile(r'viewBox\s*=\s*"[\d.]+\s+[\d.]+\s+([\d.]+)\s+([\d.]+)"')
_SVG_WH = re.compile(r'\b(width|height)\s*=\s*"([\d.]+)')


def image_size(path: Path) -> tuple[int | None, int | None]:
    """返回 (w, h);读不出来返回 (None, None)(不让扫库因个别坏文件中断)。"""
    if path.suffix.lower() == ".svg":
        try:
            txt = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return (None, None)
        m = _SVG_VIEWBOX.search(txt)
        if m:
            return (int(float(m.group(1))), int(float(m.group(2))))
        wh = {k: v for k, v in _SVG_WH.findall(txt)}
        if "width" in wh and "height" in wh:
            return (int(float(wh["width"])), int(float(wh["height"])))
        return (None, None)
    try:
        from PIL import Image  # 延迟 import:只有位图才需要 PIL
        with Image.open(path) as im:
            return (im.width, im.height)
    except Exception:
        return (None, None)


# --------------------------------------------------------------------------- #
# 占位剪影合成(PIL 画简笔黑人形)
# --------------------------------------------------------------------------- #
def _draw_figure(path: Path) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (800, 800), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink = (22, 18, 14, 255)
    # 紧凑构图:整体在画布上 75%,底部留白,避免底对齐时探进字幕区(占位 demo 也体面)
    d.ellipse([338, 96, 462, 220], fill=ink)                       # 头
    d.rounded_rectangle([330, 228, 470, 452], radius=60, fill=ink)  # 躯干
    d.rounded_rectangle([298, 256, 340, 408], radius=22, fill=ink)  # 左臂
    d.rounded_rectangle([460, 256, 502, 408], radius=22, fill=ink)  # 右臂
    d.rounded_rectangle([352, 440, 398, 600], radius=24, fill=ink)  # 左腿
    d.rounded_rectangle([402, 440, 448, 600], radius=24, fill=ink)  # 右腿
    img.save(path)


def make_placeholders(lib: Path) -> None:
    """合成占位剪影 + **种入已知标签**(scan 会保留),让 --placeholders 真正打通管线。"""
    figdir = lib / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    seed: dict[str, list] = {"figures": []}
    for name, tags in PLACEHOLDER_FIGURES:
        _draw_figure(figdir / name)
        seed["figures"].append({"file": f"figures/{name}", **tags})
    # 先把标签写进 manifest,随后的 scan() 只补尺寸、不覆盖这些标签
    (lib / "library.json").write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[placeholders] 合成 {len(PLACEHOLDER_FIGURES)} 张占位剪影(含标签) 到 {figdir}")


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #
def scan(lib: Path) -> dict:
    manifest = lib / "library.json"
    existing: dict[str, dict] = {}
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for e in data.get("figures", []) or []:
            existing[e["file"]] = e

    figures: list[dict] = []
    figdir = lib / "figures"
    if figdir.is_dir():
        for f in sorted(figdir.iterdir()):
            if f.suffix.lower() not in IMG_EXT:
                continue
            rel = f"figures/{f.name}"
            entry = dict(existing.get(rel, {}))  # 保留已填标签
            entry["file"] = rel
            w, h = image_size(f)
            entry["w"], entry["h"] = w, h
            entry.setdefault("keywords", [])
            entry.setdefault("scene", [])
            entry.setdefault("concept", "")
            figures.append(entry)
    return {"figures": figures}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="扫描本地剪影库,生成 library.json 骨架")
    p.add_argument("--lib", default=str(DEFAULT_LIB))
    p.add_argument("--placeholders", action="store_true", help="先合成占位剪影(用于无真素材时打通管线)")
    args = p.parse_args(argv)
    lib = Path(args.lib).resolve()

    if args.placeholders:
        make_placeholders(lib)

    result = scan(lib)
    manifest = lib / "library.json"
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[scan] figures={len(result['figures'])} -> {manifest}")
    miss = [e["file"] for e in result["figures"] if not e.get("keywords")]
    if miss:
        print(f"[scan] 提醒: {len(miss)} 张剪影还没填 keywords: {', '.join(miss)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
