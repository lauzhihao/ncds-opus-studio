"""模板路由：暴露 templates/ 目录给 daoer。

- GET /templates                       列出所有模板与其 template.json 元
- GET /templates/{name}/episode.json   返回该模板的初始 episode.json starter

当前 templates/{name}/ 里只有 beats.js 风格的资源（没有 episode.json），
所以服务端组装一个最小可用的 starter（1 个示例 beat + 1 个示例 scene），
画布编辑器拿去当作初始状态后用户继续填内容。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter()

_TEMPLATES_ROOT = (
    Path(__file__).resolve().parents[2] / "templates"  # ncds_opus_factory/templates
)


# ============================================================
# Starter episode 工厂
# ============================================================

def _default_tts_cfg() -> dict[str, Any]:
    return {
        "engine": "dashscope-cosyvoice",
        "model": "cosyvoice-v3-flash",
        "voice": "longtian_v3",
        "sampleRate": 22050,
        "format": "mp3",
        "rate": 1.1,
    }


def _default_image_cfg() -> dict[str, Any]:
    return {
        "engine": "gpt-image-2",
        "size": "1536x1024",
        "quality": "auto",
        "noTextHint": (
            "严格要求：整张图绝对不能出现任何中文字、汉字、英文字母、"
            "阿拉伯数字或者标点。所有标签、标牌、招牌、徽章、吊牌内部必须"
            "完全空白（留出贴标签的位置），不要画任何文字或文字纹理。"
        ),
    }


def make_starter_episode(template_id: str, title: str) -> dict[str, Any]:
    """组装一个最小可用的 paper-card-talk 风格 episode starter。

    画布加载后用户可以：
    - 改 meta.title / brandTitle
    - 加 beats 到 beats[]
    - 加 scene 到 scenes{}
    - 编辑 audio.tts / image / visual / playback 配置
    """
    return {
        "__schema__": "ncds-paper-card-talk/v1",
        "__doc__": (
            "从模板创建的画布 starter。在编辑器里增删 beats[] 和 scenes{} 即可，"
            "其它字段（audio.tts / image / visual）通常按默认运行。"
        ),
        "meta": {
            "slug": template_id,
            "title": title,
            "brandTitle": title,
            "disclaimer": "",
            "titleOptions": [title],
        },
        "fonts": [],
        "visual": {
            "palette": "paper",
            "bandStyle": "paper",
            "kenBurns": True,
            "showSubtitleEn": True,
            "capZhSize": 60,
            "capEnSize": 40,
        },
        "playback": {"rate": 0.95},
        "audio": {"tts": _default_tts_cfg()},
        "image": _default_image_cfg(),
        "beats": [
            {
                "zh": "示例：在这里写第一句字幕",
                "en": "Example: write your first subtitle here",
                "scene": "intro",
            }
        ],
        "scenes": {
            "intro": {
                "prompt": (
                    "扁平插画。米黄纸质底色，画面中央一支立着的钢笔，"
                    "旁边一本翻开的笔记本，留白干净。"
                ),
                "label": "",
                "motion": {"enter": "fade", "duration": 700},
            }
        },
    }


# ============================================================
# Endpoints
# ============================================================

def _list_template_dirs() -> list[Path]:
    if not _TEMPLATES_ROOT.exists():
        return []
    return sorted(
        p for p in _TEMPLATES_ROOT.iterdir()
        if p.is_dir() and (p / "template.json").exists()
    )


def _read_template_meta(template_dir: Path) -> dict[str, Any]:
    return json.loads((template_dir / "template.json").read_text(encoding="utf-8"))


@router.get("/templates")
async def list_templates() -> dict[str, Any]:
    """列出所有模板与其 template.json 元信息。"""
    items: list[dict[str, Any]] = []
    for d in _list_template_dirs():
        try:
            meta = _read_template_meta(d)
        except Exception as exc:
            logger.warning("[templates] failed to read %s/template.json: %s", d.name, exc)
            continue
        items.append({
            "name": d.name,
            "id": meta.get("id"),
            "title": meta.get("title"),
            "description": meta.get("description"),
            "version": meta.get("version"),
        })
    return {"templates": items}


@router.get("/templates/{name}/episode.json")
async def get_template_episode(name: str) -> dict[str, Any]:
    """返回某模板的 starter episode.json。"""
    template_dir = _TEMPLATES_ROOT / name
    if not template_dir.is_dir() or not (template_dir / "template.json").exists():
        raise HTTPException(404, f"template not found: {name}")
    meta = _read_template_meta(template_dir)
    template_id = meta.get("id") or name
    title = meta.get("title") or name
    return make_starter_episode(template_id=template_id, title=title)
