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

from ncds_opus_factory.common import benchmark_store, tikhub_client

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "state" / "benchmark"
COLLECTED = ROOT / "state" / "figure_collected"
BENCH_DB = ROOT / "state" / "shenkuo" / "benchmark.db"  # 指标层 SQLite(时间序列)
TINGWU_DIR = ROOT / "skills" / "tingwu-asr" / "scripts"
MAIN_ENV = ROOT / ".env"  # 仓库根 .env(已 gitignore,模板见 .env.example);不再写死服务器绝对路径

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
# 截帧:静止帧检测 —— 只采「动画演完的稳定构图」
# --------------------------------------------------------------------------- #
def _extract_frames(
    video_path: Path, out_dir: Path, max_frames: int = 8,
    sample_s: float = 0.3, still_th: float = 2.0, min_still: int = 2,
) -> list[Path]:
    """采画面停留段的「最终静止帧」,不取 scene 切变的过渡帧。

    沈括只采静态素材(动效由下游 figure_talk 渲染时后期加);scene 切变帧是元素正飞入/移动的
    过渡瞬间,抠图会带灰边残影。这里按帧差分找「画面停住」的连续段(diff<still_th 且时长>=min_still),
    取每段末帧(动画完全演完的干净构图),段间再按相似度去重,超量则均匀取。
    """
    import cv2
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(fps * sample_s))
    samples: list = []  # [(bgr 原帧, 灰度小图)]
    i = 0
    while True:
        ret, fr = cap.read()
        if not ret:
            break
        if i % step == 0:
            g = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (160, 120)).astype(np.int16)
            samples.append((fr, g))
        i += 1
    cap.release()
    if not samples:
        return []

    diffs = [0.0] + [float(np.mean(np.abs(samples[k][1] - samples[k - 1][1]))) for k in range(1, len(samples))]
    # 连续静止段(diff<still_th 且段长>=min_still),取段末帧=动画演完的最终静态构图
    segs: list[list[int]] = []
    cur: list[int] = []
    for k in range(len(samples)):
        if diffs[k] < still_th:
            cur.append(k)
        else:
            if len(cur) >= min_still:
                segs.append(cur)
            cur = []
    if len(cur) >= min_still:
        segs.append(cur)

    reps: list[int] = []
    prev = None
    for seg in segs:
        r = seg[-1]
        g = samples[r][1]
        if prev is not None and float(np.mean(np.abs(g - prev))) < still_th:
            continue  # 与上一张几乎相同 -> 去重
        prev = g
        reps.append(r)

    if len(reps) > max_frames:  # 超量则均匀取
        sel = np.linspace(0, len(reps) - 1, max_frames).astype(int)
        reps = [reps[j] for j in sel]

    frames: list[Path] = []
    for n, r in enumerate(reps, 1):
        fp = out_dir / f"frame_{n:03d}.jpg"
        cv2.imwrite(str(fp), samples[r][0])
        frames.append(fp)
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
    max_frames: int = 8, engine: str = "threshold", top_comments: int = 20,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    meta = meta or {}
    entry: dict[str, Any] = {
        "aweme_id": aweme_id, "desc": meta.get("desc", ""), "digg": meta.get("digg", 0),
        "status": {},
    }
    # 展示元数据(沈括详情页直接渲染:作者/话题/四项数据)
    if meta.get("author"):
        entry["author"] = meta["author"]
    if meta.get("hashtags"):
        entry["hashtags"] = meta["hashtags"]
    stats = {k: meta[k] for k in ("digg", "comment", "share", "collect") if meta.get(k) is not None}
    if stats:
        entry["stats"] = stats
    # 封面图:落盘成 <aweme_id>.cover.jpg,App 走 /artifacts/files/ 取
    cover = author_dir / f"{aweme_id}.cover.jpg"
    if meta.get("cover_url") and not cover.exists():
        tikhub_client.download_cover(meta["cover_url"], cover)
    if cover.exists():
        entry["cover"] = _rel(cover)

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
        # 提取文字直接嵌进 entry,App 不用再拉文件(超长截断,口播稿远到不了这个量)
        entry["text"] = txt.read_text(encoding="utf-8").strip()[:3000]

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

    # 5. 采集 top 赞评论(热门序 + 早停,几次调用即可锁定;评论是受众反馈,喂对标拆解)
    if top_comments > 0:
        comments_path = author_dir / f"{aweme_id}.comments.json"
        if comments_path.exists():
            on_progress(f"[{aweme_id}] 评论已存在,跳过")
            entry["status"]["comments"] = "cached"
        else:
            try:
                rows = tikhub_client.fetch_top_comments(aweme_id, top_n=top_comments, on_progress=on_progress)
                comments_path.write_text(json.dumps(
                    {"aweme_id": aweme_id, "generated_at": int(time.time()), "top_n": top_comments, "items": rows},
                    ensure_ascii=False, indent=2), encoding="utf-8")
                on_progress(f"[{aweme_id}] top {len(rows)} 评论已落盘")
                entry["status"]["comments"] = "ok"
            except Exception as e:  # noqa: BLE001 — 单条评论失败不拖垮整批
                on_progress(f"[{aweme_id}] 评论采集异常: {type(e).__name__}: {e}")
                entry["status"]["comments"] = f"error:{type(e).__name__}"
        if comments_path.exists():
            entry["comments"] = _rel(comments_path)
            # 高赞评论直接嵌进 entry(按赞数排好),App 详情页直接渲染,不用再拉文件
            try:
                items = json.loads(comments_path.read_text(encoding="utf-8")).get("items", [])
                items.sort(key=lambda c: c.get("digg", 0), reverse=True)
                entry["top_comments"] = [
                    {"nickname": c.get("nickname", ""), "text": c.get("text", ""),
                     "digg": c.get("digg", 0), "ip": c.get("ip", "")}
                    for c in items[:top_comments]
                ]
            except Exception:  # noqa: BLE001 — 评论嵌入失败不影响 entry 主体
                pass
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
    top_comments: int = 20,
    refresh_only: bool = False,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """采集一个对标号(--author sec_uid)的 top 高赞作品,或单条(--aweme)。

    refresh_only=True 时只跑"拉列表 -> 写指标层(SQLite 时间序列)",跳过深采,给高频 cron 用。
    返回 {author_dir, all_posts?, collected:[entry...]}(同时落 all_posts.json + collected.json)。
    """
    if not author and not aweme:
        raise ValueError("需要 --author <sec_uid> 或 --aweme <aweme_id>")

    # 单条模式:验证链路用。aweme 接受 纯数字id/分享短链/整段分享口令
    if aweme:
        author_dir = BENCH / "adhoc"
        author_dir.mkdir(parents=True, exist_ok=True)
        on_progress(f"沈括: 单条采集 {aweme}")
        aweme_id = tikhub_client.resolve_aweme_id(aweme)
        if not aweme_id:
            raise ValueError(f"解析不出 aweme_id: {aweme}(支持纯数字 id 或抖音分享链接)")
        if aweme_id != aweme.strip():
            on_progress(f"短链解析: -> aweme_id {aweme_id}")
        # 取展示元数据(标题/作者/话题/数据/封面);失败不阻塞主链路
        meta: dict[str, Any] = {}
        try:
            meta = tikhub_client.extract_meta(tikhub_client.fetch_one_video_detail(aweme_id))
            if meta.get("desc"):
                on_progress(f"《{meta['desc'][:36]}》 @{meta.get('author', '')} 赞 {meta.get('digg', 0)}")
        except Exception as e:  # noqa: BLE001
            on_progress(f"元数据获取失败(不阻塞): {type(e).__name__}: {e}")
        entry = collect_one(aweme_id, author_dir, meta=meta, max_frames=max_frames, engine=engine,
                            top_comments=top_comments, on_progress=on_progress)
        _write_collected(author_dir, [entry])
        on_progress(f"沈括完成: {author_dir}")
        ret: dict[str, Any] = {"author_dir": str(author_dir), "collected": [entry]}
        # 回传展示标题/副题:任务卡显示作品信息,不显示分享链接。
        # 标题剥掉内嵌的 #话题(详情页有专门的话题 chips,不重复);副题只留 @作者。
        if entry.get("desc"):
            title = entry["desc"]
            for t in entry.get("hashtags") or []:
                title = title.replace(f"#{t}", "")
            ret["task_title"] = " ".join(title.split()) or entry["desc"]
        if entry.get("author"):
            ret["task_subtitle"] = f"@{entry['author']}"
        return ret

    # 作者模式:拉作品列表 -> 写指标层 -> (refresh_only 止步) -> 选高赞 top -> 逐条采集
    author_dir = BENCH / f"author_{author}"
    author_dir.mkdir(parents=True, exist_ok=True)
    on_progress(f"沈括启动: 拉作者作品(sec_uid={author[:16]}...)")
    # refresh-only 要广覆盖(更新历史作品指标),默认拉更多;深采模式只需够挑 top
    pull_n = max_posts or (200 if refresh_only else max(top * 2, 30))
    posts = tikhub_client.fetch_user_posts(author, max_items=pull_n, on_progress=on_progress)
    (author_dir / "all_posts.json").write_text(
        json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
    on_progress(f"拉到 {len(posts)} 条作品,落 all_posts.json")

    # 指标层:写身份 + 追加变化的快照(时间序列)
    ts = int(time.time())
    conn = benchmark_store.connect(BENCH_DB)
    try:
        stat = benchmark_store.record_refresh(conn, author, posts, ts)
    finally:
        conn.close()
    on_progress(f"指标层: 作品 {stat['posts']} 条,新增快照 {stat['snapshots']} 条 -> {_rel(BENCH_DB)}")

    if refresh_only:
        on_progress("refresh-only: 跳过深采")
        return {"author_dir": str(author_dir), "all_posts": len(posts), "collected": [], "snapshots": stat["snapshots"]}

    posts.sort(key=lambda p: p.get("digg", 0), reverse=True)
    chosen = posts[:top]
    collected: list[dict] = []
    for i, p in enumerate(chosen, 1):
        on_progress(f"=== 采集 {i}/{len(chosen)}: {p['aweme_id']} ({p.get('digg')}赞) ===")
        try:
            entry = collect_one(p["aweme_id"], author_dir, meta=p, max_frames=max_frames, engine=engine,
                                top_comments=top_comments, on_progress=on_progress)
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
    parser.add_argument("--top-comments", type=int, default=20, help="每条采集高赞评论数(0=不采)")
    parser.add_argument("--refresh-only", action="store_true",
                        help="只拉列表+写指标层(SQLite 时间序列),跳过深采(高频 cron 用)")
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        author=args.author, aweme=args.aweme, top=args.top,
        max_frames=args.frames, max_posts=args.max_posts,
        top_comments=args.top_comments, refresh_only=args.refresh_only,
        on_progress=on_progress,
    )
    print(json.dumps({
        "author_dir": result["author_dir"],
        "all_posts": result.get("all_posts"),
        "collected": len(result["collected"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
