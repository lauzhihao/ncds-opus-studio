"""领域 profile：每个领域(财经/足球/情感)对应一组提示词，驱动该领域作者的选题/撰稿。

profile key 与 web config/domains.ts 的 DomainKey 一一对应（finance/football/emotion）。
作者订阅里存 domain key（见 subscriptions.py 的 author["domain"]），生产时按 key 取对应 profile。

NOTE(占位): 提示词内容尚未编写。下面每个 profile 的字段先留 None 占位，
先把"每领域一组不同要求"的契约结构定下来，待内容侧补齐后填入实际 prompt。
字段含义：
  - topic_prompt: 鬼谷子/选题阶段的领域提示词（选题角度、禁区、调性）
  - draft_prompt: 柳永/撰稿阶段的领域提示词（口播稿风格、结构、用词）
后续可按需扩展更多阶段字段（标题、配图风格等）。
"""

from __future__ import annotations

from typing import TypedDict


class DomainProfile(TypedDict):
    topic_prompt: str | None
    draft_prompt: str | None


# key 必须与 web config/domains.ts 的 DomainKey 对齐
DOMAIN_PROFILES: dict[str, DomainProfile] = {
    "finance": {"topic_prompt": None, "draft_prompt": None},   # 财经
    "football": {"topic_prompt": None, "draft_prompt": None},  # 足球
    "emotion": {"topic_prompt": None, "draft_prompt": None},   # 情感
}


def get_profile(domain: str | None) -> DomainProfile | None:
    """按领域 key 取 profile；未知/空 key 返回 None（调用方回退到通用提示词）。"""
    if not domain:
        return None
    return DOMAIN_PROFILES.get(domain)
