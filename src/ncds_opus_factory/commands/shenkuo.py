"""/shenkuo —— 沈括：对标号情报 / 素材采集 agent（离线批处理）。

沈括,《梦溪笔谈》博物采集、记录见闻 —— 盯对标号,把原料拉回来拆成两类喂下游:
- **文案**(听悟转写 .paraformer.json + .txt)→ 喂 benchmark 拆解 / 鬼谷子选题
- **画面素材**(ffmpeg 截帧 + rembg 抠图)→ 对标素材池,人工/后续筛进吴道子 figure_lib

设计原则(和柳永/吴道子/伯牙对仗):
- 复用现成:TikHub 下载(common/tikhub_client)、听悟转写(skills/tingwu-asr)、ffmpeg、rembg。
- **不碰飞书**:不用 commands/asr.py(它把结果发飞书),直接调 tingwu_transcribe。
- 幂等:已下载/已转写/已截帧/已抠图的跳过,可反复跑、断点续。
- 离线批处理:`nof shenkuo --author <sec_uid>`,能手动 / 被 cron / watchdog 调度。

库契约见 plan;产物落本地 state/(NAS 同步是后续)。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.common import tikhub_client

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "state" / "benchmark"
COLLECTED = ROOT / "state" / "figure_collected"
TINGWU_DIR = ROOT / "skills" / "tingwu-asr" / "scripts"
MAIN_ENV = Path("/root/projects/ncds-opus-studio/.env")  # worktree 无 .env,DASHSCOPE key 在主仓库

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


def _rel(p: Path | str) -> str:
    """相对仓库根的路径;落在根外(如测试 tmp)时退化为绝对路径,不抛错。"""
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)


# --------------------------------------------------------------------------- #
# 转写:复用 skills/tingwu-asr(听悟/Paraformer),monkeypatch key 用主仓库 .env
# --------------------------------------------------------------------------- #
def _read_dashscope_key() -> str | None:
    """优先 env DASHSCOPE_API_KEY,其次主仓库 .env;都没有则返回 None(让 tingwu 走 openclaw 默认)。"""
    if os.getenv("DASHSCOPE_API_KEY"):
        return os.environ["DASHSCOPE_API_KEY"]
    if MAIN_ENV.exists():
        for line in MAIN_ENV.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DASHSCOPE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _transcribe(video_path: Path, on_progress: ProgressFn) -> tuple[dict | None, str]:
    """调 tingwu_transcribe.transcribe_file,返回 (结果 dict, 纯文本)。"""
    if str(TINGWU_DIR) not in sys.path:
        sys.path.insert(0, str(TINGWU_DIR))
    import tingwu_transcribe as tw  # type: ignore

    key = _read_dashscope_key()
    if key:
        tw.get_api_key = lambda: key  # 用主仓库 .env 的 key,而非 openclaw 默认
    result = tw.transcribe_file(str(video_path))
    if result is None:
        return None, ""
    text = tw.extract_text(result)
    result_dict = json.loads(json.dumps(result, default=lambda o: getattr(o, "__dict__", str(o))))
    return result_dict, text


# --------------------------------------------------------------------------- #
# 截帧(ffmpeg 场景切变,失败回退均匀抽)
# --------------------------------------------------------------------------- #
def _extract_frames(video_path: Path, out_dir: Path, max_frames: int = 8) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pat = str(out_dir / "frame_%03d.jpg")
    # 先按场景切变取「内容变化点」,更可能抓到不同素材
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_path),
         "-vf", "select='gt(scene,0.3)',scale=720:-1", "-vsync", "vfr",
         "-frames:v", str(max_frames), pat],
        check=False,
    )
    frames = sorted(out_dir.glob("frame_*.jpg"))
    if not frames:  # 回退:每 3 秒一帧
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_path),
             "-vf", "fps=1/3,scale=720:-1", "-frames:v", str(max_frames), pat],
            check=False,
        )
        frames = sorted(out_dir.glob("frame_*.jpg"))
    return frames


# --------------------------------------------------------------------------- #
# 抠图:裁舞台区 + 阈值分割(对标号 = 黑剪影/彩色道具 on 浅纸背景)
# --------------------------------------------------------------------------- #
# 舞台区裁剪比例(left,top,right,bottom):对标号 PPT 模板固定版式,去顶标签栏/底字幕/角落水印。
# 因对标号/模板而异,需要时调这里(后续可做成每号配置)。实测「自我重塑」「健康熬夜」两号通用。
STAGE_CROP = (0.04, 0.13, 0.96, 0.82)
# 前景占比合格区间:太低=空帧/纯背景,太高=纯色过场(如纯黑标题帧),都丢弃。
FG_MIN, FG_MAX = 0.02, 0.85


def _crop_stage(im, crop: tuple[float, float, float, float] = STAGE_CROP):
    """裁出中间舞台区,去掉顶标签 / 底字幕 / 水印声明 —— 只留主要内容素材。"""
    w, h = im.size
    l, t, r, b = crop
    return im.crop((int(w * l), int(h * t), int(w * r), int(h * b)))


def _threshold_cutout(im):
    """黑剪影 + 彩色道具 on 浅纸背景 -> 透明抠图。返回 (RGBA, 前景占比)。

    前景 = 暗(剪影线条) 或 鲜艳(彩色道具如蔬菜);浅米纸纹背景(高亮度低饱和)透明。
    比 rembg(u2net 显著性)更适合简笔画:保留全部元素、边缘锐利、不下大模型。
    """
    import numpy as np
    from PIL import Image

    arr = np.asarray(im.convert("RGB")).astype(np.float32)
    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    sat = (arr.max(2) - arr.min(2)) / (arr.max(2) + 1e-6)
    fg = (lum < 150) | (sat > 0.30)
    alpha = fg.astype(np.uint8) * 255
    rgba = np.dstack([arr.astype(np.uint8), alpha])
    return Image.fromarray(rgba, "RGBA"), float(fg.mean())


def _rembg_cutout(im):
    """rembg(u2net)去背景:留作彩色照片类复杂素材的备选(--engine rembg)。"""
    from rembg import remove
    return remove(im.convert("RGBA"))


def _cutout(frames: list[Path], out_dir: Path, engine: str = "threshold", crop: bool = True) -> list[Path]:
    """逐帧:裁舞台区 -> 抠图 -> 按前景占比过滤纯色/空帧 -> 存透明 png。"""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    cutouts: list[Path] = []
    for fr in frames:
        with Image.open(fr) as raw:
            im = _crop_stage(raw) if crop else raw.convert("RGB")
            if engine == "rembg":
                res, ratio = _rembg_cutout(im), None
            else:
                res, ratio = _threshold_cutout(im)
        if ratio is not None and not (FG_MIN <= ratio <= FG_MAX):
            continue  # 纯色过场 / 空帧,丢弃
        dst = out_dir / (fr.stem + ".png")
        res.save(dst)
        cutouts.append(dst)
    return cutouts


# --------------------------------------------------------------------------- #
# 采集单条作品(幂等:每步看产物存在跳过)
# --------------------------------------------------------------------------- #
def collect_one(
    aweme_id: str, author_dir: Path, meta: dict | None = None,
    max_frames: int = 8, engine: str = "threshold", on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    meta = meta or {}
    entry: dict[str, Any] = {
        "aweme_id": aweme_id, "desc": meta.get("desc", ""), "digg": meta.get("digg", 0),
        "status": {},
    }

    # 1. 下载 mp4
    video = author_dir / f"{aweme_id}.mp4"
    if video.exists() and video.stat().st_size > 0:
        on_progress(f"[{aweme_id}] mp4 已存在,跳过下载")
        entry["status"]["download"] = "cached"
    else:
        url = tikhub_client.fetch_video_url(aweme_id)
        if not url:
            entry["status"]["download"] = "no_url"
            return entry
        tikhub_client.download_video(url, video)
        on_progress(f"[{aweme_id}] 下载完成")
        entry["status"]["download"] = "ok"
    entry["video"] = _rel(video)

    # 2. 转写文案
    para = author_dir / f"{aweme_id}.paraformer.json"
    txt = author_dir / f"{aweme_id}.txt"
    if para.exists():
        on_progress(f"[{aweme_id}] 转写已存在,跳过")
        entry["status"]["transcribe"] = "cached"
    else:
        try:
            result, text = _transcribe(video, on_progress)
            if result is not None:
                para.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                txt.write_text(text, encoding="utf-8")
                entry["status"]["transcribe"] = "ok"
            else:
                entry["status"]["transcribe"] = "failed"
        except Exception as e:  # noqa: BLE001 — 单条转写失败不拖垮整批
            on_progress(f"[{aweme_id}] 转写异常: {type(e).__name__}: {e}")
            entry["status"]["transcribe"] = f"error:{type(e).__name__}"
    if para.exists():
        entry["paraformer"] = _rel(para)
    if txt.exists():
        entry["txt"] = _rel(txt)

    # 3. 截帧
    frames_dir = author_dir / aweme_id / "frames"
    frames = sorted(frames_dir.glob("frame_*.jpg")) if frames_dir.exists() else []
    if frames:
        on_progress(f"[{aweme_id}] 截帧已存在 {len(frames)} 张")
    else:
        frames = _extract_frames(video, frames_dir, max_frames)
        on_progress(f"[{aweme_id}] 截帧 {len(frames)} 张")
    entry["frames"] = [_rel(f) for f in frames]

    # 4. 抠图
    cut_dir = COLLECTED / aweme_id
    cutouts = sorted(cut_dir.glob("*.png")) if cut_dir.exists() else []
    if cutouts:
        on_progress(f"[{aweme_id}] 抠图已存在 {len(cutouts)} 张")
    elif frames:
        try:
            cutouts = _cutout(frames, cut_dir, engine=engine)
            on_progress(f"[{aweme_id}] 抠图 {len(cutouts)} 张(舞台区/{engine})")
            entry["status"]["cutout"] = "ok"
        except Exception as e:  # noqa: BLE001
            on_progress(f"[{aweme_id}] 抠图异常: {type(e).__name__}: {e}")
            entry["status"]["cutout"] = f"error:{type(e).__name__}"
    entry["cutouts"] = [_rel(c) for c in cutouts]
    return entry


def _write_collected(author_dir: Path, collected: list[dict]) -> Path:
    path = author_dir / "collected.json"
    path.write_text(json.dumps({"generated_at": int(time.time()), "items": collected},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #
def run(
    author: str | None = None,
    aweme: str | None = None,
    top: int = 10,
    max_frames: int = 8,
    engine: str = "threshold",
    max_posts: int | None = None,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """采集一个对标号(--author sec_uid)的 top 高赞作品,或单条(--aweme)。

    返回 {author_dir, all_posts?, collected:[entry...]}(同时落 all_posts.json + collected.json)。
    """
    if not author and not aweme:
        raise ValueError("需要 --author <sec_uid> 或 --aweme <aweme_id>")

    # 单条模式:验证链路用
    if aweme:
        author_dir = BENCH / "adhoc"
        author_dir.mkdir(parents=True, exist_ok=True)
        on_progress(f"沈括: 单条采集 {aweme}")
        entry = collect_one(aweme, author_dir, max_frames=max_frames, engine=engine, on_progress=on_progress)
        _write_collected(author_dir, [entry])
        on_progress(f"沈括完成: {author_dir}")
        return {"author_dir": str(author_dir), "collected": [entry]}

    # 作者模式:拉作品列表 -> 选高赞 top -> 逐条采集
    author_dir = BENCH / f"author_{author}"
    author_dir.mkdir(parents=True, exist_ok=True)
    on_progress(f"沈括启动: 拉作者作品(sec_uid={author[:16]}...)")
    posts = tikhub_client.fetch_user_posts(
        author, max_items=max_posts or max(top * 2, 30), on_progress=on_progress
    )
    (author_dir / "all_posts.json").write_text(
        json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
    on_progress(f"拉到 {len(posts)} 条作品,落 all_posts.json")

    posts.sort(key=lambda p: p.get("digg", 0), reverse=True)
    chosen = posts[:top]
    collected: list[dict] = []
    for i, p in enumerate(chosen, 1):
        on_progress(f"=== 采集 {i}/{len(chosen)}: {p['aweme_id']} ({p.get('digg')}赞) ===")
        try:
            entry = collect_one(p["aweme_id"], author_dir, meta=p, max_frames=max_frames, engine=engine, on_progress=on_progress)
        except Exception as e:  # noqa: BLE001 — 单条失败不拖垮整批
            on_progress(f"  采集异常: {type(e).__name__}: {e}")
            entry = {"aweme_id": p["aweme_id"], "status": {"error": str(e)}}
        collected.append(entry)
    _write_collected(author_dir, collected)
    on_progress(f"沈括完成: {len(collected)} 条采集,产物在 {author_dir} + {COLLECTED}")
    return {"author_dir": str(author_dir), "all_posts": len(posts), "collected": collected}


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof shenkuo", description="沈括: 对标号情报/素材采集 agent")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--author", help="对标号 sec_user_id(拉其作品)")
    src.add_argument("--aweme", help="单条作品 aweme_id(验证链路用)")
    parser.add_argument("--top", type=int, default=10, help="作者模式:采集高赞前 N 条")
    parser.add_argument("--frames", type=int, default=8, help="每条视频截帧数上限")
    parser.add_argument("--engine", default="threshold", choices=["threshold", "rembg"],
                        help="抠图引擎:threshold(默认,简笔画矢量级)/rembg(彩色照片类备选)")
    parser.add_argument("--max-posts", type=int, default=None, help="作者模式:拉作品列表上限")
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        author=args.author, aweme=args.aweme, top=args.top,
        max_frames=args.frames, max_posts=args.max_posts, on_progress=on_progress,
    )
    print(json.dumps({
        "author_dir": result["author_dir"],
        "all_posts": result.get("all_posts"),
        "collected": len(result["collected"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
