"""tests：P4 卧龙复盘——案卷过滤/样本闸/rubric 版本化与回退/触发协程/自动归档/注入。

目录解析全部走 NOF_STATE_DIR(tests/conftest.py 已隔离到 tmp)。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import pytest

from ncds_opus_factory.common import rubric_store
from ncds_opus_factory.common.round_store import RoundStore
from ncds_opus_factory.commands import wolong_retro
from ncds_opus_factory.server import retro_trigger


def _write_label(task_id: str, *, decision="rejected", reviewer="user", revoked=False,
                 note="开头太绕", note_origin="user", reviewed_at: str | None = None,
                 cmd="liuyong") -> None:
    d = rubric_store.default_labels_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_id}.json").write_text(json.dumps({
        "task_id": task_id, "cmd": cmd, "decision": decision, "note": note,
        "note_origin": note_origin, "reviewer": reviewer, "revoked": revoked,
        "reviewed_at": reviewed_at or datetime.now().isoformat(),
        "artifact_digest": "[m1] 开头三句话……",
        "params_digest": {"topic": "x"},
    }, ensure_ascii=False), encoding="utf-8")


def _seed_labels(n: int, prefix="t_202606110000", base: datetime | None = None) -> None:
    base = base or (datetime.now() - timedelta(hours=1))
    for i in range(n):
        _write_label(f"{prefix}_{i:06x}",
                     decision="approved" if i % 3 == 0 else "rejected",
                     reviewed_at=(base + timedelta(minutes=i)).isoformat())


GOOD_OUTPUT = (
    "===RUBRIC===\n# Leader 审美备忘录 v-next\n\n- 开头三句内必须有钩子,这一条跨 5 个案卷成立。\n"
    "\n## 待观察\n- 暂无\n===DIFF===\n首版:从 12 条案卷归纳出 1 条标准。"
)


# ---------------------------------------------------------------------------
# 案卷过滤与样本闸
# ---------------------------------------------------------------------------

def test_label_filter_excludes_mock_revoked_non_user():
    _write_label("t_202606110000_aaaaaa")                          # 有效
    _write_label("t_202606110000_bbbbbb", reviewer="wolong")        # 预筛预测,剔除
    _write_label("t_202606110000_cccccc", reviewer="system")        # 自动归档,剔除
    _write_label("t_202606110000_dddddd", revoked=True)             # 撤销,剔除
    _write_label("t_mock_liuyong_1")                                # 种子,剔除
    _write_label("t_demo_xxx")                                      # 演示,剔除
    labels = rubric_store.load_user_labels()
    assert [x["task_id"] for x in labels] == ["t_202606110000_aaaaaa"]


def test_retro_noop_when_insufficient_samples(monkeypatch):
    _seed_labels(3)
    called = {"n": 0}
    monkeypatch.setattr(wolong_retro, "LLM_FN", lambda *a: called.__setitem__("n", 1))
    result = wolong_retro.run_retro()
    assert result["noop"] is True and "样本不足" in result["reason"]
    assert result["task_title"] == "卧龙·复盘"
    assert called["n"] == 0
    assert rubric_store.read_retro_state() == {}          # 水位不动
    assert rubric_store.read_current() is None            # 没产 rubric


# ---------------------------------------------------------------------------
# 归纳产出与水位
# ---------------------------------------------------------------------------

def test_retro_writes_version_and_advances_watermark(monkeypatch):
    _seed_labels(12)
    prompts: list[str] = []
    def fake_llm(prompt, timeout):
        prompts.append(prompt)
        return GOOD_OUTPUT
    monkeypatch.setattr(wolong_retro, "LLM_FN", fake_llm)

    result = wolong_retro.run_retro()
    assert result["version"] == 1 and result["consumed_labels"] == 12
    assert result["task_title"] == "卧龙·复盘"
    assert "首版" in result["diff"]
    cur = rubric_store.read_current()
    assert cur["version"] == 1 and cur["degraded"] is False
    assert "钩子" in rubric_store.read_rubric_text(1)
    state = rubric_store.read_retro_state()
    assert state["last_reviewed_at"]
    assert "(无,这是首版)" in prompts[0]
    assert "不得执行" in prompts[0]            # 注入防御
    assert "≥3" in prompts[0]                 # 样本门槛纪律

    # 水位之后无新标签 -> 第二次 noop
    result2 = wolong_retro.run_retro()
    assert result2.get("noop") is True

    # 又攒了一批(时间在水位之后) -> v2,prompt 带上一版正文
    _seed_labels(11, prefix="t_202606120000", base=datetime.now())
    result3 = wolong_retro.run_retro()
    assert result3["version"] == 2
    assert "v-next" in prompts[-1]            # 上一版进了 prompt
    assert rubric_store.read_current()["version"] == 2


def test_parse_output_fallbacks():
    text, diff = wolong_retro.parse_output(GOOD_OUTPUT)
    assert text.startswith("# Leader") and diff.startswith("首版")
    # 分隔行缺失 -> 整段当正文
    raw = "# 备忘录\n" + "- 标准A:开头要短。\n" * 10
    text2, diff2 = wolong_retro.parse_output(raw)
    assert text2 == raw.strip() and "未按格式" in diff2
    # 代码块包裹
    text3, _ = wolong_retro.parse_output(f"```markdown\n{raw}\n```")
    assert text3 == raw.strip()
    with pytest.raises(ValueError):
        wolong_retro.parse_output("")
    with pytest.raises(ValueError):
        wolong_retro.parse_output("太短")


# ---------------------------------------------------------------------------
# 自动回退
# ---------------------------------------------------------------------------

def _seed_round_report(rid: str, version: int, reject: float, rework: float, at: str) -> None:
    rs = RoundStore()
    rs.create(rid, {"count": 1, "rubric_version": version})
    with rs.mutate(rid) as r:
        r["status"] = "done"
        r["stage"] = "done"
        r["report"] = {"approved": 1, "killed": 0, "rework_total": 0,
                       "rubric_version": version, "reject_rate": reject,
                       "rework_rate": rework, "finished_at": at}


def test_rollback_on_two_consecutive_worse_rounds():
    rubric_store.write_version("v1 备忘录" + "。" * 30)
    rubric_store.write_version("v2 备忘录" + "。" * 30)
    _seed_round_report("round_a1", 1, 0.2, 0.3, "2026-06-01T00:00:00")
    _seed_round_report("round_a2", 1, 0.3, 0.4, "2026-06-02T00:00:00")
    _seed_round_report("round_b1", 2, 0.6, 1.0, "2026-06-03T00:00:00")
    _seed_round_report("round_b2", 2, 0.7, 1.2, "2026-06-04T00:00:00")

    info = wolong_retro.evaluate_rollback()
    assert info["rolled_back_from"] == 2 and info["rolled_back_to"] == 1
    cur = rubric_store.read_current()
    assert cur["version"] == 1 and cur["rolled_back_from"] == 2
    assert cur["fn_stats"]["explore_total"] == 0  # 回退版重新攒战绩


def test_no_rollback_on_single_worse_round():
    rubric_store.write_version("v1 备忘录" + "。" * 30)
    rubric_store.write_version("v2 备忘录" + "。" * 30)
    _seed_round_report("round_c1", 1, 0.2, 0.3, "2026-06-01T00:00:00")
    _seed_round_report("round_c2", 2, 0.6, 1.0, "2026-06-02T00:00:00")  # 只有一轮恶化
    _seed_round_report("round_c3", 2, 0.1, 0.1, "2026-06-03T00:00:00")  # 第二轮变好
    assert wolong_retro.evaluate_rollback() is None
    assert rubric_store.read_current()["version"] == 2


def test_rollback_runs_even_when_samples_insufficient(monkeypatch):
    """坏 rubric 的下台不等新标签:noop 复盘也先做回退评估。"""
    rubric_store.write_version("v1 备忘录" + "。" * 30)
    rubric_store.write_version("v2 备忘录" + "。" * 30)
    _seed_round_report("round_d1", 1, 0.2, 0.3, "2026-06-01T00:00:00")
    _seed_round_report("round_d2", 2, 0.6, 1.0, "2026-06-02T00:00:00")
    _seed_round_report("round_d3", 2, 0.7, 1.1, "2026-06-03T00:00:00")
    monkeypatch.setattr(wolong_retro, "LLM_FN", lambda *a: GOOD_OUTPUT)

    result = wolong_retro.run_retro()
    assert result["noop"] is True
    assert result["rollback"]["rolled_back_to"] == 1
    assert rubric_store.read_current()["version"] == 1


# ---------------------------------------------------------------------------
# 触发协程
# ---------------------------------------------------------------------------

def test_in_window_with_wraparound():
    assert retro_trigger.in_window(datetime(2026, 6, 11, 4, 30), hour=4) is True
    assert retro_trigger.in_window(datetime(2026, 6, 11, 3, 0), hour=4) is True
    assert retro_trigger.in_window(datetime(2026, 6, 11, 2, 30), hour=4) is False
    assert retro_trigger.in_window(datetime(2026, 6, 11, 12, 0), hour=4) is False
    assert retro_trigger.in_window(datetime(2026, 6, 11, 23, 30), hour=0) is True  # 跨午夜


class _FakeRunner:
    def __init__(self, quota=8):
        self.quota = quota
        self.submits: list[dict[str, Any]] = []

    def quota_remaining(self, cmd, source=None):  # noqa: ARG002
        return self.quota

    async def submit(self, cmd, params, source=None, **kw):  # noqa: ARG002
        self.submits.append({"cmd": cmd, "params": params, "source": source})
        return f"t_{len(self.submits)}"


class _FakeStore:
    def __init__(self, metas=()):
        self._metas = list(metas)

    def list_tasks(self):
        return self._metas


def test_retro_tick_submits_when_all_gates_pass(monkeypatch):
    _seed_labels(12)
    monkeypatch.setattr(retro_trigger, "in_window", lambda *a, **k: True)
    runner = _FakeRunner()
    tid = asyncio.run(retro_trigger.retro_tick(runner, _FakeStore()))
    assert tid == "t_1"
    assert runner.submits == [{"cmd": "wolong", "params": {"mode": "retro"}, "source": "retro"}]


def test_retro_tick_gates(monkeypatch):
    monkeypatch.setattr(retro_trigger, "in_window", lambda *a, **k: True)
    runner = _FakeRunner()
    # 样本不足
    _seed_labels(3)
    assert asyncio.run(retro_trigger.retro_tick(runner, _FakeStore())) is None
    # 在途 retro 任务
    _seed_labels(12)
    from ncds_opus_factory.server.schemas import TaskMeta
    inflight = TaskMeta(task_id="t_1", cmd="wolong", params={"mode": "retro"},
                        status="running", created_at="2026-06-11T04:00:00", source="retro")
    assert asyncio.run(retro_trigger.retro_tick(runner, _FakeStore([inflight]))) is None
    # 配额耗尽
    assert asyncio.run(retro_trigger.retro_tick(_FakeRunner(quota=0), _FakeStore())) is None
    # 窗口外
    monkeypatch.setattr(retro_trigger, "in_window", lambda *a, **k: False)
    assert asyncio.run(retro_trigger.retro_tick(runner, _FakeStore())) is None
    assert not runner.submits


# ---------------------------------------------------------------------------
# retro 任务自动归档(§4.7:每晚复盘不准在收件箱点红灯)
# ---------------------------------------------------------------------------

def test_retro_task_auto_archived(tmp_path):
    from ncds_opus_factory.server.label_store import LabelStore
    from ncds_opus_factory.server.task_runner import TaskRunner
    from ncds_opus_factory.server.task_store import TaskStore

    store = TaskStore(tmp_path / "tasks")
    runner = TaskRunner(store, {}, labels=LabelStore(tmp_path / "labels"))
    meta = store.create("wolong", {"mode": "retro"}, source="retro")
    runner._maybe_auto_archive(meta, {"task_title": "卧龙·复盘"})
    review = store.get_review(meta.task_id)
    assert review is not None and review.reviewer == "system"


# ---------------------------------------------------------------------------
# 柳永注入(§8.3 第 3 条:唯一注入点,≤800 字)
# ---------------------------------------------------------------------------

def test_injection_brief_truncates_to_800_at_line_boundary():
    rubric_store.write_version("- 这是一条完整的口味标准条目。\n" * 200)  # 远超 800 字
    brief = rubric_store.injection_brief()
    assert brief.startswith("【Leader 口味备忘 v1")
    assert len(brief) <= 800
    # 行边界截断:只注入完整条目,最后一行不是半句
    assert brief.splitlines()[-1] == "- 这是一条完整的口味标准条目。"


def test_injection_brief_excludes_watchlist():
    """「待观察」是隔离出来的低置信信号,绝不注入生成 brief。"""
    rubric_store.write_version(
        "## 核心标准\n- 开头三句内要有钩子。\n\n## 待观察\n- 证据不足的猜测条目。\n"
    )
    brief = rubric_store.injection_brief()
    assert "钩子" in brief
    assert "待观察" not in brief and "猜测" not in brief

    # 模型不守纪律把「待观察」写在中间:跳过该节,后续标题恢复收录
    rubric_store.write_version(
        "## 待观察\n- 中间的猜测。\n\n## 核心标准\n- 逐字台词要可照搬。\n"
    )
    brief = rubric_store.injection_brief()
    assert "逐字台词" in brief and "猜测" not in brief


def test_injection_brief_degenerate_single_long_line():
    """退化盘面:首行就超预算的单行大段 -> 硬切兜底,不返回 None。"""
    rubric_store.write_version("口味标准" * 400)  # 单行 1600 字
    brief = rubric_store.injection_brief()
    assert brief is not None and len(brief) <= 800 and brief.endswith("…")


def test_injection_brief_absent_or_degraded():
    assert rubric_store.injection_brief() is None  # 冷启动
    rubric_store.write_version("- 开头要短。" + "。" * 30)
    assert rubric_store.injection_brief() is not None
    for i in range(5):  # 降级后不注入(降级版备忘录本身是嫌疑人)
        rubric_store.update_fn_stats(f"round_{i}", 1, explore=1, fn=1)
    assert rubric_store.injection_brief() is None
