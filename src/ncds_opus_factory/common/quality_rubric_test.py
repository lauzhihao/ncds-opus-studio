"""quality_rubric 纯函数契约测试。

judge 是外部 opus,单测不真调——只测 prompt 构造 / JSON 解析 / 阈值 / 优雅降级。
"""
from __future__ import annotations

import json

import pytest

from ncds_opus_factory.common import quality_rubric

VALID = {
    "dims": {"节奏": 8, "真实性": 7, "精炼度": 6, "直接性": 7, "信任度": 8},
    "issues": ["第2段全是长句缺顿挫", "收尾金句偏空泛"],
}


# --- parse_rubric_output 容错 ---

def test_parse_plain_json():
    r = quality_rubric.parse_rubric_output(json.dumps(VALID, ensure_ascii=False))
    assert r["dims"]["节奏"] == 8
    assert r["total"] == 36  # 8+7+6+7+8
    assert r["issues"] == VALID["issues"]


def test_parse_fenced_json():
    raw = "好的,评分如下:\n```json\n" + json.dumps(VALID, ensure_ascii=False) + "\n```\n希望有用"
    assert quality_rubric.parse_rubric_output(raw)["total"] == 36


def test_parse_prose_around():
    raw = "我觉得节奏一般。" + json.dumps(VALID, ensure_ascii=False) + " 以上。"
    assert quality_rubric.parse_rubric_output(raw)["dims"]["信任度"] == 8


def test_parse_total_recomputed_not_trusting_model():
    # opus 给了错的 total,以 dims 之和为准
    bad = dict(VALID, total=999)
    assert quality_rubric.parse_rubric_output(json.dumps(bad, ensure_ascii=False))["total"] == 36


def test_parse_missing_dim_raises():
    bad = {"dims": {"节奏": 8}, "issues": []}
    with pytest.raises(ValueError):
        quality_rubric.parse_rubric_output(json.dumps(bad, ensure_ascii=False))


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        quality_rubric.parse_rubric_output("   ")


# --- grade 阈值 ---

@pytest.mark.parametrize(
    "total,grade",
    [(50, "优秀"), (47, "优秀"), (45, "优秀"), (44, "良好"), (35, "良好"), (34, "需重修"), (10, "需重修")],
)
def test_grade_thresholds(total, grade):
    assert quality_rubric.grade_of(total) == grade


# --- build_rubric_prompt 护栏(防误删) ---

def test_prompt_contains_guardrails():
    p = quality_rubric.build_rubric_prompt("随便一段稿子")
    for kw in ["节奏", "金句", "逐字台词", "1500", "不许扣分", "只打分"]:
        assert kw in p, f"护栏关键词丢失: {kw}"


def test_prompt_embeds_text():
    assert "我的独特待评稿XYZ" in quality_rubric.build_rubric_prompt("我的独特待评稿XYZ")


# --- judge 可用性检查 ---

def test_check_judge_available_uses_cli_detection(monkeypatch):
    """codex/agy 分支会走 shutil.which；deepseek 兼容旧 ds 别名。"""
    seen: list[str] = []

    def fake_which(name: str) -> str | None:
        seen.append(name)
        return f"/bin/{name}" if name == "scodex" else None

    monkeypatch.setattr(quality_rubric, "is_opus_available", lambda: False)
    monkeypatch.setattr(quality_rubric.shutil, "which", fake_which)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    assert quality_rubric._check_judge_available("opus") is False
    assert quality_rubric._check_judge_available("codex") is True
    assert quality_rubric._check_judge_available("agy") is False
    assert quality_rubric._check_judge_available("deepseek") is True
    assert quality_rubric._check_judge_available("ds") is True
    assert seen == ["scodex", "agy"]


# --- score 优雅降级(绝不抛异常) ---

def test_score_degrades_when_no_judge_available(monkeypatch):
    monkeypatch.setattr(quality_rubric, "_check_judge_available", lambda _mid: False)
    r = quality_rubric.score("一段稿子")
    assert r["available"] is False
    assert "skipped" in r


def test_score_degrades_on_all_judges_fail(monkeypatch):
    def fake_available(mid: str) -> bool:
        return mid in ("opus", "codex", "agy", "ds")
    monkeypatch.setattr(quality_rubric, "_check_judge_available", fake_available)

    def boom(*_a, **_k):
        raise RuntimeError("judge 炸了")

    monkeypatch.setattr(quality_rubric, "_call_judge", boom)
    r = quality_rubric.score("一段稿子")
    assert r["available"] is False
    assert "失败" in r["skipped"]


def test_score_uses_first_available_judge(monkeypatch):
    """验证 score 按优先级使用第一个可用 judge。"""
    called: list[str] = []

    def fake_available(mid: str) -> bool:
        return mid in ("opus", "deepseek")  # opus 可用, codex/agy 不可用
    monkeypatch.setattr(quality_rubric, "_check_judge_available", fake_available)

    def tracking_call(prompt: str, model_id: str, timeout: int) -> str:
        called.append(model_id)
        if model_id == "opus":
            raise RuntimeError("opus failed")
        # deepseek 返回 mock JSON
        return '{"dims":{"节奏":8,"真实性":7,"精炼度":6,"直接性":7,"信任度":8},"issues":[]}'

    monkeypatch.setattr(quality_rubric, "_call_judge", tracking_call)

    r = quality_rubric.score("一段稿子", avoid_models=set())
    assert r["available"] is True
    assert r["judge_model"] == "deepseek"
    assert called == ["opus", "deepseek"]


def test_refine_prefers_deepseek_model_id(monkeypatch):
    """RW 抽屉传入 deepseek 时，refine 应优先使用 deepseek judge。"""
    called: list[str] = []

    def fake_available(mid: str) -> bool:
        return mid == "deepseek"

    def tracking_call(prompt: str, model_id: str, timeout: int) -> str:
        called.append(model_id)
        return "优化后的完整口播稿。" * 30

    monkeypatch.setattr(quality_rubric, "_check_judge_available", fake_available)
    monkeypatch.setattr(quality_rubric, "_call_judge", tracking_call)

    out = quality_rubric.refine("原稿正文。" * 30, ["第 2 段节奏偏平"], prefer_models={"deepseek"})
    assert out
    assert called == ["deepseek"]
