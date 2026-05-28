"""STORYBOARD（分镜）阶段的 director agent —— prompt 构造 + 输出解析。

职责边界
--------
- rw / lines 只负责脚本：把源稿改写、结构化成逐句字幕 beats[]（zh/en）。
- 本模块对应的 director agent 只负责**视觉层**：读已敲定的 beats，
  把脚本切成子场景（每个叙事段落 2-3 个子场景），再为每个子场景产出
    1) 容器图 prompt（稀疏背景底图，主体交给简笔画）
    2) 1-6 幅简笔画 sketch（单格黑剪影元素，含位置 / 入场动效 / 跟哪句台词关键词飞入）
- 出图代码拿本模块解析后的 scenes{} 机械调 gpt-image-2，不做任何创意决策。

人格 = whisper-reel 技能的「心理学家 + 极简动画导演」。这里只取它的**导演方法论**
（剪影五铁律 / 体态情绪词典 / 符号系统 / 构图 / 单格 prompt 规范），不含文案改写部分
（脚本已经由上游敲定）。

输出契约（director 必须严格返回的 JSON）
----------------------------------------
{
  "sceneMap": { "1": "S1-01a", "2": "S1-01a", "3": "S1-01b", ... },  # beat 序号(1-based) -> 子场景 id
  "scenes": {
    "S1-01a": {
      "group": "S1-01",                 # 同一叙事段落的 2-3 子场景共享前缀，纯展示分组
      "prompt": "容器图中文 prompt：暖纸底 + 稀疏场景背景，主体留给简笔画",
      "imageFit": "contain",
      "motion": { "enter": "fade", "duration": 700 },
      "sketches": [
        { "prompt": "english single-shot content（圣经自动前置）",
          "pos": { "x": 28, "y": 62 }, "size": 34,
          "motion": { "enter": "zoom-pop", "duration": 500 },
          "at": { "match": "信用卡" } }
      ]
    }
  }
}
"""

from __future__ import annotations

import json
import re
from typing import Any

# whisper-reel 风格圣经（单格简笔画每次出图自动前置；与模板 episode.json
# image.sketchStylePrefix 保持一致，模板缺省时用这个兜底）。
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

# 简笔画 motion.enter 安全取值（与 overlays.js motionToClass 对齐）
SKETCH_ENTERS = [
    "fade", "zoom-pop", "drift-in", "bounce", "ink-bleed", "slide-clip", "handwrite",
]


DIRECTOR_SYSTEM_PROMPT = (
    "你是一名极简主义动画导演，深谙人类心理学。你只用最干净的黑色剪影和最大的留白，"
    "让每个画面只讲一件事。现在脚本（逐句字幕）已经写好，你**不改一个字**，只负责把它"
    "导成画面：切子场景 + 为每个子场景设计容器底图和叠在上面的简笔画。"
    "只输出一个合法 JSON 对象，禁止代码块或任何额外文本。"
)


def _shot_prompt_spec() -> list[str]:
    """单格简笔画 prompt 规范（剪影导演方法论，给 director 当规则）。"""
    return [
        "【剪影可读性五铁律】",
        "1. 轮廓测试：填成纯黑、只看外形也认得出在干嘛；手脚甩出躯干，别叠在身体上糊成一坨。",
        "2. 一条动作线：整个身体顺一条清晰曲线——蜷缩 C 形 / 挺立 I 形 / 伸手对角线，一格只立一条线。",
        "3. 平视或正侧，不要透视、不要前缩。",
        "4. 多个剪影之间留白缝，绝不用影子 / 地面 / 连接线把两个黑块焊在一起。",
        "5. 一格一概念：一个主体动作 + 最多一个符号物。",
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
        "【单格 sketch.prompt 写法】只写这一格独有的英文内容，顺序：体态(line of action) → "
        "空间关系/构图(focal + 留白方向) → 符号物。**不要**再写人物长相/画风（圣经已固定）。"
        "例：a small child silhouette curled into a tight C-shape in the lower-left, knees to "
        "chest, holding a glowing phone, vast empty space upper-right.",
    ]


def build_director_prompt(
    meta: dict[str, Any],
    beats: list[dict[str, Any]],
    *,
    style_bible: str,
    container_guide: str = "",
    palette: str = "",
    sub_scenes_per_scene: tuple[int, int] = (2, 3),
    sketches_per_sub_scene: tuple[int, int] = (1, 6),
) -> tuple[str, str]:
    """构造 director agent 的 (system_prompt, user_prompt)。

    beats: [{ "index": 1, "zh": "...", "en": "..." }]，index 为 1-based。
    """
    lines: list[str] = []
    lines.append("把下面这条短视频的脚本（逐句字幕）导成分镜，输出视觉层 JSON。")
    lines.append("")
    lines.append("【角色边界】脚本已敲定，你不改字。你只切子场景 + 设计容器底图与简笔画。")
    lines.append("")
    lines.append("【切分规则】")
    lines.append(
        f"- 先把脚本按语义切成若干叙事段落，每个叙事段落再切 "
        f"{sub_scenes_per_scene[0]}-{sub_scenes_per_scene[1]} 个子场景；"
    )
    lines.append("- 子场景 id 形如 S1-01a / S1-01b（同段落共享前缀，写进 group 字段）；")
    lines.append("- sceneMap 必须覆盖每一条 beat（按其 index 映射到一个子场景 id），不漏不重。")
    lines.append("")
    lines.append("【容器图 prompt（中文）】")
    lines.append("- 暖纸纸质底 + 稀疏场景背景/留白，主体元素留给简笔画，不要画满；")
    if container_guide:
        lines.append(f"- 额外约束：{container_guide}")
    if palette:
        lines.append(f"- 配色：{palette}")
    lines.append("- prompt 里不要出现任何文字 / 数字。")
    lines.append("")
    lines.append(
        f"【简笔画 sketches（每个子场景 {sketches_per_sub_scene[0]}-"
        f"{sketches_per_sub_scene[1]} 幅）】"
    )
    lines.append(
        "- 每幅 sketch 是一个白底黑剪影元素，叠在容器图上。下面这段风格圣经会在出图时"
        "自动前置到每条 sketch.prompt，你写单格内容时**默认它已存在**，不要重复写画风："
    )
    lines.append(f"  「{style_bible}」")
    lines.extend(_shot_prompt_spec())
    lines.append("")
    lines.append("【pos / size / motion / at】")
    lines.append("- pos {x,y}：简笔画在容器内的百分比位置（0-100，左上原点）；size：宽度占容器百分比；")
    lines.append(f"- motion.enter 从这些里选：{', '.join(SKETCH_ENTERS)}；duration 400-700ms；")
    lines.append(
        "- at.match：填该子场景内某条 beat.zh 里的一个关键词（2-6 字），简笔画会在台词读到"
        "这个词时飞入；不需要跟台词触发的（如背景陪衬）可省略 at（子场景切入即显）。"
    )
    lines.append("")
    lines.append("【输出格式】只输出一个 JSON 对象，结构严格如下，不要代码块、不要解释：")
    lines.append("{")
    lines.append('  "sceneMap": { "1": "S1-01a", "2": "S1-01a", "3": "S1-01b" },')
    lines.append('  "scenes": {')
    lines.append('    "S1-01a": {')
    lines.append('      "group": "S1-01",')
    lines.append('      "prompt": "容器图中文 prompt",')
    lines.append('      "imageFit": "contain",')
    lines.append('      "motion": { "enter": "fade", "duration": 700 },')
    lines.append('      "sketches": [')
    lines.append(
        '        { "prompt": "english single-shot content", '
        '"pos": {"x":28,"y":62}, "size":34, '
        '"motion": {"enter":"zoom-pop","duration":500}, "at": {"match":"信用卡"} }'
    )
    lines.append("      ]")
    lines.append("    }")
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
        out["duration"] = int(m["duration"])
    if isinstance(m.get("delay"), (int, float)):
        out["delay"] = int(m["delay"])
    return out


def _norm_sketch(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        return None
    pos_in = raw.get("pos") if isinstance(raw.get("pos"), dict) else {}
    sk: dict[str, Any] = {
        "prompt": prompt,
        "pos": {
            "x": _clamp(pos_in.get("x"), 0, 100, 50),
            "y": _clamp(pos_in.get("y"), 0, 100, 50),
        },
        "size": _clamp(raw.get("size"), 5, 100, 32),
        "motion": _norm_motion(raw.get("motion"), default_enter="zoom-pop"),
    }
    at = raw.get("at")
    if isinstance(at, dict) and str(at.get("match") or "").strip():
        sk["at"] = {"match": str(at["match"]).strip()}
        if isinstance(at.get("delay"), (int, float)):
            sk["at"]["delay"] = int(at["delay"])
    return sk


def parse_director_output(
    raw: str, beats: list[dict[str, Any]]
) -> tuple[dict[int, str], dict[str, dict[str, Any]]]:
    """解析 + 规整 director 输出。

    返回 (scene_by_beat_index, scenes)：
    - scene_by_beat_index: {1-based beat index -> scene_id}，已补全所有 beat
      （sceneMap 缺的 beat 沿用前一条的 scene；首条缺则用第一个出现的 scene）。
    - scenes: 规整后的 scenes{}，每个含 prompt / imageFit / motion / overlays[] / sketches[]。

    解析失败 / 结构非法时 raise RuntimeError / ValueError。
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

    scenes_in = parsed.get("scenes")
    scene_map_in = parsed.get("sceneMap")
    if not isinstance(scenes_in, dict) or not scenes_in:
        raise ValueError("director 输出缺 scenes{} 或为空")
    if not isinstance(scene_map_in, dict) or not scene_map_in:
        raise ValueError("director 输出缺 sceneMap{} 或为空")

    # 规整 scenes
    scenes: dict[str, dict[str, Any]] = {}
    for sid, sc in scenes_in.items():
        sid = str(sid)
        sc = sc if isinstance(sc, dict) else {}
        sketches_raw = sc.get("sketches") if isinstance(sc.get("sketches"), list) else []
        sketches = [s for s in (_norm_sketch(x) for x in sketches_raw) if s is not None]
        scenes[sid] = {
            "prompt": str(sc.get("prompt") or "").strip(),
            "group": str(sc.get("group") or "").strip(),
            "label": "",
            "imageFit": sc.get("imageFit") if sc.get("imageFit") in ("cover", "contain", "fill") else "contain",
            "motion": _norm_motion(sc.get("motion"), default_enter="fade"),
            "overlays": sc.get("overlays") if isinstance(sc.get("overlays"), list) else [],
            "sketches": sketches,
        }

    # sceneMap → 按 beat index 落实 scene；缺失沿用前值
    total = len(beats)
    raw_by_idx: dict[int, str] = {}
    for k, v in scene_map_in.items():
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        sid = str(v).strip()
        if sid:
            raw_by_idx[i] = sid

    scene_by_beat: dict[int, str] = {}
    prev = ""
    for i in range(1, total + 1):
        sid = raw_by_idx.get(i) or prev
        if not sid:
            # 首条都没给 → 用 scenes 里第一个
            sid = next(iter(scenes))
        # sceneMap 指向但 scenes 没定义的 → 补空场景，避免 image KeyError
        if sid not in scenes:
            scenes[sid] = {
                "prompt": "", "group": "", "label": "", "imageFit": "contain",
                "motion": {"enter": "fade"}, "overlays": [], "sketches": [],
            }
        scene_by_beat[i] = sid
        prev = sid

    return scene_by_beat, scenes
