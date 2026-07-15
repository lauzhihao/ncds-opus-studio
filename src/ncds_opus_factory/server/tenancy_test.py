"""多租户 owner 隔离单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from ncds_opus_factory.server.pipeline_models import JobState, NodeState
from ncds_opus_factory.server.pipeline_state_store import PipelineStateStoreMixin
from ncds_opus_factory.server.task_store import TaskStore
from ncds_opus_factory.server.tenancy import assert_owner, owner_matches, resource_visible


def test_assert_owner_auth_off_passthrough() -> None:
    assert assert_owner("1", me=None) == "1"
    assert assert_owner(None, me=None) is None


def test_assert_owner_match() -> None:
    assert assert_owner("7", me="7") == "7"


def test_assert_owner_mismatch_404() -> None:
    with pytest.raises(HTTPException) as ei:
        assert_owner("1", me="2")
    assert ei.value.status_code == 404


def test_assert_owner_claimable_unowned() -> None:
    assert assert_owner(None, me="3", claimable=True) == "3"


def test_resource_visible() -> None:
    assert resource_visible(None, None) is True
    assert resource_visible("1", None) is True
    assert resource_visible("1", "1") is True
    assert resource_visible(None, "1") is False
    assert resource_visible("2", "1") is False


def test_owner_matches_list_filter() -> None:
    assert owner_matches(None, None) is True
    assert owner_matches("1", "1") is True
    assert owner_matches(None, "1") is True  # list 时无主先保留再 claim
    assert owner_matches("2", "1") is False


class _JobStore(PipelineStateStoreMixin):
    def __init__(self, root: Path) -> None:
        self.video_jobs_dir = root
        self.bus = type("B", (), {"publish": lambda *a, **k: None})()
        self._event_seq = {}

    def reconcile_runtime_state(self, state: JobState, emit: bool = False) -> JobState:
        return state

    def _emit(self, *a, **k) -> None:
        return None


def test_job_owner_create_list_claim(tmp_path: Path) -> None:
    store = _JobStore(tmp_path)
    j1 = store.create_job("final_preview", "a", {}, owner_id="1")
    assert j1.owner_id == "1"
    j2 = store.create_job("final_preview", "b", {}, owner_id="2")
    j3 = store.create_job("final_preview", "c", {}, owner_id=None)

    mine = store.list_jobs(owner_id="1")
    ids = {x["job_id"] for x in mine}
    assert j1.job_id in ids
    assert j2.job_id not in ids
    assert j3.job_id not in ids  # 无主不进列表（避免多用户串看）

    n = store.claim_unowned_jobs("1")
    assert n >= 1
    mine2 = store.list_jobs(owner_id="1")
    assert any(x["job_id"] == j3.job_id and x.get("owner_id") == "1" for x in mine2)


def test_task_owner_list_claim(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    t1 = store.create("asr", {}, owner_id="1")
    t2 = store.create("asr", {}, owner_id="2")
    t3 = store.create("asr", {}, owner_id=None)

    mine = store.list_tasks(owner_id="1")
    ids = {m.task_id for m in mine}
    assert t1.task_id in ids
    assert t2.task_id not in ids
    assert t3.task_id not in ids

    store.claim_unowned_tasks("1")
    t3b = store.get_meta(t3.task_id)
    assert t3b is not None and t3b.owner_id == "1"
    assert t3.task_id in {m.task_id for m in store.list_tasks(owner_id="1")}
