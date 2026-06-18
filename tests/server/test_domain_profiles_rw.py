"""DomainProfile per-agent 槽位 + finance.liuyong（写作）接入 rw 的验收测试。

覆盖：
- get_profile 返回 per-agent 槽位（guiguzi/liuyong/wudaozi/cover/boya）
- get_profile None 回退契约（空 key / 未知 key）
- _build_rw_prompt domain_guidance 注入（finance domain 命中 liuyong 写作指导）
- _build_rw_prompt 无 domain 时行为与原来完全一致（不注入任何领域内容）
- 封面(cover)从片内生图(wudaozi)拆出；boya 槽位占位
"""

from __future__ import annotations

from ncds_opus_factory.server import pipeline_runner as pr
from ncds_opus_factory.server.domain_profiles import (
    DomainProfile,
    DOMAIN_PROFILES,
    get_profile,
)


# ---------------------------------------------------------------------------
# get_profile：三字段 + 回退契约
# ---------------------------------------------------------------------------

_PER_AGENT_SLOTS = ("guiguzi", "liuyong", "wudaozi", "cover", "boya")


def test_get_profile_finance_has_per_agent_fields():
    """finance profile 必须包含 per-agent 槽位（guiguzi/liuyong/wudaozi/cover/boya）。"""
    p = get_profile("finance")
    assert p is not None
    # 槽位都存在（值可以是 None，但 key 必须在 TypedDict 里）
    for slot in _PER_AGENT_SLOTS:
        assert slot in p, f"finance 缺 {slot}"


def test_get_profile_emotion_has_per_agent_fields():
    """emotion profile 同样包含 per-agent 槽位。"""
    p = get_profile("emotion")
    assert p is not None
    for slot in _PER_AGENT_SLOTS:
        assert slot in p, f"emotion 缺 {slot}"


def test_get_profile_finance_draft_prompt_not_none():
    """finance.draft_prompt 必须有实质内容（task-2.4 核心目标）。"""
    p = get_profile("finance")
    assert p is not None
    assert p["liuyong"] is not None
    assert len(p["liuyong"]) > 100  # 有实质内容，不是占位符


def test_get_profile_finance_draft_prompt_contains_compliance():
    """finance.draft_prompt 必须包含合规红线关键词（不荐股/不预测点位）。"""
    p = get_profile("finance")
    assert p is not None
    text = p["liuyong"] or ""
    # 合规红线是 SOP 提炼的核心要求
    assert "荐股" in text or "不推荐" in text
    assert "预测" in text or "点位" in text


def test_get_profile_unknown_key_returns_none():
    """未知 domain key 返回 None（调用方回退到通用提示词）。"""
    assert get_profile("nonexistent_domain") is None


def test_get_profile_empty_string_returns_none():
    """空字符串 domain 返回 None。"""
    assert get_profile("") is None


def test_get_profile_none_returns_none():
    """None domain 返回 None。"""
    assert get_profile(None) is None


def test_all_profiles_have_per_agent_fields():
    """DOMAIN_PROFILES 所有 entry 都包含 per-agent 槽位（防止漏加新 key 时出 KeyError）。"""
    for key, profile in DOMAIN_PROFILES.items():
        for slot in _PER_AGENT_SLOTS:
            assert slot in profile, f"{key} 缺 {slot}"


# ---------------------------------------------------------------------------
# _build_rw_prompt：domain_guidance 双向验证
# ---------------------------------------------------------------------------

# 体裁 profile 层已废：_build_rw_prompt 现签名 (source_text, user_requirements="", domain_guidance=None)，
# domain_guidance 非空即作为唯一写作权威逐字注入，为空则回退通用兜底 _GENERIC_RW_BODY。
# 兜底首句「你是资深中文内容写手」只在无 domain 分支出现，domain 注入时被替换掉 —— 用它当区分哨兵。
_GENERIC_SENTINEL = "你是资深中文内容写手"


def _call_build(source: str = "测试源文档内容。",
                domain_guidance: str | None = None) -> tuple[str, str]:
    return pr._build_rw_prompt(source, domain_guidance=domain_guidance)


def test_build_rw_prompt_no_domain_guidance_baseline():
    """无 domain_guidance 时回退通用兜底（含兜底哨兵），不被领域写作方法替换。"""
    _, user_prompt = _call_build(domain_guidance=None)
    assert _GENERIC_SENTINEL in user_prompt


def test_build_rw_prompt_empty_domain_guidance_skipped():
    """空字符串 domain_guidance 不注入（等同 None，仍走兜底）。"""
    _, user_prompt = _call_build(domain_guidance="")
    assert _GENERIC_SENTINEL in user_prompt


def test_build_rw_prompt_whitespace_domain_guidance_skipped():
    """纯空白 domain_guidance 不注入（仍走兜底）。"""
    _, user_prompt = _call_build(domain_guidance="   \n  ")
    assert _GENERIC_SENTINEL in user_prompt


def test_build_rw_prompt_finance_guidance_injected():
    """传入 finance liuyong 写作方法时：guidance 全文逐字注入，且兜底层被替换掉。"""
    finance_profile = get_profile("finance")
    assert finance_profile is not None
    guidance = finance_profile["liuyong"]
    assert guidance  # 确保非 None

    _, user_prompt = _call_build(domain_guidance=guidance)
    assert guidance.strip() in user_prompt   # domain 写作方法作为唯一权威逐字注入
    assert _GENERIC_SENTINEL not in user_prompt  # 兜底被替换，不叠加


def test_build_rw_prompt_finance_guidance_contains_compliance_keywords():
    """finance domain_guidance 注入后，user_prompt 含合规红线关键词。"""
    finance_profile = get_profile("finance")
    assert finance_profile is not None
    guidance = finance_profile["liuyong"]
    assert guidance

    _, user_prompt = _call_build(domain_guidance=guidance)
    # 合规红线来自 SOP §十一 + 财经通用要求
    assert "荐股" in user_prompt or "不推荐" in user_prompt


def test_build_rw_prompt_source_text_preserved():
    """无论是否有 domain_guidance，源文档必须出现在 user_prompt 中。"""
    source = "这是一段独特的源文档内容UNIQUE_MARKER。"
    _, p1 = _call_build(source=source, domain_guidance=None)
    _, p2 = _call_build(source=source, domain_guidance="领域指导内容。")
    assert "UNIQUE_MARKER" in p1
    assert "UNIQUE_MARKER" in p2


def test_build_rw_prompt_domain_guidance_appears_before_source():
    """domain_guidance 应出现在源文档之前（体裁 → 领域 → 源文档的顺序）。"""
    guidance = "领域写作专属要求_MARKER。"
    _, user_prompt = _call_build(domain_guidance=guidance)
    pos_guidance = user_prompt.find("MARKER")
    pos_source = user_prompt.find("== 源文档 ==")
    assert pos_guidance != -1 and pos_source != -1
    assert pos_guidance < pos_source, "domain_guidance 应出现在源文档之前"


def test_build_rw_prompt_domain_guidance_is_additive():
    """注入 domain_guidance 后只替换"写作要求"权威层，通用脚手架(通用约束+源文档)仍保留(叠加不替换)。"""
    marker = "额外领域指导_MARKER。"
    _, with_domain = _call_build(domain_guidance=marker)
    assert marker in with_domain          # 领域写作方法注入
    assert "【通用约束】" in with_domain    # 通用约束脚手架仍在
    assert "== 源文档 ==" in with_domain    # 源文档章节仍在


# ---------------------------------------------------------------------------
# task-2.5 验收：emotion 三字段非 None + finance topic/image 非 None + 红线关键词
# ---------------------------------------------------------------------------

def test_emotion_three_fields_all_not_none():
    """task-2.5：emotion 三字段均已填入实质内容，不允许 None。"""
    p = get_profile("emotion")
    assert p is not None
    assert p["guiguzi"] is not None and len(p["guiguzi"]) > 50
    assert p["liuyong"] is not None and len(p["liuyong"]) > 50
    assert p["wudaozi"] is not None and len(p["wudaozi"]) > 50


def test_emotion_topic_prompt_contains_compliance_keywords():
    """emotion.topic_prompt 必须包含合规红线关键词（不做诊断/不贩卖焦虑/不替代专业）。"""
    p = get_profile("emotion")
    assert p is not None
    text = p["guiguzi"] or ""
    # 心理内容三大红线
    assert "诊断" in text or "标签" in text
    assert "焦虑" in text or "恐慌" in text
    assert "专业" in text


def test_emotion_draft_prompt_contains_compliance_keywords():
    """emotion.draft_prompt 必须包含合规红线关键词（不做诊断/不贩卖焦虑/不替代专业治疗）。"""
    p = get_profile("emotion")
    assert p is not None
    text = p["liuyong"] or ""
    assert "诊断" in text
    assert "焦虑" in text or "恐慌" in text
    assert "专业" in text


def test_emotion_draft_prompt_no_preaching_allowed():
    """emotion.draft_prompt 应明确禁止说教腔（共情不说教是核心调性要求）。"""
    p = get_profile("emotion")
    assert p is not None
    text = p["liuyong"] or ""
    # draft_prompt 里应写明禁止说教相关要求
    assert "说教" in text or "爹味" in text


def test_finance_topic_prompt_not_none():
    """task-2.5：finance.topic_prompt 已填入实质内容。"""
    p = get_profile("finance")
    assert p is not None
    assert p["guiguzi"] is not None and len(p["guiguzi"]) > 50


def test_finance_topic_prompt_contains_compliance_keywords():
    """finance.topic_prompt 必须包含合规禁区（不荐股/不预测点位/不承诺收益）。"""
    p = get_profile("finance")
    assert p is not None
    text = p["guiguzi"] or ""
    assert "荐股" in text or "投资标的" in text
    assert "预测" in text or "点位" in text
    assert "收益" in text or "稳赚" in text


def test_finance_image_style_not_none():
    """task-2.5：finance.image_style 已填入实质内容。"""
    p = get_profile("finance")
    assert p is not None
    assert p["wudaozi"] is not None and len(p["wudaozi"]) > 50


def test_emotion_image_style_not_none():
    """task-2.5：emotion.wudaozi（片内生图）已填入实质内容。"""
    p = get_profile("emotion")
    assert p is not None
    assert p["wudaozi"] is not None and len(p["wudaozi"]) > 50


# ---------------------------------------------------------------------------
# per-agent 重构验收：封面(cover)从片内生图(wudaozi)拆出；boya 槽位占位
# ---------------------------------------------------------------------------

def test_finance_cover_not_none_and_has_ctr():
    """finance.cover 有实质内容，且 CTR 取向写在 cover（封面），而非片内生图。"""
    p = get_profile("finance")
    assert p is not None
    cover = p["cover"] or ""
    assert len(cover) > 50
    assert "CTR" in cover and "封面" in cover


def test_finance_wudaozi_is_in_video_only():
    """finance.wudaozi 是片内生图，封面 CTR 内容已拆走（不再出现 CTR）。"""
    p = get_profile("finance")
    assert p is not None
    assert "CTR" not in (p["wudaozi"] or "")


def test_emotion_cover_not_none():
    """emotion.cover 已填实质内容（封面槽位独立于片内生图）。"""
    p = get_profile("emotion")
    assert p is not None
    assert p["cover"] is not None and len(p["cover"]) > 50


def test_boya_slot_present_but_none():
    """boya（配音）槽位已建、内容待填：两个 domain 均为 None（回退通用）。"""
    for domain in ("finance", "emotion"):
        p = get_profile(domain)
        assert p is not None
        assert "boya" in p
        assert p["boya"] is None
