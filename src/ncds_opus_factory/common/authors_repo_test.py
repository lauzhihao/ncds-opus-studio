"""authors_repo 单测:寻址键 / 存取 / TTL 新鲜判定 / refreshed_at 保真 / 路径消毒。"""

from __future__ import annotations

import time

import pytest

from ncds_opus_factory.common import authors_repo


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("NOF_STATE_DIR", str(tmp_path / "state" / "tasks"))
    monkeypatch.delenv("NOF_AUTHOR_CACHE_TTL_H", raising=False)


def test_author_key_per_platform():
    # douyin 用 sec_uid,tiktok 用 handle(unique_id)
    assert authors_repo.author_key("douyin", sec_uid="MS4wX", unique_id="dy") == "MS4wX"
    assert authors_repo.author_key("tiktok", sec_uid="MS4wY", unique_id="handle") == "handle"
    # 各自缺失时互为兜底
    assert authors_repo.author_key("tiktok", sec_uid="onlysec") == "onlysec"
    assert authors_repo.author_key("douyin", unique_id="onlyuid") == "onlyuid"


def test_save_load_roundtrip_stamps_refreshed_at():
    saved = authors_repo.save_profile("douyin", "MS4wA", {"nickname": "甲", "follower_count": 10})
    assert saved["platform"] == "douyin" and saved["sec_uid"] == "MS4wA"
    assert isinstance(saved["refreshed_at"], float) and saved["refreshed_at"] > 0
    loaded = authors_repo.load_profile("douyin", "MS4wA")
    assert loaded["nickname"] == "甲" and loaded["follower_count"] == 10
    assert authors_repo.is_fresh(loaded) is True


def test_load_missing_returns_none():
    assert authors_repo.load_profile("douyin", "NOPE") is None
    assert authors_repo.load_profile("douyin", "") is None


def test_is_fresh_ttl_window():
    now = time.time()
    assert authors_repo.is_fresh({"refreshed_at": now}) is True
    assert authors_repo.is_fresh({"refreshed_at": now - 3 * 3600}) is False  # 3h > 默认2h
    assert authors_repo.is_fresh({"nickname": "x"}) is False  # 无时间戳
    assert authors_repo.is_fresh(None) is False


def test_ttl_env_override(monkeypatch):
    monkeypatch.setenv("NOF_AUTHOR_CACHE_TTL_H", "1")
    now = time.time()
    assert authors_repo.is_fresh({"refreshed_at": now - 30 * 60}) is True   # 30min < 1h
    assert authors_repo.is_fresh({"refreshed_at": now - 90 * 60}) is False  # 90min > 1h
    monkeypatch.setenv("NOF_AUTHOR_CACHE_TTL_H", "abc")  # 非法回退默认 2h
    assert authors_repo.ttl_seconds() == 2 * 3600


def test_save_preserves_given_refreshed_at():
    authors_repo.save_profile("douyin", "MS4wB", {"nickname": "乙"}, refreshed_at=1000.0)
    assert authors_repo.load_profile("douyin", "MS4wB")["refreshed_at"] == 1000.0


def test_tiktok_keyed_by_handle_keeps_sec_uid():
    authors_repo.save_profile("tiktok", "myhandle", {"nickname": "tt", "sec_uid": "MS4wTT"})
    p = authors_repo.load_profile("tiktok", "myhandle")
    assert p["unique_id"] == "myhandle" and p["sec_uid"] == "MS4wTT"


def test_safe_id_blocks_path_traversal():
    """带 / 的 id 不会越出 authors_root 写盘。"""
    path = authors_repo.profile_path("douyin", "../evil/x")
    assert authors_repo.authors_root() in path.parents
    assert "/" not in path.parent.name  # 斜杠被消毒成单层目录名
