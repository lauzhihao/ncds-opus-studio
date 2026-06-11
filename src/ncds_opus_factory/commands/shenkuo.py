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
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.common import benchmark_store, cancel, tikhub_client

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


def _run_proc_cancellable(args: list[str], check: cancel.CheckFn,
                          timeout: float = 1800) -> None:
    """轮询式子进程:每秒看一次取消标记,取消则 SIGTERM(线程杀不掉,子进程随便杀)。"""
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while True:
        rc = proc.poll()
        if rc is not None:
            if rc != 0:
                raise RuntimeError(f"subprocess failed rc={rc}: {args[0]}")
            return
        if check():
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise cancel.TaskCancelled("cancelled during subprocess")
        if time.time() - t0 > timeout:
            proc.kill()
            raise RuntimeError(f"subprocess timeout: {args[0]}")
        time.sleep(1)


def _extract_audio(video: Path, audio_dir: Path, on_progress: ProgressFn = _noop,
                   check: cancel.CheckFn = lambda: False) -> dict[str, str]:
    """声音素材:抽原声 + Demucs 分离 人声/伴奏(BGM 与次级音效在伴奏轨)。

    幂等(产物在则跳过);Demucs 失败只保留原声,不拖垮采集主链路。
    分离耗时约 0.5x 实时(CPU),走可取消子进程,取消时 1 秒内 SIGTERM。
    """
    import shutil

    out: dict[str, str] = {}
    audio_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"

    original = audio_dir / "original.mp3"
    if not original.exists():
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(video),
                        "-vn", "-codec:a", "libmp3lame", "-b:a", "128k", str(original)],
                       check=True, timeout=300)
        on_progress("原声已抽出")
    out["original"] = _rel(original)

    vocals, bgm = audio_dir / "vocals.mp3", audio_dir / "bgm.mp3"
    if not (vocals.exists() and bgm.exists()):
        sep_dir = audio_dir / "_demucs"
        try:
            on_progress("Demucs 分离人声/伴奏中(约 0.5x 实时)…")
            _run_proc_cancellable(
                [sys.executable, "-m", "demucs.separate", "--two-stems=vocals",
                 "-n", "htdemucs", "--mp3", "-o", str(sep_dir), str(original)],
                check)
            stem = sep_dir / "htdemucs" / original.stem
            (stem / "vocals.mp3").replace(vocals)
            (stem / "no_vocals.mp3").replace(bgm)
            shutil.rmtree(sep_dir, ignore_errors=True)
            on_progress("声音分离完成: 人声 + 伴奏(BGM/音效)")
        except cancel.TaskCancelled:
            shutil.rmtree(sep_dir, ignore_errors=True)   # 半成品清掉,恢复后重跑
            raise
        except Exception as e:  # noqa: BLE001 — 分离失败保留原声
            on_progress(f"声音分离失败(保留原声): {type(e).__name__}")
    if vocals.exists():
        out["vocals"] = _rel(vocals)
    if bgm.exists():
        out["bgm"] = _rel(bgm)
    return out


def _clean_transcript(raw: str, on_progress: ProgressFn = _noop) -> str | None:
    """听写稿清洗:首选 qwen(同音错别字/语义断句的长尾规则穷举不了),
    失败/无 key 时回退本地规则兜底。在转写支线内执行,与 Demucs 等支线并行。"""
    text = (raw or "").strip()
    if len(text) < 20:
        return None
    cleaned = _clean_with_qwen(text, on_progress)
    if cleaned:
        return cleaned
    return _clean_local(text, on_progress)


def _clean_with_qwen(raw: str, on_progress: ProgressFn = _noop) -> str | None:
    """qwen 清洗校对:纠同音错别字、去口头语、按语义断句加标点;不得增删事实。"""
    key = _read_dashscope_key()
    if not key:
        return None
    import requests

    prompt = (
        "下面是一份语音听写稿(ASR 原文)。请清洗校对:纠正同音/近音错别字,"
        "去除口头语和无意义语气词,按语义断句、补全标点、适当分段。"
        "不得增删事实,不得改写表达风格,不得编造原文没有的内容。"
        "直接输出清洗后的正文,不要任何解释或前后缀。\n\n" + raw[:6000]
    )
    try:
        on_progress("听写稿清洗中(qwen)…")
        resp = requests.post(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "qwen-plus", "temperature": 0.2,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=(15, 120),
        )
        resp.raise_for_status()
        cleaned = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        if not cleaned:
            return None
        on_progress(f"清洗完成(qwen): {len(raw)} -> {len(cleaned)} 字")
        return cleaned
    except Exception as e:  # noqa: BLE001 — qwen 失败交给本地兜底
        on_progress(f"qwen 清洗失败,本地规则兜底: {type(e).__name__}")
        return None


def _clean_local(raw: str, on_progress: ProgressFn = _noop) -> str | None:
    """本地规则兜底:去孤立语气词、规整空白与重复标点(无网络/无 key 时至少可读)。"""
    text = re.sub(r"(?:(?<=^)|(?<=[,，。!！?？、\s]))(呃+|嗯+|哎+|诶+)(?=[,，。!！?？、\s]|$)", "", raw)
    text = re.sub(r"[ \t]+", "", text)
    text = re.sub(r"，{2,}", "，", text)
    text = re.sub(r"。{2,}", "。", text)
    cleaned = text.strip()
    if not cleaned or cleaned == raw.strip():
        return None
    on_progress(f"本地清洗(兜底): {len(raw)} -> {len(cleaned)} 字")
    return cleaned


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
#
# 并行编排(能并行的不串行):
#   评论 只依赖 aweme_id —— 与下载同时起跑
#   下载完成后 转写→清洗 / 声音(ffmpeg+Demucs) / 截帧→抠图 三线并行
# 各支线只写 entry 里互不重叠的键;进度回调用锁串行化,防 events.jsonl 行交错。
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

    _lock = threading.Lock()

    def report(msg: str) -> None:
        with _lock:
            on_progress(msg)

    # 协作式取消:在主线程取 checker(thread-local 不传子线程),闭包给各支线。
    # 支线在步骤边界 guard();Demucs 子进程内部按秒轮询可被 SIGTERM。
    _check = cancel.current()

    def guard() -> None:
        cancel.checkpoint(_check)

    video = author_dir / f"{aweme_id}.mp4"

    def branch_download() -> bool:
        if video.exists() and video.stat().st_size > 0:
            report(f"[{aweme_id}] mp4 已存在,跳过下载")
            entry["status"]["download"] = "cached"
        else:
            url = tikhub_client.fetch_video_url(aweme_id)
            if not url:
                entry["status"]["download"] = "no_url"
                return False
            tikhub_client.download_video(url, video)
            report(f"[{aweme_id}] 下载完成")
            entry["status"]["download"] = "ok"
        entry["video"] = _rel(video)
        return True

    def branch_transcribe() -> None:
        guard()
        para = author_dir / f"{aweme_id}.paraformer.json"
        txt = author_dir / f"{aweme_id}.txt"
        if para.exists():
            report(f"[{aweme_id}] 转写已存在,跳过")
            entry["status"]["transcribe"] = "cached"
        else:
            try:
                result, text = _transcribe(video, report)
                if result is not None:
                    para.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    txt.write_text(text, encoding="utf-8")
                    entry["status"]["transcribe"] = "ok"
                else:
                    entry["status"]["transcribe"] = "failed"
            except cancel.TaskCancelled:
                raise
            except Exception as e:  # noqa: BLE001 — 单条转写失败不拖垮整批
                report(f"[{aweme_id}] 转写异常: {type(e).__name__}: {e}")
                entry["status"]["transcribe"] = f"error:{type(e).__name__}"
        if para.exists():
            entry["paraformer"] = _rel(para)
        if txt.exists():
            entry["txt"] = _rel(txt)
            raw_text = txt.read_text(encoding="utf-8").strip()
            guard()
            # 清洗(qwen 优先,本地兜底);原文留 .txt,清洗版存 .clean.txt
            clean = author_dir / f"{aweme_id}.clean.txt"
            if not clean.exists() and raw_text:
                cleaned = _clean_transcript(raw_text, report)
                if cleaned:
                    clean.write_text(cleaned, encoding="utf-8")
            if clean.exists():
                entry["text"] = clean.read_text(encoding="utf-8").strip()[:3000]
                entry["text_raw"] = _rel(txt)
            else:
                entry["text"] = raw_text[:3000]

    def branch_audio() -> None:
        guard()
        try:
            audio = _extract_audio(video, author_dir / aweme_id / "audio", report, check=_check)
            if audio:
                entry["audio"] = audio
                entry["status"]["audio"] = "ok"
        except cancel.TaskCancelled:
            raise
        except Exception as e:  # noqa: BLE001 — 声音素材失败不拖垮整批
            report(f"声音素材异常: {type(e).__name__}: {e}")
            entry["status"]["audio"] = f"error:{type(e).__name__}"

    def branch_frames() -> None:
        guard()
        frames_dir = author_dir / aweme_id / "frames"
        frames = sorted(frames_dir.glob("frame_*.jpg")) if frames_dir.exists() else []
        if frames:
            report(f"[{aweme_id}] 截帧已存在 {len(frames)} 张")
        else:
            frames = _extract_frames(video, frames_dir, max_frames)
            report(f"[{aweme_id}] 截帧 {len(frames)} 张")
        entry["frames"] = [_rel(f) for f in frames]
        cut_dir = COLLECTED / aweme_id
        cutouts = sorted(cut_dir.glob("*.png")) if cut_dir.exists() else []
        if cutouts:
            report(f"[{aweme_id}] 抠图已存在 {len(cutouts)} 张")
        elif frames:
            guard()
            try:
                cutouts = _cutout(frames, cut_dir, engine=engine)
                report(f"[{aweme_id}] 抠图 {len(cutouts)} 张(舞台区/{engine})")
                entry["status"]["cutout"] = "ok"
            except cancel.TaskCancelled:
                raise
            except Exception as e:  # noqa: BLE001
                report(f"[{aweme_id}] 抠图异常: {type(e).__name__}: {e}")
                entry["status"]["cutout"] = f"error:{type(e).__name__}"
        entry["cutouts"] = [_rel(c) for c in cutouts]

    def branch_comments() -> None:
        guard()
        comments_path = author_dir / f"{aweme_id}.comments.json"
        if comments_path.exists():
            report(f"[{aweme_id}] 评论已存在,跳过")
            entry["status"]["comments"] = "cached"
        else:
            try:
                rows = tikhub_client.fetch_top_comments(aweme_id, top_n=top_comments, on_progress=report)
                comments_path.write_text(json.dumps(
                    {"aweme_id": aweme_id, "generated_at": int(time.time()), "top_n": top_comments, "items": rows},
                    ensure_ascii=False, indent=2), encoding="utf-8")
                report(f"[{aweme_id}] top {len(rows)} 评论已落盘")
                entry["status"]["comments"] = "ok"
            except cancel.TaskCancelled:
                raise
            except Exception as e:  # noqa: BLE001 — 单条评论失败不拖垮整批
                report(f"[{aweme_id}] 评论采集异常: {type(e).__name__}: {e}")
                entry["status"]["comments"] = f"error:{type(e).__name__}"
        if comments_path.exists():
            entry["comments"] = _rel(comments_path)
            # 高赞评论嵌进 entry(>10 赞阈值,按赞数排好),App 直接渲染
            try:
                items = json.loads(comments_path.read_text(encoding="utf-8")).get("items", [])
                items = [c for c in items if c.get("digg", 0) > 10]
                items.sort(key=lambda c: c.get("digg", 0), reverse=True)
                top = [
                    {"nickname": c.get("nickname", ""), "text": c.get("text", ""),
                     "digg": c.get("digg", 0), "ip": c.get("ip", "")}
                    for c in items[:top_comments]
                ]
                if top:
                    entry["top_comments"] = top
            except Exception:  # noqa: BLE001 — 评论嵌入失败不影响 entry 主体
                pass

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_comments = ex.submit(branch_comments) if top_comments > 0 else None
        if branch_download():
            for f in [ex.submit(branch_transcribe), ex.submit(branch_audio), ex.submit(branch_frames)]:
                f.result()
        if f_comments is not None:
            f_comments.result()
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
