"""works_repo 单测：manifest 读写 / load_domain / save_domain。"""

from __future__ import annotations

import pytest

from ncds_opus_factory.common import works_repo


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    # works 仓库根随 NOF_STATE_DIR 隔离到 tmp
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))


# --------------------------------------------------------------------------- #
# load_domain / save_domain
# --------------------------------------------------------------------------- #
def test_load_domain_missing_manifest_returns_none():
    """manifest 不存在时返回 None，不抛。"""
    assert works_repo.load_domain("douyin", "no_such_id") is None


def test_save_then_load_domain_roundtrip():
    """save_domain 写入、load_domain 读出，值相等。"""
    works_repo.save_domain("douyin", "aid001", "finance")
    assert works_repo.load_domain("douyin", "aid001") == "finance"


def test_save_domain_empty_string_does_not_write():
    """空串不写入 manifest（present-only 原则），load 仍返回 None。"""
    works_repo.save_domain("douyin", "aid002", "")
    assert works_repo.load_domain("douyin", "aid002") is None


def test_save_domain_none_does_not_write():
    """None 不写入 manifest，load 仍返回 None。"""
    works_repo.save_domain("douyin", "aid003", None)
    assert works_repo.load_domain("douyin", "aid003") is None


def test_save_domain_strips_whitespace():
    """save_domain 对值 strip；load_domain 对值 strip（防空串写盘）。"""
    works_repo.save_domain("douyin", "aid004", "  emotion  ")
    assert works_repo.load_domain("douyin", "aid004") == "emotion"


def test_load_domain_empty_string_in_manifest_returns_none():
    """manifest 里 domain="" 时，load_domain 返回 None（空串等价于缺失）。"""
    # 直接 merge 写一个空字符串 domain（绕过 save_domain 的 present-only 守门）
    works_repo.merge("douyin", "aid005", domain="")
    assert works_repo.load_domain("douyin", "aid005") is None


def test_save_domain_does_not_overwrite_other_fields():
    """save_domain 只碰 domain 字段，不丢其余 manifest 内容。"""
    works_repo.merge("douyin", "aid006", card={"title": "test"})
    works_repo.save_domain("douyin", "aid006", "entertainment")
    m = works_repo.load_manifest("douyin", "aid006")
    assert m is not None
    assert m.get("card", {}).get("title") == "test"
    assert m.get("domain") == "entertainment"
