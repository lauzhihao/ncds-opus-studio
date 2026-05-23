#!/usr/bin/env python3
"""按 SCENES 顺序本地调 gpt-image-2 生成插图，转 WebP 落库。

- 输出尺寸：1536×1024（gpt-image-2 landscape 最高），落到 1640×740 的卡片
  area 走 image-slot 默认 cover，上下各裁约 176px，提示词里要给出"上下方留白"
  以避免主体被裁。
- 落地：pictures/NN-<scene-id>.webp，编号跟 SCENES 出场顺序对齐
- 幂等：目标 webp 存在则跳过；--force 强制重生
- 章节封面 ch1-ch5 跳过不生成（player.js 里走 CSS chapter-card 渲染）

依赖：本机 Pillow（pip install Pillow），环境变量 GPT_IMAGE2_BASE_URL 和
GPT_IMAGE2_API_KEY（在 ~/.zshrc 中），以及本机
~/.codex/skills/gpt-image/scripts/gpt_image_gen.py。
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BEATS_JS = HERE / "beats.js"
PIC_DIR = HERE / "pictures"
GEN_SCRIPT = Path.home() / ".codex/skills/gpt-image/scripts/gpt_image_gen.py"
SIZE = os.environ.get("PIC_SIZE", "1536x1024")
QUALITY = os.environ.get("PIC_QUALITY", "auto")
NO_TEXT_SUFFIX = (
    " 严格要求：整张图绝对不能出现任何中文字、汉字、英文字母、阿拉伯数字或者标点。"
    " 所有标签、标牌、招牌、徽章、吊牌内部必须完全空白（留出贴标签的位置），"
    " 不要画任何文字或文字纹理。"
)


def extract_scenes() -> list[dict]:
    """node 进 assets 目录把 SCENES 按 BEATS 顺序 dump 成 JSON。"""
    script = (
        "global.window = global;"
        "require('./beats.js');"
        "const seen = new Set(), order = [];"
        "for (const b of window.BEATS) { if (!seen.has(b.scene)) { seen.add(b.scene); order.push(b.scene); } }"
        "process.stdout.write(JSON.stringify(order.map((id, idx) => ({"
        "  id, index: idx, is_chapter: id.startsWith('ch'),"
        "  prompt: (window.SCENES[id] || {}).prompt || ''"
        "}))));"
    )
    res = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, cwd=str(HERE), check=True,
    )
    return json.loads(res.stdout)


def generate_one(scene: dict, force: bool) -> str:
    """Returns 'ok' | 'skipped' | 'failed'."""
    idx = scene["index"]
    sid = scene["id"]
    nn = f"{idx + 1:02d}"
    out_path = PIC_DIR / f"{nn}-{sid}.webp"
    if out_path.exists() and not force:
        return "skipped"

    prompt = scene["prompt"].strip() + NO_TEXT_SUFFIX

    gen_out_dir = Path("/tmp") / "gpt-image" / f"010-{nn}-{sid}"
    shutil.rmtree(gen_out_dir, ignore_errors=True)
    gen_out_dir.mkdir(parents=True, exist_ok=True)

    print(f"  [{nn}/{sid}] generating ({SIZE} {QUALITY})...", flush=True)
    t0 = time.time()
    res = subprocess.run(
        [
            "python3", str(GEN_SCRIPT),
            "--out-dir", str(gen_out_dir),
            "--size", SIZE,
            "--quality", QUALITY,
            "--overwrite",
            "--prompt", prompt,
        ],
        capture_output=True, text=True, timeout=600,
    )
    if res.returncode != 0:
        tail = (res.stderr or res.stdout or "")[-500:]
        print(f"  ! [{nn}/{sid}] gpt-image FAILED in {time.time()-t0:.1f}s: {tail}", file=sys.stderr)
        return "failed"

    local_png = gen_out_dir / "image_01.png"
    if not local_png.exists():
        print(f"  ! [{nn}/{sid}] expected {local_png} not found", file=sys.stderr)
        return "failed"

    # Convert PNG → WebP, write atomically
    try:
        from PIL import Image
    except ImportError:
        print("  ! Pillow missing; install with: pip install --break-system-packages Pillow", file=sys.stderr)
        return "failed"

    img = Image.open(local_png).convert("RGB")
    tmp_out = out_path.with_suffix(out_path.suffix + ".part")
    img.save(tmp_out, format="WEBP", quality=85, method=6)
    tmp_out.rename(out_path)
    shutil.rmtree(gen_out_dir, ignore_errors=True)

    elapsed = time.time() - t0
    size_kb = out_path.stat().st_size / 1024
    print(f"  ✓ [{nn}/{sid}] {size_kb:.1f} KB ({elapsed:.1f}s)", flush=True)
    return "ok"


def main() -> int:
    PIC_DIR.mkdir(exist_ok=True)
    force = "--force" in sys.argv[1:]
    only = [a for a in sys.argv[1:] if not a.startswith("--")]

    scenes = extract_scenes()
    if only:
        scenes = [s for s in scenes if s["id"] in only]
        print(f"only scenes: {[s['id'] for s in scenes]}")

    total_eligible = sum(1 for s in scenes if not s["is_chapter"])
    print(f"target: {total_eligible} scenes ({SIZE} {QUALITY})")

    ok = sk = fail = 0
    for s in scenes:
        if s["is_chapter"]:
            continue
        result = generate_one(s, force=force)
        if result == "ok":
            ok += 1
        elif result == "skipped":
            sk += 1
        else:
            fail += 1
        time.sleep(0.5)

    print(f"\ndone. ok={ok} skipped={sk} failed={fail}")
    return 0 if fail == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
