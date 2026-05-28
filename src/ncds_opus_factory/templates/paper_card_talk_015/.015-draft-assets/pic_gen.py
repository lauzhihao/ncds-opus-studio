#!/usr/bin/env python3
"""按 SCENES 出场顺序本地调 gpt-image-2 生成插图，转 WebP 落库。

数据源：episode.json（meta.slug / image.size / image.quality / image.noTextHint / beats / scenes）
- 输出尺寸：image.size（默认 1536×1024 landscape）
- 落地：pictures/<scene-id>.webp（player.js 的 picSrcFor 直接读 sceneId）
- 幂等：目标 webp 存在则跳过；--force 强制重生
- 章节封面 ch1-ch5 跳过不生成（player.js 里走 CSS chapter-card 渲染）
- 并发：默认 5 个线程并行调用 gpt-image-2；`--jobs N` / `-j N` / `PIC_JOBS=N` 覆盖

依赖：本机 Pillow（pip install Pillow），环境变量 GPT_IMAGE2_BASE_URL / GPT_IMAGE2_API_KEY，
以及本机 ~/.codex/skills/gpt-image/scripts/gpt_image_gen.py。
"""
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
EPISODE_JSON = HERE / "episode.json"
PIC_DIR = HERE / "pictures"
GEN_SCRIPT = Path.home() / ".codex/skills/gpt-image/scripts/gpt_image_gen.py"


def load_episode() -> dict:
    return json.loads(EPISODE_JSON.read_text(encoding="utf-8"))


def extract_scenes(episode: dict) -> list[dict]:
    """按 BEATS 出场顺序返回去重的 scene 列表（含 prompt）。"""
    beats = episode.get("beats", [])
    scenes_def = episode.get("scenes", {})
    seen: set[str] = set()
    order: list[str] = []
    for b in beats:
        sid = b.get("scene")
        if sid and sid not in seen:
            seen.add(sid)
            order.append(sid)
    return [
        {
            "id": sid,
            "index": i,
            "is_chapter": sid.startswith("ch"),
            "prompt": (scenes_def.get(sid) or {}).get("prompt", ""),
        }
        for i, sid in enumerate(order)
    ]


def generate_one(scene: dict, *, slug: str, no_text_hint: str, size: str, quality: str, force: bool) -> str:
    """Returns 'ok' | 'skipped' | 'failed'."""
    idx = scene["index"]
    sid = scene["id"]
    nn = f"{idx + 1:02d}"
    out_path = PIC_DIR / f"{sid}.webp"
    if out_path.exists() and not force:
        return "skipped"

    prompt = scene["prompt"].strip()
    if no_text_hint:
        prompt = prompt + " " + no_text_hint

    gen_out_dir = Path("/tmp") / "gpt-image" / f"{slug}-{sid}"
    shutil.rmtree(gen_out_dir, ignore_errors=True)
    gen_out_dir.mkdir(parents=True, exist_ok=True)

    print(f"  [{nn}/{sid}] generating ({size} {quality})...", flush=True)
    t0 = time.time()
    res = subprocess.run(
        [
            "python3", str(GEN_SCRIPT),
            "--out-dir", str(gen_out_dir),
            "--size", size,
            "--quality", quality,
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


def parse_jobs(argv: list[str]) -> tuple[int, list[str]]:
    """提取 --jobs N / -j N，返回 (jobs, 剩余 argv)。"""
    jobs = int(os.environ.get("PIC_JOBS") or 5)
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--jobs", "-j") and i + 1 < len(argv):
            jobs = int(argv[i + 1]); i += 2; continue
        if a.startswith("--jobs="):
            jobs = int(a.split("=", 1)[1]); i += 1; continue
        rest.append(a); i += 1
    return max(1, jobs), rest


def main() -> int:
    PIC_DIR.mkdir(exist_ok=True)
    jobs, rest = parse_jobs(sys.argv[1:])
    force = "--force" in rest
    only = [a for a in rest if not a.startswith("--")]

    episode = load_episode()
    slug = (episode.get("meta") or {}).get("slug") or "episode"
    image_cfg = episode.get("image") or {}
    size = os.environ.get("PIC_SIZE") or image_cfg.get("size") or "1536x1024"
    quality = os.environ.get("PIC_QUALITY") or image_cfg.get("quality") or "auto"
    no_text_hint = image_cfg.get("noTextHint") or ""

    scenes = extract_scenes(episode)
    if only:
        scenes = [s for s in scenes if s["id"] in only]
        print(f"only scenes: {[s['id'] for s in scenes]}")

    eligible = [s for s in scenes if not s["is_chapter"]]
    print(f"target: {len(eligible)} scenes ({size} {quality}) · jobs={jobs}")

    ok = sk = fail = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(
                generate_one, s,
                slug=slug, no_text_hint=no_text_hint,
                size=size, quality=quality, force=force,
            ): s for s in eligible
        }
        for fut in as_completed(futures):
            try:
                result = fut.result()
            except Exception as e:
                sid = futures[fut]["id"]
                print(f"  ! [{sid}] worker crashed: {e}", file=sys.stderr)
                fail += 1
                continue
            if result == "ok":
                ok += 1
            elif result == "skipped":
                sk += 1
            else:
                fail += 1

    print(f"\ndone. ok={ok} skipped={sk} failed={fail}")
    return 0 if fail == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
