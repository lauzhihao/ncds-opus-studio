"""ai_taste.scan 的行为契约测试。

重点验证摘自 Humanizer-zh 的新增词表:
- high 规则命中 -> verdict fail(打回重写)
- mid 规则(模糊归因)命中 -> 记录但不一票否决(verdict 仍可 pass)
- 正常口播稿(含金句/低密度反差钩子/口语台词)不被误伤 -> verdict pass
"""
from __future__ import annotations

from ncds_opus_factory.common import ai_taste
from ncds_opus_factory.common.ai_taste import scan


def _rules(report: dict, key: str) -> set[str]:
    return {h["rule"] for h in report[key]}


# --- 新增 high 词表:命中即 fail ---

def test_ai_shumian_ci_fails():
    r = scan("今天我们深入探讨一个被很多人忽略的问题。")
    assert r["verdict"] == "fail"
    assert "AI书面词" in _rules(r, "hard")


def test_shengwei_qiang_extension_fails():
    # "至关重要" 属扩充后的升维腔
    r = scan("管理好你的时间至关重要，这是大多数人翻车的地方。")
    assert r["verdict"] == "fail"
    assert "升维腔" in _rules(r, "hard")


def test_taolu_lianjieci_extension_fails():
    # "此外，" 属扩充后的套路连接词
    r = scan("第一步先观察。此外，你还要记录自己的情绪。")
    assert r["verdict"] == "fail"
    assert "套路连接词" in _rules(r, "hard")


def test_duihua_qiang_canliu_fails():
    # codex 把 chatbot 口吻漏进脚本
    r = scan("作为一个AI助手，我希望这对你有帮助。")
    assert r["verdict"] == "fail"
    assert "对话腔残留" in _rules(r, "hard")


# --- 模糊归因是 mid:记录但不打回 ---

def test_mohu_guiyin_is_mid_not_fail():
    r = scan("研究表明，早睡的人第二天精力更好。")
    assert r["verdict"] == "pass"  # mid 不一票否决
    assert "模糊归因" in _rules(r, "hard")
    assert all(h["severity"] == "mid" for h in r["hard"])


# --- 不误伤:正常口播稿应 pass ---

def test_normal_koubo_passes():
    # 有金句、有招式、含 1 次低密度"不是X而是Y"反差钩子、含口播常用语"你说得对"
    text = (
        "同事阴阳你的时候，别急着解释。你越解释，他越觉得你心虚。"
        "记住：不接招，才是最好的接招。"
        "下次他再阴阳，你就笑一下说你说得对，然后该干嘛干嘛。"
        "他要的不是真相，而是看你炸毛，你偏不给。"
    )
    r = scan(text)
    assert r["verdict"] == "pass", f"正常口播稿被误伤: {r['summary']} / {r['hard']}"


# --- 回归:既有行为不变 ---

def test_legacy_connective_still_fails():
    r = scan("然而，事情远没有那么简单。")
    assert r["verdict"] == "fail"
    assert "套路连接词" in _rules(r, "hard")


def test_legacy_density_still_fails():
    # "不是X而是Y" 出现 3 次,达密度阈值
    text = "他不是不努力，而是方法错了；你不是不行，而是没开窍；问题不是能力，而是习惯。"
    r = scan(text)
    assert r["verdict"] == "fail"
    assert any(h["rule"] == "不是X而是Y" for h in r["density"])


def test_build_purge_prompt_includes_density_and_hard_hits():
    report = {
        "density": [{"rule": "不是X而是Y", "count": 3, "samples": ["不是A而是B"]}],
        "hard": [{"rule": "套路连接词", "samples": ["然而，"]}],
    }
    prompt = ai_taste.build_purge_prompt("原稿正文", report)
    assert "必须消除的句式" in prompt
    assert "不是X而是Y" in prompt
    assert "套路连接词" in prompt
    assert "原稿正文" in prompt


def test_purge_ai_taste_delegates_to_model_call():
    captured: dict = {}

    def fake_model(prompt: str, **kwargs) -> str:
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return "  改写后正文  "

    out = ai_taste.purge_ai_taste(
        "原稿",
        {"density": ["套话"], "hard": []},
        timeout_seconds=12,
        env={"X": "Y"},
        model_call=fake_model,
    )
    assert out == "改写后正文"
    assert "原稿" in captured["prompt"]
    assert captured["kwargs"]["system_prompt"] == ai_taste.DEFAULT_PURGE_SYSTEM_PROMPT
    assert captured["kwargs"]["timeout_seconds"] == 12
    assert captured["kwargs"]["env"] == {"X": "Y"}
