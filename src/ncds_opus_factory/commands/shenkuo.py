"""/shenkuo —— 沈括：对标号情报 / 素材采集 agent（离线批处理）。

沈括,《梦溪笔谈》博物采集、记录见闻 —— 盯对标号,把原料拉回来拆成两类喂下游:
- **文案**(听悟转写 .paraformer.json + .txt)→ 喂 benchmark 拆解 / 鬼谷子选题
- **画面素材**(ffmpeg 截帧 + rembg 抠图)→ 对标素材池,人工/后续筛进吴道子 figure_lib

设计原则(和柳永/吴道子/伯牙对仗):
- **薄组合层**:沈括本身只做"编排 + 缓存",底层能力一律调 `common/capabilities`
  (transcribe/separate_audio/extract_frames/cutout) + `common/tikhub_client`(下载/评论)。
  能力的"唯一实现"在那两处,沈括不自带实现,别处要复用直接调 capabilities。
- **底层复用**:单作品采集走 capabilities.transcribe(听悟/Paraformer),不复刻 ASR 实现。
- 幂等 + 缓存:产物按 平台+作品id 落 works 仓库(works_repo),已采的跳过,可反复跑、断点续、跨对标号复用。
- 离线批处理:`nof shenkuo --author <sec_uid>`,能手动 / 被 cron / watchdog 调度。

⚠️ 边界:URL→文章管线(skills/video-pipeline + asr_service,喂老画布/引擎015链)是另一条链路,
不是沈括这套采集能力的实现,两者别混用。

库契约见 plan;产物落本地 state/works/(NAS 同步是后续)。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ncds_opus_core.common import cancel

from ncds_opus_factory.common import benchmark_store, capabilities, tikhub_client, works_repo
from ncds_opus_factory.common.capabilities import diarize as diarize_cap

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "state" / "benchmark"
COLLECTED = ROOT / "state" / "figure_collected"
BENCH_DB = ROOT / "state" / "shenkuo" / "benchmark.db"  # 指标层 SQLite(时间序列)
# entry["text"](提取文案/下游 rw 源)的安全上限:防极端长稿撑爆 payload。
# 旧值 3000 会把 rw 改写源也截断,故抬到 2 万(典型稿几千字,等于不截)。
_TEXT_CAP = 20000
SUPPORTED_PLATFORMS = {"douyin", "tiktok", "youtube"}

ProgressFn = Callable[[str], None]


def _noop(_text: str) -> None:
    return None


def _rel(p: Path | str) -> str:
    """相对仓库根的路径;落在根外(如测试 tmp)时退化为绝对路径,不抛错。"""
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)


def _platform(p: str | None) -> str:
    v = (p or "douyin").strip().lower()
    if v not in SUPPORTED_PLATFORMS:
        raise ValueError(f"不支持的平台: {v}（支持 douyin/tiktok/youtube）")
    return v


def _safe_author_id(author: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", (author or "").strip()) or "unknown"


def author_benchmark_dir(platform: str, author: str) -> Path:
    """作者 all_posts/collected 索引目录；抖音保留历史路径，非抖音带平台前缀。"""
    platform = _platform(platform)
    if platform == "douyin":
        return BENCH / f"author_{author}"
    return BENCH / f"author_{platform}_{_safe_author_id(author)}"


def _metric_author_key(platform: str, author: str) -> str:
    return author if _platform(platform) == "douyin" else f"{_platform(platform)}:{author}"


def _source_url_for(platform: str, aweme_id: str, author: str | None = None) -> str:
    if platform == "youtube":
        return f"https://www.youtube.com/watch?v={aweme_id}"
    if platform == "tiktok":
        handle = (author or "_").strip().lstrip("@") or "_"
        return f"https://www.tiktok.com/@{handle}/video/{aweme_id}"
    return f"https://www.douyin.com/video/{aweme_id}"


def _video_ref_from_input(text: str, platform: str, source_url: str | None = None) -> tikhub_client.VideoRef | None:
    """Resolve a single-work input, honoring an explicit non-Douyin platform for raw IDs."""
    if source_url:
        ref = tikhub_client.resolve_video_ref(source_url)
        if ref:
            return ref
    ref = tikhub_client.resolve_video_ref(text)
    if ref:
        if platform != "douyin" and ref.platform == "douyin" and (text or "").strip() == ref.video_id:
            return tikhub_client.VideoRef(platform, ref.video_id, _source_url_for(platform, ref.video_id))
        return ref
    raw = (text or "").strip()
    if platform != "douyin" and raw:
        return tikhub_client.VideoRef(platform, raw, source_url or _source_url_for(platform, raw))
    return None


def _adopt_legacy(aweme_id: str, author_dir: Path, mapping: dict[Path, Path]) -> None:
    """历史产物原地兼容(不迁移):旧位置在、新仓库位缺 -> 建相对软链指过去。

    软链后各支线 exists() 即命中、就地复用旧文件,不搬字节、不重跑采集。
    软链失败(权限/跨盘/不支持)静默忽略 —— 退化为对该项重新采集,不致命。
    """
    for old, new in mapping.items():
        try:
            if new.exists() or new.is_symlink():
                continue  # 已有真身或已建链(含悬空链)都不重复处理
            if not old.exists():
                continue
            new.parent.mkdir(parents=True, exist_ok=True)
            new.symlink_to(os.path.relpath(old, new.parent))
        except OSError:
            pass


def top_comments_from_items(items: list[dict], top_n: int) -> list[dict]:
    """从评论原始列表筛高赞(>10 赞)、按赞降序、取前 top_n,塑成前端直接渲染的精简结构。

    collect_one 的 branch_comments 与轻量刷新 refresh_stats_comments 共用,口径一致。
    """
    items = [c for c in items if c.get("digg", 0) > 10]
    items.sort(key=lambda c: c.get("digg", 0), reverse=True)
    return [
        {"nickname": c.get("nickname", ""), "text": c.get("text", ""),
         "digg": c.get("digg", 0), "ip": c.get("ip", "")}
        for c in items[:top_n]
    ]


@dataclass(frozen=True)
class _CollectPaths:
    wdir: Path
    video: Path
    para: Path
    timeline: Path
    txt: Path
    clean: Path
    speakers: Path
    dialogue: Path
    cover: Path
    comments_path: Path
    audio_dir: Path
    frames_dir: Path
    cut_dir: Path


def _collect_paths(platform: str, aweme_id: str) -> _CollectPaths:
    # 产物统一落作品仓库 state/works/{platform}/{aweme_id}/ —— 按平台+作品id 寻址。
    wdir = works_repo.work_dir(platform, aweme_id)
    return _CollectPaths(
        wdir=wdir,
        video=wdir / "video.mp4",
        para=wdir / "asr.paraformer.json",
        timeline=wdir / "asr.timeline.json",
        txt=wdir / "asr.txt",
        clean=wdir / "asr.clean.txt",
        speakers=wdir / "asr.speakers.json",
        dialogue=wdir / "dialogue.txt",
        cover=wdir / "cover.jpg",
        comments_path=wdir / "comments.json",
        audio_dir=wdir / "audio",
        frames_dir=wdir / "frames",
        cut_dir=wdir / "cutouts",
    )


def _asr_engine_for_platform(platform: str) -> str | None:
    return "whisper" if _platform(platform) in {"tiktok", "youtube"} else None


# 说话人分离默认只对多人对白 domain 开(重 CPU 步骤,别的 domain 用不上);
# NOF_DIARIZE_DOMAINS 逗号分隔可覆盖(留空 = 全关)。
_DIARIZE_DOMAINS_DEFAULT = frozenset({"comedy"})


def _diarize_domains() -> frozenset[str]:
    raw = os.getenv("NOF_DIARIZE_DOMAINS")
    if raw is None:
        return _DIARIZE_DOMAINS_DEFAULT
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _needs_diarization(domain: str | None) -> bool:
    return bool(domain) and domain.strip().lower() in _diarize_domains()


def _transcript_backend(path: Path) -> str:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(doc.get("backend") or "").strip().lower() if isinstance(doc, dict) else ""


def _transcript_cache_usable(paths: _CollectPaths, platform: str) -> bool:
    if not paths.para.exists() or not paths.txt.exists():
        return False
    expected = _asr_engine_for_platform(platform)
    if expected == "whisper":
        return _transcript_backend(paths.para) == "whisper"
    return True


def _transcript_result_empty(result: dict[str, Any] | None) -> bool:
    return isinstance(result, dict) and result.get("empty") is True


def _write_text_atomic(path: Path, text: str) -> None:
    """tmp + os.replace 原子落盘:worker 被 launchd 杀在写一半时,不留半截文件毒化缓存。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_speakers_doc(path: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _speakers_cache_usable(paths: _CollectPaths) -> dict[str, Any] | None:
    """speakers 缓存可用则返回解析后的 doc,否则 None(触发重跑)。

    不可用的情形:文件损坏(半截 JSON);或当年跑在混音源(original/video)上、
    而现在 Demucs 人声轨已就绪 —— 人声轨结果更准,值得重算一次。
    """
    if not paths.speakers.exists():
        return None
    doc = _load_speakers_doc(paths.speakers)
    if doc is None:
        return None
    if str(doc.get("source") or "") != "vocals.mp3" and (paths.audio_dir / "vocals.mp3").exists():
        return None
    return doc


def _speaker_count_from_file(path: Path) -> int | None:
    doc = _load_speakers_doc(path)
    count = doc.get("speakerCount") if doc else None
    return count if isinstance(count, int) else None


def _timestamp_ms(value: Any, *, seconds: bool = False) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if seconds:
        number *= 1000
    return max(0, int(round(number)))


def _normalize_timeline_words(
    words: Any,
    *,
    seconds: bool,
) -> list[dict[str, Any]]:
    if not isinstance(words, list):
        return []
    normalized: list[dict[str, Any]] = []
    for word in words:
        if not isinstance(word, dict):
            continue
        text = str(
            word.get("text")
            or word.get("word")
            or word.get("label")
            or ""
        )
        if not text:
            continue
        start_value = (
            word.get("start")
            if seconds
            else word.get("start_time", word.get("start"))
        )
        end_value = (
            word.get("end")
            if seconds
            else word.get("end_time", word.get("end"))
        )
        normalized.append({
            "text": text,
            "start_ms": _timestamp_ms(start_value, seconds=seconds),
            "end_ms": _timestamp_ms(end_value, seconds=seconds),
        })
    return normalized


def normalize_asr_timeline(asr_result: dict[str, Any]) -> dict[str, Any]:
    """Convert backend-specific Bcut/Whisper results to the stable ASR timeline."""
    raw = asr_result.get("rawResponse")
    payload = raw if isinstance(raw, dict) else asr_result
    backend = str(asr_result.get("backend") or "").strip().lower()
    source_segments: Any
    seconds = False
    if isinstance(payload.get("utterances"), list):
        source_segments = payload["utterances"]
    else:
        source_segments = payload.get("segments") or []
        seconds = backend == "whisper"

    segments: list[dict[str, Any]] = []
    duration_ms = 0
    for source in source_segments:
        if not isinstance(source, dict):
            continue
        text = str(source.get("transcript") or source.get("text") or "").strip()
        start_value = (
            source.get("start")
            if seconds
            else source.get("start_time", source.get("start"))
        )
        end_value = (
            source.get("end")
            if seconds
            else source.get("end_time", source.get("end"))
        )
        start_ms = _timestamp_ms(start_value, seconds=seconds)
        end_ms = max(
            start_ms,
            _timestamp_ms(end_value, seconds=seconds),
        )
        words = _normalize_timeline_words(
            source.get("words"),
            seconds=seconds,
        )
        if not text and words:
            text = "".join(word["text"] for word in words)
        if not text:
            continue
        duration_ms = max(duration_ms, end_ms)
        segments.append({
            "id": f"seg_{len(segments) + 1:04d}",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
            "words": words,
        })

    timeline: dict[str, Any] = {"version": 1, "segments": segments}
    if duration_ms > 0:
        timeline["duration_ms"] = duration_ms
    return timeline


def _ensure_asr_timeline(para_path: Path, timeline_path: Path) -> dict[str, Any] | None:
    """Generate or refresh the stable timeline, including for cached ASR files."""
    try:
        asr_result = json.loads(para_path.read_text(encoding="utf-8"))
        if not isinstance(asr_result, dict):
            return None
        timeline = normalize_asr_timeline(asr_result)
        timeline_path.write_text(
            json.dumps(timeline, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return timeline
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _adopt_collect_legacy(aweme_id: str, author_dir: Path, paths: _CollectPaths) -> None:
    # 历史产物原地兼容(不迁移):旧 author_dir 下产物若在、新仓库位缺,建相对软链指过去。
    # 软链后各支线 exists() 即命中、就地复用旧文件,不搬字节、不重跑下载/转写/截帧。
    _adopt_legacy(aweme_id, author_dir, {
        author_dir / f"{aweme_id}.mp4": paths.video,
        author_dir / f"{aweme_id}.paraformer.json": paths.para,
        author_dir / f"{aweme_id}.txt": paths.txt,
        author_dir / f"{aweme_id}.clean.txt": paths.clean,
        author_dir / f"{aweme_id}.cover.jpg": paths.cover,
        author_dir / f"{aweme_id}.comments.json": paths.comments_path,
        author_dir / aweme_id / "audio": paths.audio_dir,
        author_dir / aweme_id / "frames": paths.frames_dir,
        COLLECTED / aweme_id: paths.cut_dir,
    })


def _new_collect_entry(
    aweme_id: str, meta: dict[str, Any], paths: _CollectPaths, *, platform: str = "douyin",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "platform": platform,
        "aweme_id": aweme_id,
        "desc": meta.get("desc", ""),
        "status": {},
    }
    if meta.get("digg") is not None:
        entry["digg"] = meta["digg"]
    # 展示元数据(沈括详情页直接渲染:作者/话题/四项数据)
    if meta.get("author"):
        entry["author"] = meta["author"]
    if meta.get("hashtags"):
        entry["hashtags"] = meta["hashtags"]
    if meta.get("duration"):
        entry["duration"] = meta["duration"]
    stats = {k: meta[k] for k in ("digg", "comment", "share", "collect") if meta.get(k) is not None}
    if stats:
        entry["stats"] = stats
    # 封面图:落盘成 cover.jpg,App 走 /artifacts/files/ 取
    if meta.get("cover_url") and not paths.cover.exists():
        tikhub_client.download_cover(meta["cover_url"], paths.cover)
    if paths.cover.exists():
        entry["cover"] = _rel(paths.cover)
    return entry


def _merge_collect_manifest(
    entry: dict[str, Any], paths: _CollectPaths, *,
    platform: str, aweme_id: str, author_domain: str | None,
) -> None:
    # 写作品级 manifest:沈括只占 products/status 分区,不碰 works.py 的 card 分区。
    # 这是 works.py / 引擎读"已采作品全产物"的统一入口(下次同作品据此短路)。
    manifest_patch: dict[str, Any] = {
        "products": {
            "video": entry.get("video"),
            "asr": {
                "paraformer": _rel(paths.para) if paths.para.exists() else None,
                "timeline": _rel(paths.timeline) if paths.timeline.exists() else None,
                "segment_count": entry.get("segment_count"),
                "txt": _rel(paths.txt) if paths.txt.exists() else None,
                "clean": _rel(paths.clean) if paths.clean.exists() else None,
                "text": entry.get("text"),
            },
            "speakers": {
                "json": _rel(paths.speakers) if paths.speakers.exists() else None,
                "dialogue": _rel(paths.dialogue) if paths.dialogue.exists() else None,
                # count 从文件推导而非 entry:后续跳过 diarize 分支的采集趟不清空已有值
                "count": _speaker_count_from_file(paths.speakers),
            },
            "cover": entry.get("cover"),
            "comments": entry.get("comments"),
            "audio": entry.get("audio"),
            "frames": entry.get("frames"),
            "cutouts": entry.get("cutouts"),
        },
        "status": entry["status"],
        "collected_at": int(time.time()),
    }
    if author_domain and author_domain.strip():
        # 仅当作品尚无 domain 时才从作者继承（已有非空则保留）
        existing_domain = works_repo.load_domain(platform, aweme_id)
        if not existing_domain:
            manifest_patch["domain"] = author_domain.strip()
    works_repo.merge(platform, aweme_id, **manifest_patch)


@dataclass
class _CollectRun:
    aweme_id: str
    platform: str
    source_url: str | None
    entry: dict[str, Any]
    paths: _CollectPaths
    max_frames: int
    engine: str
    top_comments: int
    on_progress: ProgressFn
    check: Any
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def report(self, msg: str) -> None:
        with self._lock:
            self.on_progress(msg)

    def guard(self) -> None:
        cancel.checkpoint(self.check)

    def branch_download(self) -> bool:
        p = self.paths
        if p.video.exists() and p.video.stat().st_size > 0:
            self.report(f"[{self.aweme_id}] mp4 已存在,跳过下载")
            self.entry["status"]["download"] = "cached"
        else:
            # DouK sidecar 优先，失败回退 yt-dlp；抖音最后再回退 TikHub(付费)。
            try:
                capabilities.fetch_and_download(
                    self.aweme_id, p.video, on_progress=self.report, check=self.check,
                    platform=self.platform, source_url=self.source_url,
                )
                self.report(f"[{self.aweme_id}] 下载完成")
                self.entry["status"]["download"] = "ok"
            except cancel.TaskCancelled:
                raise
            except Exception as e:  # noqa: BLE001 — 单条下载失败不拖垮整批,降级跳过下游
                self.report(f"[{self.aweme_id}] 下载失败: {type(e).__name__}: {e}")
                self.entry["status"]["download"] = "failed"
                return False
        self.entry["video"] = _rel(p.video)
        return True

    def branch_transcribe(self) -> None:
        p = self.paths
        self.guard()
        cache_usable = _transcript_cache_usable(p, self.platform)
        use_transcript_files = False
        if cache_usable:
            self.report(f"[{self.aweme_id}] 转写已存在,跳过")
            self.entry["status"]["transcribe"] = "cached"
            use_transcript_files = True
        else:
            try:
                engine = _asr_engine_for_platform(self.platform)
                if p.para.exists() or p.txt.exists():
                    self.report(f"[{self.aweme_id}] 转写缓存后端不匹配,重新听写")
                if engine:
                    result, text = capabilities.transcribe(p.video, self.report, engine=engine)
                else:
                    result, text = capabilities.transcribe(p.video, self.report)
                text = (text or "").strip()
                if result is not None and text:
                    p.para.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    p.txt.write_text(text, encoding="utf-8")
                    if p.clean.exists():
                        p.clean.unlink()
                    self.entry["status"]["transcribe"] = "ok"
                    use_transcript_files = True
                elif _transcript_result_empty(result):
                    p.para.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    p.txt.write_text("", encoding="utf-8")
                    if p.clean.exists():
                        p.clean.unlink()
                    self.entry["status"]["transcribe"] = "empty"
                    use_transcript_files = True
                else:
                    self.entry["status"]["transcribe"] = "failed"
            except cancel.TaskCancelled:
                raise
            except Exception as e:  # noqa: BLE001 — 单条转写失败不拖垮整批
                self.report(f"[{self.aweme_id}] 转写异常: {type(e).__name__}: {e}")
                self.entry["status"]["transcribe"] = f"error:{type(e).__name__}"
        if use_transcript_files and p.para.exists():
            self.entry["paraformer"] = _rel(p.para)
            timeline = _ensure_asr_timeline(p.para, p.timeline)
            if timeline is not None:
                self.entry["timeline"] = _rel(p.timeline)
                self.entry["segment_count"] = len(timeline["segments"])
        if use_transcript_files and p.txt.exists():
            self.entry["txt"] = _rel(p.txt)
            raw_text = p.txt.read_text(encoding="utf-8").strip()
            self.guard()
            # 清洗(qwen 优先,本地兜底);原文留 asr.txt,清洗版存 asr.clean.txt
            if not p.clean.exists() and raw_text:
                cleaned = capabilities.clean_transcript(raw_text, self.report)
                if cleaned:
                    p.clean.write_text(cleaned, encoding="utf-8")
            # text 同时供前端「提取文案」展示与下游 rw 改写源（_rw_source_text 优先读它）。
            # 用户要的是解说稿原文；清洗稿只作为旁路产物保留，绝不覆盖原始听写语言。
            self.entry["text"] = raw_text[:_TEXT_CAP]
            self.entry["text_raw"] = _rel(p.txt)
            if p.clean.exists():
                self.entry["text_clean"] = _rel(p.clean)

    def branch_audio(self) -> None:
        p = self.paths
        self.guard()
        try:
            audio = capabilities.separate_audio(p.video, p.audio_dir, self.report, check=self.check)
            if audio:
                self.entry["audio"] = {k: _rel(v) for k, v in audio.items()}
                self.entry["status"]["audio"] = "ok"
        except cancel.TaskCancelled:
            raise
        except Exception as e:  # noqa: BLE001 — 声音素材失败不拖垮整批
            self.report(f"声音素材异常: {type(e).__name__}: {e}")
            self.entry["status"]["audio"] = f"error:{type(e).__name__}"

    def branch_diarize(self) -> None:
        """说话人分离(多人对白 domain 专用):优先吃 Demucs 人声轨,BGM 干扰最小。

        排在 branch_audio 之后串行跑;失败/不可用只记状态,不拖垮采集主链路。
        """
        p = self.paths
        self.guard()
        cached_doc = _speakers_cache_usable(p)
        if cached_doc is not None:
            self.report(f"[{self.aweme_id}] 说话人分离已存在,跳过")
            self.entry["status"]["diarize"] = "cached"
            # 缓存自愈:上次死在 dialogue 落盘前时,从 speakers.json 重建对话体
            if not p.dialogue.exists():
                try:
                    dialogue = diarize_cap.format_dialogue(diarize_cap.sentences_from_dict(cached_doc))
                    if dialogue:
                        _write_text_atomic(p.dialogue, dialogue)
                except (OSError, ValueError) as e:
                    self.report(f"[{self.aweme_id}] 对话体重建失败: {type(e).__name__}: {e}")
        else:
            source = p.audio_dir / "vocals.mp3"
            if not source.exists():
                source = p.audio_dir / "original.mp3"
            if not source.exists():
                source = p.video
            if not source.exists():
                self.entry["status"]["diarize"] = "skipped:no-audio"
                return
            try:
                result = diarize_cap.diarize(source, self.report)
                doc = diarize_cap.result_to_dict(result)
                doc["source"] = source.name  # 溯源:混音源结果在人声轨就绪后可判定重算
                dialogue = diarize_cap.format_dialogue(result.sentences)
                if dialogue:
                    _write_text_atomic(p.dialogue, dialogue)
                # speakers.json 最后写,作为本分支的"提交标记":它在即产物全在
                _write_text_atomic(
                    p.speakers, json.dumps(doc, ensure_ascii=False, indent=2))
            except cancel.TaskCancelled:
                raise
            except diarize_cap.DiarizeUnavailableError as e:
                self.report(f"[{self.aweme_id}] 说话人分离不可用: {e}")
                self.entry["status"]["diarize"] = "unavailable"
                return
            except Exception as e:  # noqa: BLE001 — 分离/落盘失败不拖垮整批
                self.report(f"[{self.aweme_id}] 说话人分离异常: {type(e).__name__}: {e}")
                self.entry["status"]["diarize"] = f"error:{type(e).__name__}"
                return
            if dialogue:
                self.entry["status"]["diarize"] = "ok"
                self.report(
                    f"[{self.aweme_id}] 说话人分离完成: {result.speaker_count} 人 / {len(result.sentences)} 句"
                )
            else:
                self.entry["status"]["diarize"] = "empty"
                self.report(f"[{self.aweme_id}] 说话人分离空稿")
        if p.speakers.exists():
            self.entry["speakers"] = _rel(p.speakers)
            count = _speaker_count_from_file(p.speakers)
            if count is not None:
                self.entry["speaker_count"] = count
        if p.dialogue.exists():
            self.entry["dialogue"] = _rel(p.dialogue)

    def branch_frames(self) -> None:
        p = self.paths
        self.guard()
        frames = sorted(p.frames_dir.glob("frame_*.jpg")) if p.frames_dir.exists() else []
        if frames:
            self.report(f"[{self.aweme_id}] 截帧已存在 {len(frames)} 张")
        else:
            frames = capabilities.extract_frames(p.video, p.frames_dir, self.max_frames)
            self.report(f"[{self.aweme_id}] 截帧 {len(frames)} 张")
        self.entry["frames"] = [_rel(f) for f in frames]
        cutouts = sorted(p.cut_dir.glob("*.png")) if p.cut_dir.exists() else []
        if cutouts:
            self.report(f"[{self.aweme_id}] 抠图已存在 {len(cutouts)} 张")
        elif frames:
            self.guard()
            try:
                cutouts = capabilities.cutout(frames, p.cut_dir, engine=self.engine)
                self.report(f"[{self.aweme_id}] 抠图 {len(cutouts)} 张(舞台区/{self.engine})")
                self.entry["status"]["cutout"] = "ok"
            except cancel.TaskCancelled:
                raise
            except Exception as e:  # noqa: BLE001
                self.report(f"[{self.aweme_id}] 抠图异常: {type(e).__name__}: {e}")
                self.entry["status"]["cutout"] = f"error:{type(e).__name__}"
        self.entry["cutouts"] = [_rel(c) for c in cutouts]

    def branch_comments(self) -> None:
        p = self.paths
        self.guard()
        if self.platform != "douyin":
            self.report(f"[{self.platform}:{self.aweme_id}] 非抖音作品跳过评论采集")
            self.entry["status"]["comments"] = "skipped"
            return
        if p.comments_path.exists():
            self.report(f"[{self.aweme_id}] 评论已存在,跳过")
            self.entry["status"]["comments"] = "cached"
        else:
            try:
                rows = tikhub_client.fetch_top_comments(
                    self.aweme_id, top_n=self.top_comments, on_progress=self.report,
                )
                p.comments_path.write_text(json.dumps(
                    {
                        "aweme_id": self.aweme_id,
                        "generated_at": int(time.time()),
                        "top_n": self.top_comments,
                        "items": rows,
                    },
                    ensure_ascii=False, indent=2,
                ), encoding="utf-8")
                self.report(f"[{self.aweme_id}] top {len(rows)} 评论已落盘")
                self.entry["status"]["comments"] = "ok"
            except cancel.TaskCancelled:
                raise
            except Exception as e:  # noqa: BLE001 — 单条评论失败不拖垮整批
                self.report(f"[{self.aweme_id}] 评论采集异常: {type(e).__name__}: {e}")
                self.entry["status"]["comments"] = f"error:{type(e).__name__}"
        if p.comments_path.exists():
            self.entry["comments"] = _rel(p.comments_path)
            # 高赞评论嵌进 entry(>10 赞阈值,按赞数排好),App 直接渲染
            try:
                items = json.loads(p.comments_path.read_text(encoding="utf-8")).get("items", [])
                top = top_comments_from_items(items, self.top_comments)
                if top:
                    self.entry["top_comments"] = top
            except Exception:  # noqa: BLE001 — 评论嵌入失败不影响 entry 主体
                pass


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
    platform: str = "douyin", on_progress: ProgressFn = _noop,
    do_audio: bool = True, do_frames: bool = True,
    author_domain: str | None = None, source_url: str | None = None,
) -> dict[str, Any]:
    meta = meta or {}
    paths = _collect_paths(platform, aweme_id)

    _adopt_collect_legacy(aweme_id, author_dir, paths)
    entry = _new_collect_entry(aweme_id, meta, paths, platform=platform)
    # 协作式取消:在主线程取 checker(thread-local 不传子线程),闭包给各支线。
    # 支线在步骤边界 guard();Demucs 子进程内部按秒轮询可被 SIGTERM。
    run_ctx = _CollectRun(
        aweme_id=aweme_id,
        platform=platform,
        source_url=source_url,
        entry=entry,
        paths=paths,
        max_frames=max_frames,
        engine=engine,
        top_comments=top_comments,
        on_progress=on_progress,
        check=cancel.current(),
    )

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_comments = ex.submit(run_ctx.branch_comments) if top_comments > 0 else None
        if run_ctx.branch_download():
            # web 画布分两趟:首趟快采(do_audio/do_frames=False)出文案/评论让下游 rw 先走,
            # 音轨分离(Demucs)/抠图重活由后台第二趟补 —— collect_one 幂等,已采支线自动跳过。
            futures = [ex.submit(run_ctx.branch_transcribe)]
            if do_audio:
                # 多人对白 domain(comedy)在人声轨就绪后串行做说话人分离;
                # domain 取参数,首趟没传时回退 manifest 已有值(第二趟补采场景)。
                effective_domain = (author_domain or "").strip() or works_repo.load_domain(platform, aweme_id)
                if _needs_diarization(effective_domain):
                    def _audio_then_diarize() -> None:
                        run_ctx.branch_audio()
                        run_ctx.branch_diarize()
                    futures.append(ex.submit(_audio_then_diarize))
                else:
                    futures.append(ex.submit(run_ctx.branch_audio))
            if do_frames:
                futures.append(ex.submit(run_ctx.branch_frames))
            for f in futures:
                f.result()
        if f_comments is not None:
            f_comments.result()

    # domain 继承逻辑：已有非空 domain 保留（补全，不抢占）；作者传了才写，空值不注入。
    _merge_collect_manifest(
        entry, paths, platform=platform, aweme_id=aweme_id, author_domain=author_domain,
    )
    return entry


def refresh_stats_comments(
    aweme_id: str, platform: str = "douyin", top_comments: int = 20,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """轻量刷新单条作品的「播放数据(stats)」+「评论(top_comments)」,强制重取,
    不碰下载/转写/音轨/抠图。供进画布时的后台刷新用(节流由调用方的 Redis 锁把关)。

    返回只含变化字段的 patch:{stats?, digg?, comments?, top_comments?};某项失败则
    该项不进 patch(不抛错),保证单项故障不影响其它项。
    """
    if platform != "douyin":
        on_progress(f"[{platform}:{aweme_id}] 非抖音作品跳过数据/评论刷新")
        return {}

    patch: dict[str, Any] = {}

    # 1) 播放数据:重取作品详情 -> meta -> 四项数据。
    try:
        meta = tikhub_client.extract_meta(tikhub_client.fetch_one_video_detail(aweme_id))
        stats = {k: meta[k] for k in ("digg", "comment", "share", "collect") if meta.get(k) is not None}
        if stats:
            patch["stats"] = stats
            if "digg" in stats:
                patch["digg"] = stats["digg"]
    except Exception as exc:  # noqa: BLE001 — 数据刷新失败不影响评论刷新
        on_progress(f"[{aweme_id}] 播放数据刷新失败(不影响): {type(exc).__name__}: {exc}")

    # 2) 评论:强制重取(覆写 comments.json),重算高赞 top_comments。
    if top_comments > 0:
        try:
            rows = tikhub_client.fetch_top_comments(aweme_id, top_n=top_comments, on_progress=on_progress)
            wdir = works_repo.work_dir(platform, aweme_id)
            comments_path = wdir / "comments.json"
            comments_path.write_text(json.dumps(
                {"aweme_id": aweme_id, "generated_at": int(time.time()), "top_n": top_comments, "items": rows},
                ensure_ascii=False, indent=2), encoding="utf-8")
            patch["comments"] = _rel(comments_path)
            patch["top_comments"] = top_comments_from_items(rows, top_comments)
            on_progress(f"[{aweme_id}] 评论已刷新(top {len(patch['top_comments'])})")
        except Exception as exc:  # noqa: BLE001 — 评论刷新失败不影响数据刷新
            on_progress(f"[{aweme_id}] 评论刷新失败(不影响): {type(exc).__name__}: {exc}")

    return patch


def _write_collected(author_dir: Path, collected: list[dict]) -> Path:
    path = author_dir / "collected.json"
    path.write_text(json.dumps({"generated_at": int(time.time()), "items": collected},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _fetch_posts_multipass(
    author: str, pull_n: int, on_progress: ProgressFn, passes: int = 3,
    platform: str = "douyin",
) -> list[dict]:
    """多趟拉取取并集,逼近作者真实可达作品集。

    抖音 user_posts 翻页非确定:has_more / cursor 会在还有作品时就谎报"到底",单趟可能
    严重欠收(实测同账号 42 vs 164)。重复拉几趟、按 aweme_id 取并集补齐;连续一趟零新增
    即提前停。单趟抛异常(如 ReadTimeout)只丢这一趟、用已得的,不让整次采集失败。
    """
    acc: dict[str, dict] = {}
    for i in range(passes):
        try:
            batch = tikhub_client.fetch_author_posts(
                platform, author, max_items=pull_n, on_progress=on_progress,
            )
        except Exception as e:  # noqa: BLE001 — 单趟失败不拖垮整次(已得的照样用)
            on_progress(f"第 {i + 1} 趟异常,跳过用已得: {type(e).__name__}: {e}")
            continue
        before = len(acc)
        for p in batch:
            aid = p.get("aweme_id")
            if aid:
                acc[aid] = p  # 后一趟的 stats 覆盖前一趟(取最新)
        gained = len(acc) - before
        on_progress(f"第 {i + 1} 趟: 本趟 {len(batch)} 条,累计去重 {len(acc)}(+{gained})")
        if i > 0 and gained == 0:
            break
    return list(acc.values())


def _merge_all_posts(path: Path, fresh: list[dict]) -> list[dict]:
    """all_posts.json 并集合并写:旧的保留、新的覆盖 stats,**只增不减**。

    防"短趟覆盖丢数据"(曾把好好的 123 条覆盖成 42)。配合周期 refresh,覆盖率单调收敛。
    按 create 倒序(新作品在前)。
    """
    merged: dict[str, dict] = {}
    if path.exists():
        try:
            for p in json.loads(path.read_text(encoding="utf-8")):
                aid = p.get("aweme_id")
                if aid:
                    merged[aid] = p
        except (json.JSONDecodeError, OSError):
            pass  # 旧文件坏了就当空,fresh 兜底
    for p in fresh:
        aid = p.get("aweme_id")
        if aid:
            merged[aid] = p
    return sorted(merged.values(), key=lambda p: p.get("create", 0), reverse=True)


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #
def run(
    author: str | None = None,
    aweme: str | None = None,
    source_url: str | None = None,
    top: int = 10,
    max_frames: int = 8,
    engine: str = "threshold",
    max_posts: int | None = None,
    top_comments: int = 20,
    refresh_only: bool = False,
    platform: str = "douyin",
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """采集一个对标号(--author sec_uid)的 top 高赞作品,或单条(--aweme)。

    refresh_only=True 时只跑"拉列表 -> 写指标层(SQLite 时间序列)",跳过深采,给高频 cron 用。
    返回 {author_dir, all_posts?, collected:[entry...]}(同时落 all_posts.json + collected.json)。
    """
    platform = _platform(platform)
    if not author and not aweme:
        raise ValueError("需要 --author <sec_uid> 或 --aweme <aweme_id>")

    # 单条模式:验证链路用。aweme 接受 纯数字id/分享短链/整段分享口令
    if aweme:
        author_dir = BENCH / "adhoc"
        author_dir.mkdir(parents=True, exist_ok=True)
        on_progress(f"沈括: 单条采集 {aweme}")
        ref = _video_ref_from_input(aweme, platform, source_url=source_url)
        if not ref:
            raise ValueError(f"解析不出作品 id: {aweme}(支持抖音/TK/油管 单作品链接)")
        aweme_id = ref.video_id
        platform = ref.platform
        if aweme_id != aweme.strip():
            on_progress(f"链接解析: -> {platform}:{aweme_id}")
        # 取展示元数据(标题/作者/话题/数据/封面);失败不阻塞主链路
        meta: dict[str, Any] = {}
        if platform == "douyin":
            try:
                meta = tikhub_client.extract_meta(tikhub_client.fetch_one_video_detail(aweme_id))
                if meta.get("desc"):
                    on_progress(f"《{meta['desc'][:36]}》 @{meta.get('author', '')} 赞 {meta.get('digg', 0)}")
            except Exception as e:  # noqa: BLE001
                on_progress(f"元数据获取失败(不阻塞): {type(e).__name__}: {e}")
        else:
            try:
                meta = tikhub_client.fetch_video_ref_meta(ref)
            except Exception as e:  # noqa: BLE001
                on_progress(f"元数据获取失败(不阻塞): {type(e).__name__}: {e}")
                meta = tikhub_client.video_ref_meta(ref)
        entry = collect_one(aweme_id, author_dir, meta=meta, max_frames=max_frames, engine=engine,
                            top_comments=top_comments, platform=platform, on_progress=on_progress,
                            source_url=ref.url)
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
    if author is None:
        raise ValueError("需要 --author <sec_uid>")
    author_dir = author_benchmark_dir(platform, author)
    author_dir.mkdir(parents=True, exist_ok=True)
    on_progress(f"沈括启动: 拉作者作品({platform}:{author[:32]}...)")
    # refresh-only 要广覆盖(更新历史作品指标),默认拉更多;深采模式只需够挑 top
    pull_n = max_posts or (200 if refresh_only else max(top * 2, 30))
    # 多趟并集拉本次"新鲜"作品(抖音翻页非确定,单趟会欠收),再 merge 进 all_posts.json(只增不减)
    fresh = _fetch_posts_multipass(author, pull_n, on_progress, platform=platform)
    posts = _merge_all_posts(author_dir / "all_posts.json", fresh)
    (author_dir / "all_posts.json").write_text(
        json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
    on_progress(f"本次新鲜 {len(fresh)} 条,合并后累计 {len(posts)} 条,落 all_posts.json")

    # 作者库:顺带刷新作者名片(昵称/头像/粉丝数),让 worker 刷新真正更新 /accounts/resolve 缓存。
    try:
        from ncds_opus_factory.common import authors_repo
        prof = None
        if platform == "douyin":
            prof = tikhub_client.fetch_douyin_profile(author)
        elif platform == "tiktok":
            prof = tikhub_client.fetch_tiktok_profile(author)
        if prof:
            key = authors_repo.author_key(platform, prof.get("sec_uid", ""), prof.get("unique_id", "")) or author
            authors_repo.save_profile(platform, key, prof)
            on_progress("作者库: 已刷新作者名片")
    except Exception as e:  # noqa: BLE001
        on_progress(f"作者库名片刷新失败(不阻塞): {type(e).__name__}: {e}")

    # 指标层:写身份 + 追加变化的快照(时间序列)。只喂 fresh(真实当前 stats),
    # 不喂 merged——merged 含历史旧条目的旧 stats,会往时间序列里塞陈旧/重复快照。
    ts = int(time.time())
    sig: dict[str, int] = {}
    conn = benchmark_store.connect(BENCH_DB)
    try:
        metric_author = _metric_author_key(platform, author)
        stat = benchmark_store.record_refresh(conn, metric_author, fresh, ts)
        on_progress(f"指标层: 作品 {stat['posts']} 条,新增快照 {stat['snapshots']} 条 -> {_rel(BENCH_DB)}")
        # 信号检测(订阅传感器的产出):新作品/指标飙升 -> events.jsonl 供排产消费。
        # 失败不阻塞采集主链路。
        try:
            from ncds_opus_factory.common import signals
            sig = signals.emit_signals(
                conn, metric_author, ts, events_dir=BENCH_DB.parent,
                on_progress=on_progress, platform=platform,
            )
        except Exception as e:  # noqa: BLE001
            on_progress(f"信号检测失败(不阻塞): {type(e).__name__}: {e}")
    finally:
        conn.close()

    if refresh_only:
        on_progress("refresh-only: 跳过深采")
        return {"author_dir": str(author_dir), "all_posts": len(posts), "collected": [],
                "snapshots": stat["snapshots"], "signals": sig}

    # 从订阅配置查此作者的 domain，用于"作者→作品 domain 继承"。
    # 订阅文件在 state/shenkuo/subscriptions.json，与任务目录为兄弟目录关系。
    # 失败不阻塞深采主链路（domain 是补全信息，缺失时行为回退到现有逻辑）。
    author_domain: str | None = None
    try:
        from ncds_opus_factory.server.subscriptions import load_subscriptions, subscriptions_path
        # state 根解析必须与 works_repo 一致：NOF_STATE_DIR 设了用其(任务目录)，没设回退
        # 仓库根 state/。只认 env 会让默认部署(本机不设 env)下 domain 继承静默失效。
        _sub_path = subscriptions_path(works_repo._state_root() / "tasks")
        _subs = load_subscriptions(_sub_path)
        for _a in _subs.get("authors") or []:
            sub_platform = str(_a.get("platform") or "douyin").strip().lower() or "douyin"
            _key = _a.get("unique_id") if sub_platform in {"tiktok", "youtube"} else _a.get("sec_uid")
            if sub_platform == platform and _key == author and isinstance(_a.get("domain"), str):
                _d = _a["domain"].strip()
                if _d:
                    author_domain = _d
                    break
    except Exception as e:  # noqa: BLE001 — domain 查询失败不影响采集
        on_progress(f"domain 查询失败(不阻塞): {type(e).__name__}: {e}")

    if author_domain:
        on_progress(f"作者 domain: {author_domain}")

    posts.sort(key=lambda p: p.get("digg", 0), reverse=True)
    chosen = posts[:top]
    collected: list[dict] = []
    for i, p in enumerate(chosen, 1):
        on_progress(f"=== 采集 {i}/{len(chosen)}: {p['aweme_id']} ({p.get('digg')}赞) ===")
        try:
            entry = collect_one(p["aweme_id"], author_dir, meta=p, max_frames=max_frames, engine=engine,
                                top_comments=top_comments, platform=platform, on_progress=on_progress,
                                author_domain=author_domain,
                                source_url=p.get("share_url") or p.get("source_url"))
        except Exception as e:  # noqa: BLE001 — 单条失败不拖垮整批
            on_progress(f"  采集异常: {type(e).__name__}: {e}")
            entry = {"aweme_id": p["aweme_id"], "status": {"error": str(e)}}
        collected.append(entry)
    _write_collected(author_dir, collected)
    on_progress(f"沈括完成: {len(collected)} 条采集,产物在 {works_repo.works_root()},索引 {author_dir}")
    return {"author_dir": str(author_dir), "all_posts": len(posts), "collected": collected}


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof shenkuo", description="沈括: 对标号情报/素材采集 agent")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--author", help="对标号 sec_user_id(拉其作品)")
    src.add_argument("--aweme", help="单条作品 aweme_id(验证链路用)")
    parser.add_argument("--platform", default="douyin", choices=sorted(SUPPORTED_PLATFORMS),
                        help="采集平台: douyin/tiktok/youtube")
    parser.add_argument("--source-url", default=None, help="单条模式原始作品 URL(可选)")
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
        source_url=args.source_url, platform=args.platform,
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
