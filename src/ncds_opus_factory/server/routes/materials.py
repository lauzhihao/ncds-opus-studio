"""素材库占位接口。

当前版本只提供前端 UI 对接所需的稳定契约，不扫描本地文件夹。
未来实现应改为查询素材索引（数据库 / 向量库），素材文件由对象存储提供 URL。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ncds_opus_factory.common import works_repo
from ncds_opus_factory.server.artifacts import file_url
from ncds_opus_factory.server.state import PIPELINE_RUNNER

router = APIRouter()

MaterialScope = Literal["current_job", "same_author", "same_domain", "global"]


class AttachMaterialBody(BaseModel):
    shot_id: str
    material_id: str
    pos: dict[str, float] | None = None
    size: float | None = None
    motion: dict[str, Any] | None = None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _short_text(value: Any, limit: int = 42) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _known_work_cutouts(platform: str, aweme_id: str) -> list[str]:
    """局部兜底：只看当前 job 已关联作品自己的 cutouts 子目录。

    正式素材库接入后应改由索引表/向量库返回；这里不做全局目录扫描。
    """
    cutout_dir = works_repo.works_root() / platform / aweme_id / "cutouts"
    if not cutout_dir.exists():
        return []
    return [str(p) for p in sorted(cutout_dir.glob("*.png"))]


def _current_job_materials(job_id: str, *, q: str, tag_filter: list[str], limit: int) -> list[dict[str, Any]]:
    """列当前 job 已知的沈括 cutouts。

    只读取 job state 中已知的 aweme_id / cutout relpath，必要时按 aweme_id 读取对应 manifest。
    不扫描 ``state/works`` 或任何本地目录。
    """
    try:
        state = PIPELINE_RUNNER.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, f"job not found: {job_id}") from exc

    asr = state.nodes.get("asr")
    collected = _as_list((asr.outputs if asr else {}).get("collected"))
    if not collected and asr:
        collected = _as_list((asr.outputs or {}).get("items"))

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    query_tokens = [t.lower() for t in q.split() if t.strip()]
    wanted_tags = {t.strip().lower() for t in tag_filter if t.strip()}

    for entry in collected:
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        aweme_id = str(entry.get("aweme_id") or "").strip()
        if not aweme_id:
            continue
        platform = str(entry.get("platform") or "douyin").strip() or "douyin"
        manifest = works_repo.load_manifest(platform, aweme_id) or {}
        products = manifest.get("products") if isinstance(manifest.get("products"), dict) else {}
        card = manifest.get("card") if isinstance(manifest.get("card"), dict) else {}
        cutouts = _as_list(entry.get("cutouts")) or _as_list(products.get("cutouts"))
        if not cutouts:
            cutouts = _known_work_cutouts(platform, aweme_id)
        if not cutouts:
            continue

        title = _short_text(entry.get("desc") or card.get("title") or card.get("desc") or aweme_id)
        author = str(entry.get("author") or ((card.get("author") or {}).get("nickname") if isinstance(card.get("author"), dict) else "") or "")
        source_label = f"{title} · @{author}" if author else title
        tags = []
        for t in _as_list(entry.get("hashtags")) + _as_list(card.get("hashtags")) + _as_list(entry.get("tags")):
            s = str(t).strip()
            if s and s not in tags:
                tags.append(s)
        domain = manifest.get("domain") if isinstance(manifest.get("domain"), str) else None
        if domain and domain not in tags:
            tags.append(domain)

        searchable = " ".join([title, author, aweme_id, " ".join(tags)]).lower()
        if query_tokens and not all(t in searchable for t in query_tokens):
            continue
        if wanted_tags and not wanted_tags.issubset({t.lower() for t in tags}):
            continue

        for rel_raw in cutouts:
            rel = str(rel_raw or "").strip()
            if not rel or rel in seen:
                continue
            seen.add(rel)
            url = file_url(rel)
            if not url:
                continue
            name = Path(rel).name
            out.append({
                "id": f"shenkuo:{platform}:{aweme_id}:cutout:{Path(rel).stem}",
                "kind": "cutout",
                "title": name,
                "preview_url": url,
                "object_url": url,
                "source_label": source_label,
                "source": {
                    "platform": platform,
                    "aweme_id": aweme_id,
                    "job_id": job_id,
                },
                "tags": tags,
                "domain": domain,
                "usage_count": 0,
            })
            if len(out) >= limit:
                return out
    return out


@router.get("/materials")
async def list_materials(
    job_id: str | None = Query(default=None),
    scope: MaterialScope = "current_job",
    q: str = "",
    tags: str = "",
    limit: int = Query(default=60, ge=1, le=120),
) -> dict[str, Any]:
    """查询可复用素材。

    TODO(material-library):
    - 不要扫描 ``state/works`` 或任何本地目录。
    - 从素材索引表 / 向量数据库读取，按 ``job_id`` 派生当前作品、同作者、同赛道等 scope。
    - 素材文件迁到云端对象存储，返回可直接渲染的 ``preview_url`` / ``object_url``。
    - 沈括写入 cutout 后异步注册素材，并用视觉模型自动补 tags / embedding。
    - 用户手工改过的标签作为高优先级人工标签，自动标签只能补充，不能覆盖。
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if scope == "current_job" and job_id:
        items = _current_job_materials(job_id, q=q, tag_filter=tag_list, limit=limit)
        return {
            "index_ready": True,
            "items": items,
            "query": {
                "job_id": job_id,
                "scope": scope,
                "q": q,
                "tags": tag_list,
                "limit": limit,
            },
            "scopes": [
                {"id": "current_job", "label": "当前任务"},
                {"id": "same_author", "label": "同作者"},
                {"id": "same_domain", "label": "同赛道"},
                {"id": "global", "label": "全库"},
            ],
            "todo": "当前任务素材来自 job 已知沈括产物；同作者/同赛道/全库后续接数据库/向量库索引。",
        }

    return {
        "index_ready": False,
        "items": [],
        "query": {
            "job_id": job_id,
            "scope": scope,
            "q": q,
            "tags": tag_list,
            "limit": limit,
        },
        "scopes": [
            {"id": "current_job", "label": "当前任务"},
            {"id": "same_author", "label": "同作者"},
            {"id": "same_domain", "label": "同赛道"},
            {"id": "global", "label": "全库"},
        ],
        "todo": "素材索引尚未接入：后续由数据库/向量库查询，图片由对象存储 URL 渲染。",
    }


@router.post("/jobs/{job_id}/nodes/image/materials")
async def attach_image_material(job_id: str, body: AttachMaterialBody) -> dict[str, Any]:
    """把素材库中的素材挂到吴道子某个 shot。

    TODO(material-library):
    - 校验 material_id 来自索引，复制或固化对象存储文件到当前 job 的 03_image。
    - 更新 ``02_rw/episode.json`` 的 ``visual.shots[].assets[]``，写入 imageFile/source/tags。
    - 同步 patch image 节点 outputs.items，确保前端 SSE 立即刷新。
    """
    raise HTTPException(
        status_code=501,
        detail={
            "message": "素材库挂载接口尚未接入",
            "job_id": job_id,
            "shot_id": body.shot_id,
            "material_id": body.material_id,
        },
    )
