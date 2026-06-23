"""STORYBOARD（视觉方案）director agent：prompt 构造 + 输出解析。

吴道子的职责边界：
- 上游 lines 只负责脚本，把定稿结构化成逐句字幕 ``beats[]``。
- 本模块只负责视觉层：一张全片统一舞台背景 + 每条完整字幕一个视觉 shot。
- ``beats[].scene`` 不再驱动画面切换；它只保留给 TTS 做粗粒度音频分段。
- 出图代码读取 ``episode.visual.shots[]``，机械调用底层文生图能力，不做创意决策。

输出契约（director 必须严格返回 JSON）：

{
  "visual": {
    "style": "paper_card_talk",
    "stage": {
      "background": {
        "prompt": "全片统一背景图中文 prompt：暖纸底 + 极简舞台/留白，不含主体",
        "imageFit": "cover"
      },
      "palette": {"paper": "#F4EBDD", "ink": "#1E1A16", "accent": "#B7352D"},
      "shotRhythm": "one-shot-per-beat"
    },
    "shots": [
      {
        "beatIndex": 1,
        "shotId": "b001",
        "group": "S1-01",
        "intent": "这一句字幕的画面含义",
        "layout": "center_icon",
        "transition": "replace",
        "motion": {"enter": "fade", "duration": 500},
        "emphasis": [
          {
            "text": "关键词",
            "pos": {"x": 50, "y": 22},
            "style": {"size": 54, "weight": 800, "color": "#2A241E"},
            "motion": {"enter": "handwrite", "duration": 500}
          }
        ],
        "assets": [
          {
            "id": "a1",
            "role": "main",
            "prompt": "english single-shot pictogram content",
            "pos": {"x": 50, "y": 50},
            "size": 32,
            "motion": {"enter": "zoom-pop", "duration": 500}
          }
        ]
      }
    ]
  }
}
"""

from __future__ import annotations

import json
import re
from typing import Any

# 单格前景素材出图时自动前置的风格圣经。director 只写每个素材的独有内容。
DEFAULT_SKETCH_STYLE_PREFIX = (
    "Minimalist pictogram in the universal public-signage style, like airport "
    "wayfinding icons. Flat solid-black silhouette on a plain pure-white background. "
    "Simple rounded head, smooth thick rounded limbs, no neck, no face, no fingers, "
    "no interior lines, no outline -- filled black shapes only. Limbs clearly "
    "separated from the torso so the silhouette reads at a glance. Flat front or "
    "side view, no perspective, no foreshortening, no cast shadow. One single "
    "subject or symbol, generous empty negative space, pure black and white, no "
    "gray, no gradient, no color."
)

ASSET_ENTERS = [
    "fade",
    "zoom-pop",
    "drift-in",
    "bounce",
    "ink-bleed",
    "slide-clip",
    "handwrite",
]

SHOT_LAYOUTS = {
    "center_icon",
    "left_icon_text",
    "right_icon_text",
    "icon_pair",
    "emphasis_text",
    "card_accumulate",
    "timeline",
    "comparison",
    "empty_pause",
}

SHOT_TRANSITIONS = {"replace", "accumulate", "hold", "morph"}

DIRECTOR_SYSTEM_PROMPT = (
    "你是一名极简主义动画导演，深谙人类心理学。脚本逐句字幕已经写好，"
    "你不改一个字，只把它导成画面：一张全片统一舞台背景，以及每条完整字幕"
    "对应的一个视觉 shot。只输出一个合法 JSON 对象，禁止代码块或任何额外文本。"
)


def _shot_prompt_spec() -> list[str]:
    """单格前景素材 prompt 规范，给 director 当规则。"""
    return [
        "【剪影可读性五铁律】",
        "1. 轮廓测试：填成纯黑、只看外形也认得出在干嘛；手脚甩出躯干，别叠在身体上糊成一坨。",
        "2. 一条动作线：整个身体顺一条清晰曲线——蜷缩 C 形 / 挺立 I 形 / 伸手对角线，一格只立一条线。",
        "3. 平视或正侧，不要透视、不要前缩。",
        "4. 多个剪影之间留白缝，绝不用影子 / 地面 / 连接线把两个黑块焊在一起。",
        "5. 一格一概念：一个主体动作 + 最多一个符号物。",
        "",
        "【字幕驱动画面】每条完整字幕都要切换一次画面内容；背景不变，前景素材/强调文字/布局变化。",
        "不要把多句字幕塞进同一个静态子场景里等待。",
        "",
        "【隐喻必须落地】只画具体的并置 / 比例 / 姿态 / 位置，不画抽象修辞。"
        "想说“孩子在复制你”，就画一大一小两个独立剪影、同一姿态、并排、中间留白；"
        "不要画“影子 / 倒影 / 分身 / 心里的声音”——这类一律糊掉。",
        "",
        "【体态情绪词典】蜷缩抱膝=羞耻封闭；含胸低头盯手机=沉溺；站在巨物前显小=被压垮；"
        "背对走开=逃避；头微抬肩打开=松动；挺立双臂垂面向光=释然。",
        "",
        "【符号系统】先为本片定 2-4 个固定符号反复出现：发光手机 / 屏幕=沉溺；时钟 / 日历=时间流逝；"
        "巨石 / 大方块=太重的事；墙 / 门缝 / 笼=困住；线 / 绳=牵绊；一束光=希望出口。",
        "",
        "【单格 asset.prompt 写法】只写这一格独有的英文内容，顺序：体态(line of action) → "
        "空间关系/构图(focal + 留白方向) → 符号物。不要再写人物长相/画风（圣经已固定）。",
        "例：a small child silhouette curled into a tight C-shape in the lower-left, knees to chest, holding a glowing phone.",
    ]


def build_director_prompt(
    meta: dict[str, Any],
    beats: list[dict[str, Any]],
    *,
    style_bible: str,
    container_guide: str = "",
    palette: str = "",
    domain_image_style: str | None = None,
    sub_scenes_per_scene: tuple[int, int] = (2, 3),
    sketches_per_sub_scene: tuple[int, int] = (1, 3),
) -> tuple[str, str]:
    """构造 director agent 的 ``(system_prompt, user_prompt)``。

    ``sub_scenes_per_scene`` 保留在签名里是为了调用面稳定；新契约不再产子场景。
    ``sketches_per_sub_scene`` 在新契约中表示每个 shot 的前景素材数量范围。
    """
    del sub_scenes_per_scene
    asset_min, asset_max = sketches_per_sub_scene
    lines: list[str] = []
    lines.append("把下面这条短视频的逐句字幕导成吴道子视觉方案 JSON。")
    lines.append("")
    lines.append("【角色边界】脚本已敲定，你不改字。你只设计统一舞台背景与逐字幕 shot。")
    lines.append("")
    lines.append("【核心节奏】")
    lines.append("- 每条完整字幕 = 一个 visual.shots[] 项，shots.length 必须等于 beats 条数；")
    lines.append("- beatIndex 必须覆盖每条 beat.index（1-based），不漏不重；")
    lines.append("- 画面切换由 shot 驱动，不再由 scene/subscene 驱动；")
    lines.append("- group 只用于粗粒度音频/展示分组，建议每 3-6 句同一组，不能替代逐字幕 shot；")
    lines.append("- 参考短视频的可借鉴点只有“字幕驱动内容变化”：背景像固定 PPT 舞台，前景素材与强调文字跟着每句变化。")
    lines.append("")
    lines.append("【stage.background.prompt（中文）】")
    lines.append("- 只生成 1 张全片共用背景，作为最终成片整张 16:9 页面背景；")
    lines.append("- 背景图必须覆盖整张画面，不能只在下方字幕区出现景物，不能写“上方留白 / 大面积留白”；")
    lines.append("- 暖纸纸质底 + 贯穿全幅的稀疏场景氛围 / 远景层次；主体元素留给前景素材，背景不要画成主角；")
    lines.append("- 如果使用山形、海岸线、城市轮廓等地景，需要让它自然延展为全页底图，而不是只贴在底边；")
    lines.append("- 背景应能承载整条片子，不能跟某一句台词绑定，不能出现人物主体；")
    if container_guide:
        lines.append(f"- 额外约束：{container_guide}")
    if palette:
        lines.append(f"- 配色：{palette}")
    if domain_image_style:
        lines.append(f"- 【本片领域视觉调性】（仅供参考，叠加在上述风格之外）：{domain_image_style}")
    lines.append("- background.prompt 里不要出现任何文字 / 数字。")
    lines.append("")
    lines.append(f"【shot.assets（每个 shot {asset_min}-{asset_max} 个前景素材）】")
    lines.append("- 前景素材是白底黑剪影元素，叠在统一背景上；")
    lines.append("- 下面这段风格圣经会在出图时自动前置到每条 asset.prompt，你写单格内容时默认它已存在，不要重复写画风：")
    lines.append(f"  「{style_bible}」")
    lines.extend(_shot_prompt_spec())
    lines.append("")
    lines.append("【shot 字段】")
    lines.append("- shotId 形如 b001 / b002；")
    lines.append(f"- layout 从这些里选：{', '.join(sorted(SHOT_LAYOUTS))}；")
    lines.append(f"- transition 从这些里选：{', '.join(sorted(SHOT_TRANSITIONS))}；")
    lines.append("- intent：中文短句，说明这一句字幕在画面上讲什么；")
    lines.append("- emphasis：可选的强调文字，必须短，不要复制整句字幕；")
    lines.append("- assets[].pos {x,y}：画面百分比位置（0-100，左上原点）；size：宽度占画面百分比；")
    lines.append(f"- assets[].motion.enter 从这些里选：{', '.join(ASSET_ENTERS)}；duration 400-700ms。")
    lines.append("")
    lines.append("【输出格式】只输出一个 JSON 对象，结构严格如下，不要代码块、不要解释：")
    lines.append("{")
    lines.append('  "visual": {')
    lines.append('    "style": "paper_card_talk",')
    lines.append('    "stage": {')
    lines.append('      "background": { "prompt": "全片统一背景图中文 prompt", "imageFit": "cover" },')
    lines.append('      "palette": { "paper": "#F4EBDD", "ink": "#1E1A16", "accent": "#B7352D" },')
    lines.append('      "shotRhythm": "one-shot-per-beat"')
    lines.append("    },")
    lines.append('    "shots": [')
    lines.append("      {")
    lines.append('        "beatIndex": 1, "shotId": "b001", "group": "S1-01",')
    lines.append('        "intent": "这一句字幕的画面含义",')
    lines.append('        "layout": "center_icon", "transition": "replace",')
    lines.append('        "motion": { "enter": "fade", "duration": 500 },')
    lines.append('        "emphasis": [')
    lines.append('          { "text": "关键词", "pos": {"x":50,"y":22}, "style": {"size":54,"weight":800}, "motion": {"enter":"handwrite","duration":500} }')
    lines.append("        ],")
    lines.append('        "assets": [')
    lines.append('          { "id": "a1", "role": "main", "prompt": "english single-shot pictogram content", "pos": {"x":50,"y":52}, "size":32, "motion": {"enter":"zoom-pop","duration":500} }')
    lines.append("        ]")
    lines.append("      }")
    lines.append("    ]")
    lines.append("  }")
    lines.append("}")
    lines.append("")
    title = str((meta or {}).get("title") or "")
    if title:
        lines.append(f"== 视频标题：{title} ==")
    lines.append("== 脚本 beats（index. zh）==")
    for b in beats:
        idx = b.get("index")
        zh = str(b.get("zh") or "")
        en = str(b.get("en") or "")
        suffix = f"  // {en}" if en else ""
        lines.append(f"{idx}. {zh}{suffix}")
    lines.append("== 脚本结束 ==")

    return DIRECTOR_SYSTEM_PROMPT, "\n".join(lines)


def _strip_code_fence(raw: str) -> str:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        inner = re.match(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$", cleaned)
        if inner:
            cleaned = inner.group(1).strip()
    return cleaned


def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))


def _norm_motion(raw: Any, *, default_enter: str) -> dict[str, Any]:
    m = raw if isinstance(raw, dict) else {}
    enter = m.get("enter")
    if not isinstance(enter, str) or not enter.strip():
        enter = default_enter
    out: dict[str, Any] = {"enter": enter.strip()}
    if isinstance(m.get("duration"), (int, float)):
        out["duration"] = int(_clamp(m["duration"], 100, 2000, 500))
    if isinstance(m.get("delay"), (int, float)):
        out["delay"] = int(_clamp(m["delay"], 0, 5000, 0))
    if isinstance(m.get("easing"), str) and m["easing"].strip():
        out["easing"] = m["easing"].strip()
    return out


def _norm_pos(raw: Any, *, default_x: float = 50, default_y: float = 50) -> dict[str, float]:
    pos = raw if isinstance(raw, dict) else {}
    return {
        "x": _clamp(pos.get("x"), 0, 100, default_x),
        "y": _clamp(pos.get("y"), 0, 100, default_y),
    }


def _safe_id(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = fallback
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-")
    return safe or fallback


def _fallback_asset_prompt(beat: dict[str, Any]) -> str:
    zh = str(beat.get("zh") or "").strip()
    return f"a simple black pictogram symbolizing this line: {zh}"


def _norm_asset(raw: Any, *, shot_id: str, index: int, beat: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        return None
    asset_id = _safe_id(raw.get("id"), f"a{index}")
    out: dict[str, Any] = {
        "id": asset_id,
        "role": str(raw.get("role") or ("main" if index == 1 else "support")).strip(),
        "prompt": prompt,
        "pos": _norm_pos(raw.get("pos"), default_x=50, default_y=50),
        "size": _clamp(raw.get("size"), 5, 100, 32),
        "motion": _norm_motion(raw.get("motion"), default_enter="zoom-pop"),
        "imageFile": f"pictures/{shot_id}-{asset_id}.webp",
    }
    at = raw.get("at")
    if isinstance(at, dict) and str(at.get("match") or "").strip():
        out["at"] = {"match": str(at["match"]).strip()}
        if isinstance(at.get("delay"), (int, float)):
            out["at"]["delay"] = int(_clamp(at["delay"], 0, 5000, 0))
    # beat is part of the signature so fallback creation can share this normalizer.
    del beat
    return out


def _fallback_asset(*, shot_id: str, beat: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "a1",
        "role": "main",
        "prompt": _fallback_asset_prompt(beat),
        "pos": {"x": 50, "y": 52},
        "size": 34,
        "motion": {"enter": "zoom-pop", "duration": 500},
        "imageFile": f"pictures/{shot_id}-a1.webp",
    }


def _norm_emphasis(raw: Any, *, index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()
    if not text:
        return None
    out: dict[str, Any] = {
        "id": _safe_id(raw.get("id"), f"t{index}"),
        "text": text[:18],
        "pos": _norm_pos(raw.get("pos"), default_x=50, default_y=22),
        "style": raw.get("style") if isinstance(raw.get("style"), dict) else {},
        "motion": _norm_motion(raw.get("motion"), default_enter="handwrite"),
    }
    return out


def _norm_stage(parsed: dict[str, Any], shots: list[dict[str, Any]]) -> dict[str, Any]:
    visual_in = parsed.get("visual") if isinstance(parsed.get("visual"), dict) else {}
    stage_in = visual_in.get("stage") if isinstance(visual_in.get("stage"), dict) else parsed.get("stage")
    stage_in = stage_in if isinstance(stage_in, dict) else {}
    bg_in = stage_in.get("background") if isinstance(stage_in.get("background"), dict) else {}
    bg_prompt = str(bg_in.get("prompt") or "").strip()
    if not bg_prompt:
        bg_prompt = next((str(s.get("intent") or "").strip() for s in shots if str(s.get("intent") or "").strip()), "")
    return {
        "background": {
            "prompt": bg_prompt or "16:9 全幅暖纸质感页面背景，淡雅远景层次贯穿整张画面，细腻纸张纹理，主体元素留给前景素材，无文字，无数字。",
            "imageFit": bg_in.get("imageFit") if bg_in.get("imageFit") in ("cover", "contain", "fill") else "cover",
            "imageFile": "pictures/background.webp",
        },
        "palette": stage_in.get("palette") if isinstance(stage_in.get("palette"), dict) else {},
        "shotRhythm": "one-shot-per-beat",
    }


def _group_for_index(i: int) -> str:
    return f"S1-{((i - 1) // 4) + 1:02d}"


def _norm_shot(raw: Any, *, beat: dict[str, Any], index: int) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    shot_id = _safe_id(src.get("shotId") or src.get("id"), f"b{index:03d}")
    layout = str(src.get("layout") or "center_icon").strip()
    if layout not in SHOT_LAYOUTS:
        layout = "center_icon"
    transition = str(src.get("transition") or "replace").strip()
    if transition not in SHOT_TRANSITIONS:
        transition = "replace"

    assets_raw = src.get("assets") if isinstance(src.get("assets"), list) else []
    assets = [
        a for a in (
            _norm_asset(item, shot_id=shot_id, index=n, beat=beat)
            for n, item in enumerate(assets_raw, start=1)
        )
        if a is not None
    ]
    if not assets and layout != "empty_pause":
        assets = [_fallback_asset(shot_id=shot_id, beat=beat)]

    emphasis_raw = src.get("emphasis") if isinstance(src.get("emphasis"), list) else []
    emphasis = [
        e for e in (
            _norm_emphasis(item, index=n)
            for n, item in enumerate(emphasis_raw, start=1)
        )
        if e is not None
    ]

    group = str(src.get("group") or "").strip() or _group_for_index(index)
    return {
        "beatIndex": index,
        "shotId": shot_id,
        "group": group,
        "intent": str(src.get("intent") or beat.get("zh") or "").strip(),
        "layout": layout,
        "transition": transition,
        "motion": _norm_motion(src.get("motion"), default_enter="fade"),
        "emphasis": emphasis,
        "assets": assets,
    }


def parse_director_output(raw: str, beats: list[dict[str, Any]]) -> dict[str, Any]:
    """解析 + 规整 director 输出，返回 ``episode.visual``。

    新版本不把旧 ``scenes/sceneMap`` 当成成功结果。模型吐旧契约会触发重试，
    避免历史字段继续绑住吴道子。
    """
    cleaned = _strip_code_fence(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"director 输出非法 JSON：{exc}；tail={cleaned[-300:]}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError("director 输出不是 JSON 对象")

    visual_in = parsed.get("visual") if isinstance(parsed.get("visual"), dict) else {}
    shots_in = visual_in.get("shots") if isinstance(visual_in.get("shots"), list) else parsed.get("shots")
    if not isinstance(shots_in, list) or not shots_in:
        raise ValueError("director 输出缺 visual.shots[] 或为空")

    by_index: dict[int, Any] = {}
    for item in shots_in:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("beatIndex"))
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= len(beats) and idx not in by_index:
            by_index[idx] = item

    shots: list[dict[str, Any]] = []
    for i, beat in enumerate(beats, start=1):
        shots.append(_norm_shot(by_index.get(i), beat=beat, index=i))

    stage = _norm_stage(parsed, shots)
    style = str(visual_in.get("style") or parsed.get("style") or "paper_card_talk").strip()
    return {
        "style": style or "paper_card_talk",
        "stage": stage,
        "shots": shots,
    }
