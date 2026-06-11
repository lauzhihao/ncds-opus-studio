"""卧龙的学习记忆层（docs/WOLONG-DESIGN.md §5.2/§5.3、§8.3）：
learned rubric 版本库 + 案卷读取 + 复盘水位。

与 quality_rubric.py 严格区分：那是**静态体裁评分**（50 分制工艺打分,仅标注）;
这里存的是**复盘产出的 Leader 审美备忘录**（learned rubric）——文件、用途、
生命周期都不同。三个读方（预筛/柳永注入/iOS 战报）统一经 current.json 指针解析。

目录（与 server.state 的 NOF_STATE_DIR 约定一致,state/tasks 的兄弟目录）：

    state/wolong/rubric/v{n}.md      # 备忘录正文,只增不删（回退要靠旧版在）
    state/wolong/rubric/current.json # 指针 {version, path, degraded, fn_stats, ...}
    state/wolong/labels/{task_id}.json  # 案卷（server.label_store 写,这里只读）
    state/wolong/retro_state.json    # 复盘水位 {last_reviewed_at}

本模块在 common 层：commands（复盘/预筛/柳永注入）与 server（触发协程）都要读,
而 commands 禁止 import server。写入方只有卧龙段（wolong 并发硬钳 1,天然串行）,
读方跨线程,所以写一律 tmp+os.replace 原子替换——读到旧版可以,读到撕裂不行。
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]

# 预筛降级口径（§5.3/§8.3）：最近 FN_WINDOW 个有探索样本的 round 里,
# 累计探索样本 ≥ FN_MIN_EXPLORE 才评估;假阴性率 > FN_DEGRADE_RATE → degraded
FN_WINDOW = 5
FN_MIN_EXPLORE = 5
FN_DEGRADE_RATE = 0.30


def _state_root() -> Path:
    """NOF_STATE_DIR 指向 state/tasks,卧龙记忆在兄弟目录 state/wolong/。"""
    sd = os.environ.get("NOF_STATE_DIR")
    if sd:
        return Path(sd).parent
    return _ROOT / "state"


def default_labels_dir() -> Path:
    """与 server.state.LABELS_DIR 同一套解析（NOF_LABELS_DIR 可覆盖）。"""
    explicit = os.environ.get("NOF_LABELS_DIR")
    if explicit:
        return Path(explicit)
    return _state_root() / "wolong" / "labels"


def default_rubric_dir() -> Path:
    return _state_root() / "wolong" / "rubric"


def default_retro_state_path() -> Path:
    return _state_root() / "wolong" / "retro_state.json"


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 案卷读取（复盘的训练数据;只读,写入方是 server.label_store）
# ---------------------------------------------------------------------------
_EXCLUDED_TASK_PREFIXES = ("t_mock_", "t_demo_")


def load_user_labels(base_dir: Path | None = None) -> list[dict[str, Any]]:
    """读全部有效标注样本：reviewer=user、非 revoked、剔除种子/演示任务。

    按 reviewed_at 升序。machine/inferred 备注的样本**也返回**（decision 本身
    是有效标注）,note 的证据权重由复盘 prompt 按 note_origin 区分。
    """
    d = Path(base_dir) if base_dir else default_labels_dir()
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for f in d.glob("*.json"):
        if f.name.startswith(_EXCLUDED_TASK_PREFIXES):
            continue
        try:
            label = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("[rubric] 跳过不可读案卷: %s", f.name)
            continue
        if not isinstance(label, dict):
            continue
        if label.get("reviewer") != "user" or label.get("revoked"):
            continue
        if str(label.get("task_id", "")).startswith(_EXCLUDED_TASK_PREFIXES):
            continue
        out.append(label)
    out.sort(key=lambda x: str(x.get("reviewed_at") or ""))
    return out


def count_new_user_labels(since_iso: str, base_dir: Path | None = None) -> int:
    """水位之后新增的有效标注数（触发协程与复盘段共用同一口径）。

    改判会刷新 reviewed_at,自然重入——"改判按最终判定算"（§5.2）。
    """
    return sum(
        1 for label in load_user_labels(base_dir)
        if str(label.get("reviewed_at") or "") > (since_iso or "")
    )


# ---------------------------------------------------------------------------
# 复盘水位
# ---------------------------------------------------------------------------
def read_retro_state(path: Path | None = None) -> dict[str, Any]:
    p = path or default_retro_state_path()
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, OSError):
        logger.warning("[rubric] 复盘水位不可读,按空处理: %s", p)
        return {}


def write_retro_state(state: dict[str, Any], path: Path | None = None) -> None:
    _atomic_write_json(path or default_retro_state_path(), state)


# ---------------------------------------------------------------------------
# rubric 版本库 + current.json 指针
# ---------------------------------------------------------------------------
def read_current(base_dir: Path | None = None) -> dict[str, Any] | None:
    """读 current.json 指针。缺失/损坏/指向的版本文件不存在 → None（冷启动语义）。"""
    d = Path(base_dir) if base_dir else default_rubric_dir()
    p = d / "current.json"
    if not p.exists():
        return None
    try:
        cur = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("[rubric] current.json 不可读: %s", p)
        return None
    if not isinstance(cur, dict) or not isinstance(cur.get("version"), int):
        return None
    if not (d / str(cur.get("path") or f"v{cur['version']}.md")).exists():
        logger.warning("[rubric] 指针指向的版本文件不存在: %s", cur.get("path"))
        return None
    return cur


def current_version(base_dir: Path | None = None) -> int | None:
    cur = read_current(base_dir)
    return cur["version"] if cur else None


def read_rubric_text(version: int, base_dir: Path | None = None) -> str | None:
    d = Path(base_dir) if base_dir else default_rubric_dir()
    p = d / f"v{version}.md"
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def active_rubric(base_dir: Path | None = None) -> tuple[int, str] | None:
    """预筛/柳永注入用的"可用 rubric"：缺失或 degraded 一律 None（放行/不注入）。"""
    cur = read_current(base_dir)
    if cur is None or cur.get("degraded"):
        return None
    text = read_rubric_text(cur["version"], base_dir)
    if not text or not text.strip():
        return None
    return cur["version"], text


def _empty_fn_stats() -> dict[str, Any]:
    return {"rounds": [], "explore_total": 0, "fn_total": 0, "fn_rate": None}


def write_version(text: str, base_dir: Path | None = None) -> int:
    """落一个新版本并把指针切过去。版本号 = 盘上已有最大版 +1（回退后再产新版
    不会覆盖被隔离的坏版本）。新版本重置 degraded/fn_stats——预测表现要重新攒。
    """
    if not text or len(text.strip()) < 20:
        raise ValueError("rubric 正文过短,拒绝落盘")
    d = Path(base_dir) if base_dir else default_rubric_dir()
    d.mkdir(parents=True, exist_ok=True)
    existing = []
    for f in d.glob("v*.md"):
        try:
            existing.append(int(f.stem[1:]))
        except ValueError:
            continue
    n = (max(existing) if existing else 0) + 1
    (d / f"v{n}.md").write_text(text, encoding="utf-8")
    _atomic_write_json(d / "current.json", {
        "version": n,
        "path": f"v{n}.md",
        "degraded": False,
        "updated_at": datetime.now().isoformat(),
        "fn_stats": _empty_fn_stats(),
    })
    return n


def rollback(base_dir: Path | None = None) -> int | None:
    """指针回退到当前版之前最近的存在版本。无处可退返回 None。

    回退版重置 degraded/fn_stats（它当年的表现不必背新版的账）;
    rolled_back_from 记录被停用的版本,战报/iOS 可见。
    """
    d = Path(base_dir) if base_dir else default_rubric_dir()
    cur = read_current(d)
    if cur is None:
        return None
    candidates = []
    for f in d.glob("v*.md"):
        try:
            v = int(f.stem[1:])
        except ValueError:
            continue
        if v < cur["version"]:
            candidates.append(v)
    if not candidates:
        return None
    target = max(candidates)
    _atomic_write_json(d / "current.json", {
        "version": target,
        "path": f"v{target}.md",
        "degraded": False,
        "updated_at": datetime.now().isoformat(),
        "fn_stats": _empty_fn_stats(),
        "rolled_back_from": cur["version"],
    })
    logger.warning("[rubric] 指针回退: v%d -> v%d", cur["version"], target)
    return target


def update_fn_stats(
    round_id: str,
    rubric_version: int,
    explore: int,
    fn: int,
    base_dir: Path | None = None,
) -> dict[str, Any] | None:
    """round 收盘时回写探索样本战绩,评估预筛降级（§8.3 第 2 条）。

    只记当前指针版本的账（旧版的预测不指控新版）;无探索样本的 round 不占窗口。
    窗口内累计探索 ≥ FN_MIN_EXPLORE 且假阴性率 > FN_DEGRADE_RATE → degraded=true
    （只警告不拦截：预筛随之整体放行,直到复盘产新版重置）。
    """
    if explore <= 0:
        return None
    d = Path(base_dir) if base_dir else default_rubric_dir()
    cur = read_current(d)
    if cur is None or cur.get("version") != rubric_version:
        logger.info("[rubric] fn_stats 跳过: round=%s 的 rubric v%s 已非当前版", round_id, rubric_version)
        return None
    stats = cur.get("fn_stats") if isinstance(cur.get("fn_stats"), dict) else _empty_fn_stats()
    rounds = [r for r in stats.get("rounds", []) if isinstance(r, dict)]
    rounds = [r for r in rounds if r.get("round_id") != round_id]  # 幂等:重复收盘覆盖
    rounds.append({
        "round_id": round_id, "version": rubric_version,
        "explore": int(explore), "fn": int(fn),
        "at": datetime.now().isoformat(),
    })
    rounds = rounds[-FN_WINDOW:]
    explore_total = sum(int(r.get("explore", 0)) for r in rounds)
    fn_total = sum(int(r.get("fn", 0)) for r in rounds)
    fn_rate = (fn_total / explore_total) if explore_total else None
    cur["fn_stats"] = {
        "rounds": rounds,
        "explore_total": explore_total,
        "fn_total": fn_total,
        "fn_rate": fn_rate,
    }
    if explore_total >= FN_MIN_EXPLORE and fn_rate is not None and fn_rate > FN_DEGRADE_RATE:
        if not cur.get("degraded"):
            logger.warning(
                "[rubric] 预筛假阴性率 %.0f%% 超阈(>%.0f%%),v%d 降级为只警告不拦截",
                fn_rate * 100, FN_DEGRADE_RATE * 100, rubric_version,
            )
        cur["degraded"] = True
    cur["updated_at"] = datetime.now().isoformat()
    _atomic_write_json(d / "current.json", cur)
    return cur["fn_stats"]


# ---------------------------------------------------------------------------
# 柳永生成侧注入（§8.3 第 3 条:learned rubric 的唯一注入点）
# ---------------------------------------------------------------------------
_BRIEF_MAX_CHARS = 800

# 「待观察」小节标题行:低置信/矛盾信号只服务复盘与人审,绝不注入生成 brief——
# 防噪声机制隔离出来的可疑信号,不能在注入环节又被端回去
_WATCHLIST_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s*|\*{2}\s*|【)?\s*待观察")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")


def _strip_watchlist(text: str) -> str:
    """剔除「待观察」小节(从其标题行起,到下一个 markdown 标题或文末)。"""
    out: list[str] = []
    skipping = False
    for line in text.splitlines():
        if _WATCHLIST_RE.match(line):
            skipping = True
            continue
        if skipping and _HEADING_RE.match(line) and not _WATCHLIST_RE.match(line):
            skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out).strip()


def injection_brief(max_chars: int = _BRIEF_MAX_CHARS, base_dir: Path | None = None) -> str | None:
    """给柳永 userRequirements 附加的 Leader 口味摘要（≤max_chars,含标头）。

    rubric 缺失/degraded → None（不注入,宁缺毋滥——降级版备忘录本身是嫌疑人）。
    「待观察」小节剔除;超长时按**行边界**截断,只注入完整条目不切半句
    （复盘 prompt 要求核心标准按支撑样本数排序,所以截掉的是置信度最低的尾部）。
    """
    active = active_rubric(base_dir)
    if active is None:
        return None
    version, text = active
    header = f"【Leader 口味备忘 v{version}（卧龙复盘归纳,按此口味创作）】\n"
    body_budget = max(0, max_chars - len(header))
    kept: list[str] = []
    used = 0
    body_full = _strip_watchlist(text)
    for line in body_full.splitlines():
        cost = len(line) + (1 if kept else 0)
        if used + cost > body_budget:
            break
        kept.append(line)
        used += cost
    body = "\n".join(kept).strip()
    if not body:
        # 退化盘面(首行就超预算的单行大段):硬切兜底,聊胜于无
        body = body_full[: max(0, body_budget - 1)].strip()
        if not body:
            return None
        body += "…"
    return header + body
