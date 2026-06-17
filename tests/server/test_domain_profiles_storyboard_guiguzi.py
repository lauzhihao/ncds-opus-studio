"""task-2.5 接线测试：domain 注入到生图（storyboard）和选题（鬼谷子）两个阶段。

测试策略：
  - 全程 monkeypatch stub domain_profiles.get_profile，不依赖真实 profile 内容。
  - 不调真实 opus/deepseek，不写磁盘副作用（除 storyboard 需要 episode.json 的情况）。
  - 双向验证：有 domain → prompt 含注入段；无/未知 domain → 与原通用 prompt 完全一致。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# 公共 stub
# ---------------------------------------------------------------------------
_FAKE_FINANCE_PROFILE = {
    "guiguzi": "[FINANCE_TOPIC_STUB] 受众：关注财经的男性用户",
    "liuyong": "[FINANCE_DRAFT_STUB]",
    "wudaozi": "[FINANCE_IMAGE_STUB] 冷色调金融符号风格",
    "cover": "[FINANCE_COVER_STUB] 封面 CTR<=15%",
    "boya": None,
}

_FAKE_EMOTION_PROFILE = {
    "guiguzi": "[EMOTION_TOPIC_STUB] 受众：共情型女性",
    "liuyong": None,
    "wudaozi": "[EMOTION_IMAGE_STUB] 暖色温情调色",
    "cover": "[EMOTION_COVER_STUB] 温柔共鸣封面",
    "boya": None,
}


def _stub_get_profile(domain: str | None):
    """stub domain_profiles.get_profile：finance/emotion 返回假 profile，其余返回 None。"""
    if domain == "finance":
        return _FAKE_FINANCE_PROFILE
    if domain == "emotion":
        return _FAKE_EMOTION_PROFILE
    return None


def _noop(_: str) -> None:
    return None


# ===========================================================================
# Part 1：storyboard_director.build_director_prompt domain 注入
# ===========================================================================
class TestBuildDirectorPromptDomainInjection:
    """验证 build_director_prompt 对 domain_image_style 参数的注入行为。"""

    def _call(self, **kwargs: Any) -> tuple[str, str]:
        from ncds_opus_factory.server import storyboard_director
        meta = {"title": "测试"}
        beats = [{"index": 1, "zh": "第一句", "en": "line one"}]
        return storyboard_director.build_director_prompt(
            meta, beats,
            style_bible=storyboard_director.DEFAULT_SKETCH_STYLE_PREFIX,
            **kwargs,
        )

    def test_no_domain_no_image_style_segment(self):
        """无 domain_image_style → user_prompt 不含领域视觉段。"""
        _, user = self._call()
        assert "领域视觉调性" not in user

    def test_domain_image_style_injected_in_user_prompt(self):
        """传入 domain_image_style → user_prompt 含注入段，system_prompt 不变。"""
        sys_p, user = self._call(domain_image_style="冷色金融风")
        assert "冷色金融风" in user
        assert "领域视觉调性" in user
        # system_prompt 不应被修改（它是固定的极简导演人格）
        assert "领域视觉调性" not in sys_p

    def test_domain_image_style_does_not_replace_style_bible(self):
        """领域视觉调性应叠加在 style_bible 之外，不替换圣经本体。"""
        _, user = self._call(domain_image_style="暖调情感风格")
        # 圣经关键词仍在
        assert "Minimalist pictogram" in user
        # 领域调性也在
        assert "暖调情感风格" in user

    def test_empty_domain_image_style_no_segment(self):
        """空字符串 domain_image_style → 不注入（等同于无 domain）。"""
        _, user = self._call(domain_image_style="")
        assert "领域视觉调性" not in user

    def test_none_domain_image_style_no_segment(self):
        """None domain_image_style → 不注入。"""
        _, user = self._call(domain_image_style=None)
        assert "领域视觉调性" not in user


# ===========================================================================
# Part 2：run_storyboard_step domain 参数透传
# ===========================================================================
class TestRunStoryboardStepDomainInjection:
    """验证 run_storyboard_step 从 kwargs.domain 读取后注入 build_director_prompt。"""

    def _write_episode(self, jd: Path) -> None:
        ep = {
            "meta": {"title": "T"}, "image": {}, "visual": {},
            "beats": [
                {"zh": "句一", "en": "", "scene": ""},
                {"zh": "句二", "en": "", "scene": ""},
            ],
            "scenes": {},
        }
        (jd / "02_rw").mkdir(parents=True, exist_ok=True)
        (jd / "02_rw" / "episode.json").write_text(
            json.dumps(ep, ensure_ascii=False), encoding="utf-8"
        )

    def _fake_opus_director(self, captured_prompts: list[str]):
        """捕获 user_prompt 以便断言 domain 注入，同时返回合法 director JSON。"""
        director_json = json.dumps({
            "scenes": {
                "s1": {"prompt": "x", "group": "g1", "imageFit": "contain",
                       "motion": {"enter": "fade"}, "overlays": [], "sketches": []},
            },
            "sceneMap": {"1": "s1", "2": "s1"},
        }, ensure_ascii=False)

        def _opus(user_prompt: str, system_prompt: str, model_id: str = "") -> str:
            captured_prompts.append(user_prompt)
            return director_json
        return _opus

    def test_domain_finance_injects_image_style(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """domain=finance 且 profile.image_style 非空 → user_prompt 含 image_style 内容。"""
        from ncds_opus_factory.server.engine import pipeline_performers_015 as perf
        from ncds_opus_factory.server import domain_profiles as dp_mod

        monkeypatch.setattr(dp_mod, "get_profile", _stub_get_profile)
        monkeypatch.setattr(perf, "_get_domain_profile", _stub_get_profile)

        captured: list[str] = []
        monkeypatch.setattr(perf, "_opus_structure", self._fake_opus_director(captured))

        jd = tmp_path / "job"
        self._write_episode(jd)
        perf.run_storyboard_step(_noop, job_dir=str(jd), domain="finance")

        assert captured, "opus 应被调用一次"
        assert "[FINANCE_IMAGE_STUB]" in captured[0]
        assert "领域视觉调性" in captured[0]

    def test_no_domain_no_image_style_segment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """domain 为 None → user_prompt 不含领域视觉段（回退原行为）。"""
        from ncds_opus_factory.server.engine import pipeline_performers_015 as perf
        from ncds_opus_factory.server import domain_profiles as dp_mod

        monkeypatch.setattr(dp_mod, "get_profile", _stub_get_profile)
        monkeypatch.setattr(perf, "_get_domain_profile", _stub_get_profile)

        captured: list[str] = []
        monkeypatch.setattr(perf, "_opus_structure", self._fake_opus_director(captured))

        jd = tmp_path / "job"
        self._write_episode(jd)
        perf.run_storyboard_step(_noop, job_dir=str(jd))

        assert captured
        assert "领域视觉调性" not in captured[0]

    def test_unknown_domain_no_injection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """未知 domain → get_profile 返回 None → 无注入，回退默认行为。"""
        from ncds_opus_factory.server.engine import pipeline_performers_015 as perf

        monkeypatch.setattr(perf, "_get_domain_profile", _stub_get_profile)

        captured: list[str] = []
        monkeypatch.setattr(perf, "_opus_structure", self._fake_opus_director(captured))

        jd = tmp_path / "job"
        self._write_episode(jd)
        perf.run_storyboard_step(_noop, job_dir=str(jd), domain="unknown_xyz")

        assert captured
        assert "领域视觉调性" not in captured[0]


# ===========================================================================
# Part 3：guiguzi._build_analyze_prompt domain_guidance 注入
# ===========================================================================
class TestBuildAnalyzePromptDomainGuidance:
    """验证 _build_analyze_prompt 对 domain_guidance 的注入行为。"""

    _ITEMS = [{"text": "对标原文", "comment": "很好看"}]

    def _call(self, domain_guidance: str | None = None) -> str:
        from ncds_opus_factory.commands.guiguzi import _build_analyze_prompt
        return _build_analyze_prompt(self._ITEMS, domain_guidance=domain_guidance)

    def test_no_guidance_no_segment(self):
        """无 domain_guidance → prompt 不含领域软背景段。"""
        prompt = self._call()
        assert "领域软背景" not in prompt

    def test_guidance_appended_with_non_formula_label(self):
        """有 domain_guidance → prompt 末尾含软背景段，且明确注明'仅供参考，不要套成固定赛道'。"""
        prompt = self._call(domain_guidance="财经受众：关注宏观经济的男性")
        assert "财经受众：关注宏观经济的男性" in prompt
        assert "领域软背景" in prompt
        # 保护鬼谷子设计哲学：必须有"仅供参考"防止模型固化赛道
        assert "仅供参考" in prompt

    def test_core_task_not_changed(self):
        """domain_guidance 不改变核心分析任务（'反推…为什么成为爆款'仍在）。"""
        prompt = self._call(domain_guidance="[SOME_DOMAIN]")
        assert "反推" in prompt
        assert "爆款" in prompt

    def test_empty_guidance_no_segment(self):
        """空字符串 domain_guidance → 不注入。"""
        prompt = self._call(domain_guidance="")
        assert "领域软背景" not in prompt


# ===========================================================================
# Part 4：guiguzi._build_topic_prompt_template domain_guidance 注入
# ===========================================================================
class TestBuildTopicPromptTemplateDomainGuidance:
    """验证 _build_topic_prompt_template 对 domain_guidance 的注入行为。"""

    _ITEMS = [{"text": "原文", "comment": "评论A"}]
    _ANALYSIS = {
        "hook_reason": "原因", "audience": "受众", "hooks": ["钩子1"], "direction": "方向",
    }

    def _call(self, domain_guidance: str | None = None) -> str:
        from ncds_opus_factory.commands.guiguzi import _build_topic_prompt_template
        return _build_topic_prompt_template(self._ITEMS, self._ANALYSIS, domain_guidance=domain_guidance)

    def test_no_guidance_no_segment(self):
        """无 domain_guidance → prompt 模板不含领域软背景段。"""
        template = self._call()
        assert "领域软背景" not in template

    def test_guidance_appended_soft_background(self):
        """有 domain_guidance → 模板末尾含软背景段，明确标注'仅供参考'。"""
        template = self._call(domain_guidance="情感受众：共情型用户")
        assert "情感受众：共情型用户" in template
        assert "领域软背景" in template
        assert "仅供参考" in template

    def test_core_task_instructions_preserved(self):
        """注入不改变核心选题任务（'锚定'/'每一条评论'仍在）。"""
        template = self._call(domain_guidance="[DOM]")
        assert "每一条评论" in template

    def test_no_comment_mode_guidance_appended(self):
        """无评论模式下 domain_guidance 同样追加。"""
        from ncds_opus_factory.commands.guiguzi import _build_topic_prompt_template
        items_no_comment = [{"text": "原文A", "comment": ""}]
        template = _build_topic_prompt_template(
            items_no_comment, self._ANALYSIS, domain_guidance="无评论领域背景"
        )
        assert "无评论领域背景" in template
        assert "领域软背景" in template


# ===========================================================================
# Part 5：guiguzi.analyze 公开函数 domain 参数端到端（stub callers）
# ===========================================================================
class TestGuiguziAnalyzeDomain:
    """验证 analyze() 带 domain 参数时实际注入 domain_guidance 到 prompt。"""

    _ITEMS = [{"text": "原文", "comment": "高赞评论"}]
    _MOCK_RESULT = json.dumps({
        "hook_reason": "r", "audience": "a", "hooks": ["h"], "direction": "d"
    })

    def test_finance_domain_injects_topic_prompt(self, monkeypatch: pytest.MonkeyPatch):
        """domain=finance → prompt 含 [FINANCE_TOPIC_STUB]（来自 stub profile）。"""
        from ncds_opus_factory.commands import guiguzi
        from ncds_opus_factory.server import domain_profiles as dp_mod

        monkeypatch.setattr(dp_mod, "get_profile", _stub_get_profile)

        captured: list[str] = []

        def fake_call_opus(prompt: str, **_: Any) -> str:
            captured.append(prompt)
            return self._MOCK_RESULT

        def fake_call_deepseek(prompt: str, **_: Any) -> str:
            captured.append(prompt)
            return self._MOCK_RESULT

        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_opus", fake_call_opus)
        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_deepseek", fake_call_deepseek)

        guiguzi.analyze(self._ITEMS, domain="finance")

        assert captured, "opus/deepseek 应被调用"
        assert all("[FINANCE_TOPIC_STUB]" in p for p in captured)
        assert all("仅供参考" in p for p in captured)

    def test_no_domain_no_injection(self, monkeypatch: pytest.MonkeyPatch):
        """domain=None → prompt 不含领域软背景段。"""
        from ncds_opus_factory.commands import guiguzi

        captured: list[str] = []

        def fake_call(prompt: str, **_: Any) -> str:
            captured.append(prompt)
            return self._MOCK_RESULT

        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_opus", fake_call)
        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_deepseek", fake_call)

        guiguzi.analyze(self._ITEMS)

        assert captured
        assert all("领域软背景" not in p for p in captured)

    def test_unknown_domain_no_injection(self, monkeypatch: pytest.MonkeyPatch):
        """未知 domain → get_profile 返回 None → 无注入。"""
        from ncds_opus_factory.commands import guiguzi
        from ncds_opus_factory.server import domain_profiles as dp_mod

        monkeypatch.setattr(dp_mod, "get_profile", _stub_get_profile)

        captured: list[str] = []

        def fake_call(prompt: str, **_: Any) -> str:
            captured.append(prompt)
            return self._MOCK_RESULT

        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_opus", fake_call)
        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_deepseek", fake_call)

        guiguzi.analyze(self._ITEMS, domain="unknown_xyz")

        assert captured
        assert all("领域软背景" not in p for p in captured)


# ===========================================================================
# Part 6：guiguzi.generate_topics 公开函数 domain 参数端到端（stub callers）
# ===========================================================================
class TestGuiguziGenerateTopicsDomain:
    """验证 generate_topics() 带 domain 参数时注入 domain_guidance 到默认模板。"""

    _ITEMS = [{"text": "原文", "comment": "评论A"}]
    _ANALYSIS = {"hook_reason": "r", "audience": "a", "hooks": ["h"], "direction": "d"}
    _MOCK_TOPICS = json.dumps([
        {"index": 1, "title": "选题A", "angle": "角度", "why": "爆款原因", "potential": 8}
    ])

    def test_finance_domain_injects_topic_prompt(self, monkeypatch: pytest.MonkeyPatch):
        """domain=finance → 默认模板含 [FINANCE_TOPIC_STUB]。"""
        from ncds_opus_factory.commands import guiguzi
        from ncds_opus_factory.server import domain_profiles as dp_mod

        monkeypatch.setattr(dp_mod, "get_profile", _stub_get_profile)
        monkeypatch.setattr("ncds_opus_factory.server.domain_profiles.get_profile", _stub_get_profile)

        captured: list[str] = []

        def fake_call(prompt: str, **_: Any) -> str:
            captured.append(prompt)
            return self._MOCK_TOPICS

        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_opus", fake_call)
        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_deepseek", fake_call)
        # stub topic_store.merge 避免文件副作用
        monkeypatch.setattr("ncds_opus_factory.common.topic_store.merge",
                            lambda topics, **_: {"added": len(topics), "skipped": 0})

        guiguzi.generate_topics(self._ITEMS, self._ANALYSIS, domain="finance")

        assert captured
        assert all("[FINANCE_TOPIC_STUB]" in p for p in captured)

    def test_custom_prompt_skips_domain_injection(self, monkeypatch: pytest.MonkeyPatch):
        """用户自定义 prompt 传入时，domain_guidance 不追加（尊重用户已编辑内容）。"""
        from ncds_opus_factory.commands import guiguzi
        from ncds_opus_factory.server import domain_profiles as dp_mod

        monkeypatch.setattr(dp_mod, "get_profile", _stub_get_profile)

        captured: list[str] = []

        def fake_call(prompt: str, **_: Any) -> str:
            captured.append(prompt)
            return self._MOCK_TOPICS

        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_opus", fake_call)
        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_deepseek", fake_call)
        monkeypatch.setattr("ncds_opus_factory.common.topic_store.merge",
                            lambda topics, **_: {"added": len(topics), "skipped": 0})

        custom_prompt = "自定义 prompt，不含 $source，评论：评论A"
        guiguzi.generate_topics(
            self._ITEMS, self._ANALYSIS,
            prompt=custom_prompt,
            domain="finance",
        )

        # 自定义 prompt 原样用，不额外追加 domain 内容
        assert captured
        assert all("[FINANCE_TOPIC_STUB]" not in p for p in captured)

    def test_no_domain_no_injection(self, monkeypatch: pytest.MonkeyPatch):
        """domain=None → 默认模板不含领域软背景。"""
        from ncds_opus_factory.commands import guiguzi

        captured: list[str] = []

        def fake_call(prompt: str, **_: Any) -> str:
            captured.append(prompt)
            return self._MOCK_TOPICS

        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_opus", fake_call)
        monkeypatch.setattr("ncds_opus_factory.commands.guiguzi.call_deepseek", fake_call)
        monkeypatch.setattr("ncds_opus_factory.common.topic_store.merge",
                            lambda topics, **_: {"added": len(topics), "skipped": 0})

        guiguzi.generate_topics(self._ITEMS, self._ANALYSIS)

        assert captured
        assert all("领域软背景" not in p for p in captured)
