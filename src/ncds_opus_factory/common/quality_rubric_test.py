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


# --- score 优雅降级(绝不抛异常) ---

def test_score_degrades_when_opus_missing(monkeypatch):
    monkeypatch.setattr(quality_rubric.shutil, "which", lambda _name: None)
    r = quality_rubric.score("一段稿子")
    assert r["available"] is False
    assert "skipped" in r


def test_score_degrades_on_judge_failure(monkeypatch):
    monkeypatch.setattr(quality_rubric, "available", lambda: True)

    def boom(*_a, **_k):
        raise RuntimeError("opus 炸了")

    monkeypatch.setattr(quality_rubric, "_call_opus_judge", boom)
    r = quality_rubric.score("一段稿子")
    assert r["available"] is False
    assert "失败" in r["skipped"]
