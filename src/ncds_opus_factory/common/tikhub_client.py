"""TikHub API 项目内封装：token / 按作者拉作品 / 取播放地址 / 下载。

douyin-downloader skill(`skills/douyin-downloader/scripts/douyin_download.py`)只覆盖单视频;
沈括(采集 agent)需要「按作者 sec_user_id 分页拉作品列表」—— 仓库没有,这里补上,
并把作品列表整理成 `all_posts.json`(鬼谷子 guiguzi 吃的精简格式:aweme_id/desc/digg/comment/share/collect/create)。

token 只在项目内解析:优先环境变量 TIKHUB_API_TOKEN,其次仓库根 .env(已 gitignore,
模板见 .env.example)——不读 ~/.openclaw 等仓库外配置。本模块不碰飞书、不依赖项目其它部分。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests

CHUNK_SIZE = 1024 * 1024
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 60
DOWNLOAD_RETRIES = 3

API_BASE = "https://api.tikhub.io/api/v1/douyin/web"
ONE_VIDEO_URL = f"{API_BASE}/fetch_one_video"
USER_POSTS_URL = f"{API_BASE}/fetch_user_post_videos"
DOUYIN_PROFILE_URL = f"{API_BASE}/handler_user_profile"
TIKTOK_PROFILE_URL = "https://api.tikhub.io/api/v1/tiktok/web/fetch_user_profile"
# 评论接口走 app/v3(与上面的 web base 不同);实测每页硬上限 20 条,count 调大无效
COMMENTS_URL = "https://api.tikhub.io/api/v1/douyin/app/v3/fetch_video_comments"
COMMENTS_PAGE_SIZE = 20

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

ProgressFn = Callable[[str], None]

ROOT = Path(__file__).resolve().parents[3]   # 仓库根(src/ncds_opus_factory/common/ 上三级)


def get_token(token: str | None = None) -> str:
    """读 TikHub token。优先入参,其次 env TIKHUB_API_TOKEN,最后仓库根 .env。"""
    if token:
        return token
    if os.getenv("TIKHUB_API_TOKEN"):
        return os.environ["TIKHUB_API_TOKEN"]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("TIKHUB_API_TOKEN="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    raise ValueError("缺少 TIKHUB_API_TOKEN(设环境变量,或写进仓库根 .env,模板见 .env.example)")


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# 按作者拉作品(新实现)
# --------------------------------------------------------------------------- #
def fetch_user_posts_page(
    sec_user_id: str, max_cursor: int = 0, count: int = 20, token: str | None = None
) -> tuple[list[dict], int, bool]:
    """拉一页作者作品。返回 (aweme_list 原始, next_cursor, has_more)。"""
    token = get_token(token)
    url = f"{USER_POSTS_URL}?sec_user_id={sec_user_id}&max_cursor={max_cursor}&count={count}"
    resp = requests.get(url, headers=_headers(token), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    resp.raise_for_status()
    payload = resp.json()
    # TikHub 通常包一层 data;容错直接返回顶层
    d = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    d = d or {}
    aweme_list = d.get("aweme_list") or d.get("aweme_lists") or []
    next_cursor = int(d.get("max_cursor") or d.get("cursor") or 0)
    has_more = bool(d.get("has_more"))
    return aweme_list, next_cursor, has_more


def simplify_aweme(aweme: dict) -> dict[str, Any]:
    """TikHub aweme -> all_posts.json 精简条目(鬼谷子格式)。"""
    st = aweme.get("statistics") or {}
    video = aweme.get("video") or {}
    cover_url = ""
    for key in ("cover", "origin_cover", "cover_original_scale"):
        urls = (video.get(key) or {}).get("url_list") or []
        if urls:
            cover_url = urls[0]
            break
    return {
        "aweme_id": str(aweme.get("aweme_id") or ""),
        "desc": aweme.get("desc") or "",
        "digg": st.get("digg_count", 0),
        "comment": st.get("comment_count", 0),
        "share": st.get("share_count", 0),
        "collect": st.get("collect_count", 0),
        "create": aweme.get("create_time", 0),
        "cover_url": cover_url,
    }


def fetch_user_posts(
    sec_user_id: str, max_items: int = 60, token: str | None = None, on_progress: ProgressFn | None = None
) -> list[dict[str, Any]]:
    """分页拉作者作品,返回精简条目列表(鬼谷子 all_posts.json 格式)。"""
    token = get_token(token)
    out: list[dict[str, Any]] = []
    cursor, page = 0, 0
    seen: set[str] = set()
    while len(out) < max_items:
        aweme_list, cursor, has_more = fetch_user_posts_page(sec_user_id, cursor, 20, token)
        if not aweme_list:
            break
        for a in aweme_list:
            item = simplify_aweme(a)
            if item["aweme_id"] and item["aweme_id"] not in seen:
                seen.add(item["aweme_id"])
                out.append(item)
        page += 1
        if on_progress:
            on_progress(f"拉第 {page} 页,累计 {len(out)} 条作品")
        if not has_more:
            break
        time.sleep(0.3)
    return out[:max_items]


# --------------------------------------------------------------------------- #
# 单视频:取播放地址 + 下载(逻辑同 douyin_download,搬到项目内)
# --------------------------------------------------------------------------- #
def resolve_aweme_id(aweme: str) -> str | None:
    """把「纯数字 id / 分享短链 / 整段分享口令」统一解析成 aweme_id。

    App 剪贴板捕获传来的是 v.douyin.com 短链(甚至带文案的口令),跟随重定向
    到 www.douyin.com/video/<id> 再抠数字;解析不动返回 None,调用方报清楚。
    """
    s = (aweme or "").strip()
    if s.isdigit():
        return s
    m = re.search(r"https?://\S+", s)
    if not m:
        return None
    url = m.group(0).rstrip("。，,)）]】\"'")
    try:
        resp = requests.get(url, headers={"User-Agent": _UA},
                            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=True)
        final = resp.url
    except requests.RequestException:
        final = url
    m = (re.search(r"(?:video|note|slides)/(\d{8,25})", final)
         or re.search(r"aweme_id=(\d{8,25})", final)
         or re.search(r"(\d{15,25})", final))
    return m.group(1) if m else None


_SECUID_RE = r"(MS4w[A-Za-z0-9_-]+)"


def resolve_sec_uid(text: str) -> str | None:
    """把「抖音主页分享链接 / 口令 / 完整 user URL / 裸 sec_uid」统一解析成 sec_user_id。

    支持三种来源:
      - 完整主页 URL：``www.douyin.com/user/MS4w...?...`` → 直接抠 ``/user/<sec_uid>``
      - 分享短链/口令：``长按复制...查看TA的更多作品 https://v.douyin.com/XXX/`` → 跟随重定向再抠
      - 裸 sec_uid（MS4w 开头）
    解析不出返回 None,调用方报清楚。
    """
    s = (text or "").strip()
    # 裸 sec_uid
    if re.fullmatch(_SECUID_RE, s):
        return s
    # 文本里直接含 /user/<sec_uid>
    m = re.search(r"/user/" + _SECUID_RE, s)
    if m:
        return m.group(1)
    # 抽 URL,跟随短链重定向到完整主页 URL 再抠
    m = re.search(r"https?://\S+", s)
    if not m:
        return None
    url = m.group(0).rstrip("。，,)）]】\"'")
    try:
        resp = requests.get(url, headers={"User-Agent": _UA},
                            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=True)
        final = resp.url
    except requests.RequestException:
        final = url
    m = (re.search(r"/user/" + _SECUID_RE, final)
         or re.search(r"sec_uid=" + _SECUID_RE, final)
         or re.search(r"sec_user_id=" + _SECUID_RE, final))
    return m.group(1) if m else None


def fetch_user_nickname(sec_uid: str, token: str | None = None) -> str:
    """best-effort 取作者昵称（拉 1 条作品从原始 author 里读）；失败返回空串。"""
    try:
        aweme_list, _, _ = fetch_user_posts_page(sec_uid, count=1, token=token)
        if aweme_list:
            return (aweme_list[0].get("author") or {}).get("nickname") or ""
    except Exception:  # noqa: BLE001 — 昵称只是展示用,失败不阻塞
        pass
    return ""


# --------------------------------------------------------------------------- #
# 用户主页档案（跨平台：抖音 / TikTok）—— 新增对标号时展示昵称/头像/粉丝/获赞/作品数
# --------------------------------------------------------------------------- #
def _pick_avatar(user: dict, keys: list[str]) -> str:
    """从 user 的若干头像字段里取一个 URL。兼容抖音(dict{url_list})与 TikTok(str)。"""
    for k in keys:
        v = user.get(k)
        if isinstance(v, dict):
            ul = v.get("url_list") or []
            if ul:
                return ul[0]
        elif isinstance(v, str) and v.startswith("http"):
            return v
    return ""


def _empty_profile(platform: str, sec_uid: str = "", unique_id: str = "") -> dict[str, Any]:
    return {
        "platform": platform, "sec_uid": sec_uid, "nickname": "", "unique_id": unique_id,
        "avatar": "", "follower_count": 0, "like_count": 0, "works_count": 0,
    }


def fetch_douyin_profile(sec_uid: str, token: str | None = None) -> dict[str, Any] | None:
    """抖音用户主页档案 -> 归一化 {platform,sec_uid,nickname,unique_id,avatar,follower/like/works}。"""
    token = get_token(token)
    resp = requests.get(f"{DOUYIN_PROFILE_URL}?sec_user_id={sec_uid}",
                        headers=_headers(token), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    resp.raise_for_status()
    user = ((resp.json().get("data") or {}).get("user")) or {}
    if not user:
        return None
    return {
        "platform": "douyin",
        "sec_uid": user.get("sec_uid") or sec_uid,
        "nickname": user.get("nickname") or "",
        "unique_id": str(user.get("unique_id") or user.get("short_id") or ""),
        "avatar": _pick_avatar(user, ["avatar_168x168", "avatar_300x300", "avatar_thumb", "avatar_medium"]),
        "follower_count": int(user.get("follower_count") or 0),
        "like_count": int(user.get("total_favorited") or 0),
        "works_count": int(user.get("aweme_count") or 0),
    }


def fetch_tiktok_profile(unique_id: str, token: str | None = None) -> dict[str, Any] | None:
    """TikTok 用户主页档案（按 uniqueId/handle）-> 归一化结构（同抖音字段名）。"""
    token = get_token(token)
    resp = requests.get(f"{TIKTOK_PROFILE_URL}?uniqueId={unique_id}",
                        headers=_headers(token), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    resp.raise_for_status()
    info = (resp.json().get("data") or {}).get("userInfo") or {}
    user = info.get("user") or {}
    stats = info.get("stats") or info.get("statsV2") or {}
    if not user:
        return None
    return {
        "platform": "tiktok",
        "sec_uid": user.get("secUid") or "",
        "nickname": user.get("nickname") or user.get("uniqueId") or "",
        "unique_id": user.get("uniqueId") or unique_id,
        "avatar": _pick_avatar(user, ["avatarThumb", "avatarMedium", "avatarLarger"]),
        "follower_count": int(stats.get("followerCount") or 0),
        "like_count": int(stats.get("heartCount") or stats.get("heart") or 0),
        "works_count": int(stats.get("videoCount") or 0),
    }


def resolve_tiktok_handle(text: str) -> str | None:
    """从 TikTok 主页链接/短链解析出 @handle（uniqueId）。非 TikTok 返回 None。"""
    s = (text or "").strip()
    m = re.search(r"tiktok\.com/@([A-Za-z0-9_.\-]+)", s)
    if m:
        return m.group(1)
    # vt./vm.tiktok.com 短链 -> 跟随重定向到 /@handle
    m = re.search(r"https?://(?:vt|vm)\.tiktok\.com/\S+", s)
    if m:
        url = m.group(0).rstrip("。，,)）]】\"'")
        try:
            resp = requests.get(url, headers={"User-Agent": _UA},
                                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=True)
            m2 = re.search(r"tiktok\.com/@([A-Za-z0-9_.\-]+)", resp.url)
            if m2:
                return m2.group(1)
        except requests.RequestException:
            pass
    return None


def fetch_account_profile(text: str, token: str | None = None) -> dict[str, Any] | None:
    """统一入口：从分享链接/口令/handle 判平台并拉主页档案（归一化）。解析不出返回 None。

    先判 TikTok（tiktok.com/@handle 或 vt/vm 短链），否则走抖音（resolve_sec_uid + handler_user_profile）。
    注意 TikTok 的 secUid 也以 MS4w 开头但与抖音命名空间不同，必须靠 URL 域名区分，故先判 TikTok。
    """
    handle = resolve_tiktok_handle(text)
    if handle:
        return fetch_tiktok_profile(handle, token) or _empty_profile("tiktok", unique_id=handle)
    sec_uid = resolve_sec_uid(text)
    if sec_uid:
        return fetch_douyin_profile(sec_uid, token) or _empty_profile("douyin", sec_uid=sec_uid)
    return None


def fetch_video_url(aweme_id: str, token: str | None = None) -> str | None:
    """aweme_id -> 无水印播放地址。"""
    token = get_token(token)
    url = f"{ONE_VIDEO_URL}?aweme_id={aweme_id}&need_anchor_info=false"
    resp = requests.get(url, headers=_headers(token), timeout=30)
    resp.raise_for_status()
    normalized = resp.text.replace("\\/", "/")
    m = re.search(r'(https://www\.douyin\.com/aweme/v1/play/[^\s"<>\\]+)', normalized)
    return m.group(1) if m else None


def fetch_one_video_detail(aweme_id: str, token: str | None = None) -> dict:
    """fetch_one_video 的完整 aweme_detail(desc/author/statistics/text_extra/video.cover)。"""
    token = get_token(token)
    url = f"{ONE_VIDEO_URL}?aweme_id={aweme_id}&need_anchor_info=false"
    resp = requests.get(url, headers=_headers(token), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    resp.raise_for_status()
    payload = resp.json()
    d = payload.get("data") if isinstance(payload, dict) else None
    return (d or {}).get("aweme_detail") or {}


def extract_meta(detail: dict) -> dict[str, Any]:
    """aweme_detail -> 采集 entry 的展示元数据:标题/作者/话题/四项数据/封面 URL。"""
    st = detail.get("statistics") or {}
    hashtags = [e.get("hashtag_name") for e in (detail.get("text_extra") or [])
                if e.get("hashtag_name")]
    video = detail.get("video") or {}
    cover_url = ""
    # cover_original_scale 才是作者设置的封面;origin_cover 是首帧截图,只作兜底。
    # (实测对比过:7647701061202148009 的 origin_cover=首帧街景,original_scale=真封面)
    for key in ("cover_original_scale", "cover", "origin_cover"):
        urls = ((video.get(key) or {}).get("url_list")) or []
        if urls:
            cover_url = urls[0]
            break
    return {
        "desc": detail.get("desc") or "",
        "author": (detail.get("author") or {}).get("nickname") or "",
        "hashtags": hashtags,
        "digg": st.get("digg_count", 0),
        "comment": st.get("comment_count", 0),
        "share": st.get("share_count", 0),
        "collect": st.get("collect_count", 0),
        "cover_url": cover_url,
    }


def extract_work_card(detail: dict) -> dict[str, Any]:
    """aweme_detail -> 临时任务"智能解析"作品卡。

    复用 extract_meta 的标题/话题/四项互动数据/封面,额外把 author 从昵称字符串
    扩成 {platform,sec_uid,nickname,unique_id,avatar} —— 「关注ta」需要 sec_uid
    才能把作者加入对标订阅(复用 /accounts 的展示快照形状)。
    """
    meta = extract_meta(detail)
    desc = detail.get("desc") or ""
    # 话题：text_extra 的 hashtag_name 为主 + desc 里正则抽取的话题补充并去重(保序)
    # —— 有些作品 text_extra 不全会漏标签(如末尾的 #教育)，用 desc 文本兜底补回。
    hashtags = list(meta["hashtags"])
    seen = set(hashtags)
    for tag in re.findall(r"#([^\s#]+)", desc):
        if tag and tag not in seen:
            seen.add(tag)
            hashtags.append(tag)
    # 标题去掉 #话题(话题已由 hashtags 单独展示，避免标题里重复)，并规整移除后多余的空白(含全角空格)
    title = re.sub(r"#[^\s#]+", "", desc)
    title = re.sub(r"[ \t　]{2,}", " ", title).strip()
    author = detail.get("author") or {}
    return {
        "title": title,
        "hashtags": hashtags,
        "digg": meta["digg"],
        "comment": meta["comment"],
        "share": meta["share"],
        "collect": meta["collect"],
        "cover_url": meta["cover_url"],
        "author": {
            "platform": "douyin",
            "sec_uid": author.get("sec_uid") or "",
            "nickname": author.get("nickname") or "",
            "unique_id": str(author.get("unique_id") or author.get("short_id") or ""),
            "avatar": _pick_avatar(
                author, ["avatar_168x168", "avatar_300x300", "avatar_thumb", "avatar_medium"]
            ),
        },
    }


def download_cover(cover_url: str, output_path: str | Path) -> bool:
    """下载封面图;失败返回 False,不抛(封面缺了不影响采集主链路)。"""
    try:
        resp = requests.get(cover_url, headers={"User-Agent": _UA},
                            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        resp.raise_for_status()
        Path(output_path).write_bytes(resp.content)
        return True
    except Exception:  # noqa: BLE001
        return False


def download_video(url: str, output_path: str | Path, max_retries: int = DOWNLOAD_RETRIES) -> str:
    """streaming 下载 + 有限重试。写 .part 临时文件成功后原子替换。"""
    headers = {"User-Agent": _UA, "Referer": "https://www.douyin.com/"}
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with requests.get(url, headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
            tmp.replace(out)
            return str(out)
        except Exception as error:  # noqa: BLE001 — 网络异常统一重试
            last_error = error
            if tmp.exists():
                tmp.unlink()
            if attempt >= max_retries:
                break
            time.sleep(min(attempt, 3))
    raise last_error  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# 评论:拉 TOP 赞评论(热门序 + 早停,避免在爆款上翻全量)
# --------------------------------------------------------------------------- #
def fetch_comments_page(
    aweme_id: str, cursor: int = 0, count: int = COMMENTS_PAGE_SIZE, token: str | None = None
) -> tuple[list[dict], int, bool, int]:
    """拉一页评论。返回 (comments 原始, next_cursor, has_more, total)。"""
    token = get_token(token)
    params = {"aweme_id": aweme_id, "cursor": cursor, "count": count}
    resp = requests.get(
        COMMENTS_URL, headers=_headers(token), params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
    )
    resp.raise_for_status()
    payload = resp.json()
    d = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    d = d or {}
    comments = d.get("comments") or []
    next_cursor = int(d.get("cursor") or 0)
    has_more = bool(d.get("has_more"))
    total = int(d.get("total") or 0)
    return comments, next_cursor, has_more, total


def simplify_comment(c: dict) -> dict[str, Any]:
    """TikHub 评论 -> 精简条目。"""
    u = c.get("user") or {}
    return {
        "cid": str(c.get("cid") or ""),
        "nickname": u.get("nickname") or "",
        "text": c.get("text") or "",
        "digg": c.get("digg_count", 0),
        "reply": c.get("reply_comment_total", 0),
        "ip": c.get("ip_label") or "",
        "create": c.get("create_time", 0),
    }


def fetch_top_comments(
    aweme_id: str,
    top_n: int = 5,
    max_pages: int = 1,
    token: str | None = None,
    on_progress: ProgressFn | None = None,
) -> list[dict[str, Any]]:
    """拉点赞 TOP N 评论。

    抖音评论接口默认就是「热门序」(点赞+回复加权),最高赞评论实测就堆在第一页
    (某 9818 条评论的视频:第1页最高赞 133,第2页骤降到 3,之后全是 0/1)。
    所以默认只扫第一页——旧的「翻几页+早停」在长尾全 0 赞的评论区永远停不下来,
    纯属浪费 API 配额。需要更深覆盖时调大 max_pages 即可。
    """
    token = get_token(token)
    collected: list[dict[str, Any]] = []
    cursor, page = 0, 0
    while page < max_pages:
        comments, cursor, has_more, total = fetch_comments_page(aweme_id, cursor, COMMENTS_PAGE_SIZE, token)
        page += 1
        if not comments:
            break
        page_max = max((c.get("digg_count", 0) for c in comments), default=0)
        collected.extend(simplify_comment(c) for c in comments)
        if on_progress:
            on_progress(f"第 {page} 页,累计 {len(collected)} 条(评论区共约 {total} 条),本页最高赞 {page_max}")
        if not has_more:
            break
        # 早停:已凑满 top_n 且本页最高赞低于当前 TOP N 门槛
        threshold = sorted((c["digg"] for c in collected), reverse=True)[:top_n]
        if len(threshold) == top_n and page_max < threshold[-1]:
            if on_progress:
                on_progress(f"早停:本页最高赞 {page_max} 低于 TOP{top_n} 门槛 {threshold[-1]}")
            break
        time.sleep(0.3)
    collected.sort(key=lambda c: c["digg"], reverse=True)
    return collected[:top_n]
