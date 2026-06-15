"""S1 测试：events.jsonl 写入 + seq 单调 + 跨重启恢复 + tail 续读。

约定：*_test.py 与实现并排，pytest 自动发现。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ncds_opus_factory.server.pipeline_runner import PipelineRunner


# ---------------------------------------------------------------------------
# Fixture：创建临时 PipelineRunner（不依赖真实 pipeline/db）
# ---------------------------------------------------------------------------

@pytest.fixture()
def runner(tmp_path: Path) -> PipelineRunner:
    """返回指向 tmp 目录的 PipelineRunner，不连 EventBus 订阅者。"""
    return PipelineRunner(video_jobs_dir=tmp_path)


def _events_path(runner: PipelineRunner, job_id: str) -> Path:
    return runner._events_file(job_id)


# ---------------------------------------------------------------------------
# AC#1-a: 写入字段完整性
# ---------------------------------------------------------------------------

class TestEventsJsonlWrite:
    def test_fields_complete(self, runner: PipelineRunner, tmp_path: Path) -> None:
        """_emit 写入的每行 = event 原样 + ts/seq 信封：含 job_id/type/node/state/ts/seq。"""
        job_id = "testjob001"
        runner._emit(job_id, {
            "type": "node_status",
            "job_id": job_id,
            "node": "asr",
            "state": {"status": "running"},
        })

        ef = _events_path(runner, job_id)
        assert ef.is_file(), "events.jsonl 未创建"
        lines = [l for l in ef.read_text().splitlines() if l.strip()]
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["job_id"] == job_id
        assert record["type"] == "node_status"
        assert record["node"] == "asr"
        assert record["state"] == {"status": "running"}  # state 平铺在顶层(= 前端 wire 格式)
        assert "payload" not in record                    # 不得埋进 payload 子对象
        assert "ts" in record
        assert record["seq"] == 1

    def test_node_none_for_job_updated(self, runner: PipelineRunner) -> None:
        """job_updated 事件没有 node 字段，应落 null。"""
        job_id = "testjob002"
        runner._emit(job_id, {"type": "job_updated", "job_id": job_id})
        ef = _events_path(runner, job_id)
        record = json.loads(ef.read_text().strip())
        assert record["node"] is None

    def test_multiple_emits_no_corruption(self, runner: PipelineRunner) -> None:
        """多次 emit 每行独立，无撕裂。"""
        job_id = "testjob003"
        for i in range(10):
            runner._emit(job_id, {"type": "node_status", "job_id": job_id, "node": f"n{i}", "state": {}})

        ef = _events_path(runner, job_id)
        lines = [l for l in ef.read_text().splitlines() if l.strip()]
        assert len(lines) == 10
        # 每行独立可解析
        records = [json.loads(l) for l in lines]
        assert all("seq" in r for r in records)


# ---------------------------------------------------------------------------
# AC#1-b: seq 从 1 开始单调递增
# ---------------------------------------------------------------------------

class TestSeqMonotonic:
    def test_seq_starts_at_1(self, runner: PipelineRunner) -> None:
        """第一条事件 seq=1。"""
        job_id = "seqjob001"
        runner._emit(job_id, {"type": "node_status", "job_id": job_id, "node": "asr", "state": {}})
        ef = _events_path(runner, job_id)
        r = json.loads(ef.read_text().strip())
        assert r["seq"] == 1

    def test_seq_monotonic(self, runner: PipelineRunner) -> None:
        """连续 emit，seq 严格单调递增（1, 2, 3 ...）。"""
        job_id = "seqjob002"
        n = 5
        for _ in range(n):
            runner._emit(job_id, {"type": "job_updated", "job_id": job_id})
        ef = _events_path(runner, job_id)
        seqs = [json.loads(l)["seq"] for l in ef.read_text().splitlines() if l.strip()]
        assert seqs == list(range(1, n + 1))


# ---------------------------------------------------------------------------
# AC#2: seq 跨重启恢复（不回退）
# ---------------------------------------------------------------------------

class TestSeqRecovery:
    def test_seq_resumes_after_runner_recreate(self, tmp_path: Path) -> None:
        """重建 PipelineRunner 后，seq 从文件末行继续而非从 1 重开。"""
        job_id = "recoveryjob"
        r1 = PipelineRunner(video_jobs_dir=tmp_path)
        for _ in range(3):
            r1._emit(job_id, {"type": "job_updated", "job_id": job_id})

        # 新建 runner 模拟 server 重启
        r2 = PipelineRunner(video_jobs_dir=tmp_path)
        r2._emit(job_id, {"type": "job_updated", "job_id": job_id})

        ef = r2._events_file(job_id)
        seqs = [json.loads(l)["seq"] for l in ef.read_text().splitlines() if l.strip()]
        # 应为 [1, 2, 3, 4]，4 > 3，不回退到 1
        assert seqs[-1] == 4
        assert sorted(seqs) == seqs  # 严格单调

    def test_seq_not_reset_on_partial_file(self, tmp_path: Path) -> None:
        """events.jsonl 已有 50 行时，新 runner 的 seq 从 51 开始。"""
        job_id = "partialjob"
        r1 = PipelineRunner(video_jobs_dir=tmp_path)
        for _ in range(50):
            r1._emit(job_id, {"type": "job_updated", "job_id": job_id})

        r2 = PipelineRunner(video_jobs_dir=tmp_path)
        r2._emit(job_id, {"type": "job_updated", "job_id": job_id})

        ef = r2._events_file(job_id)
        last_record = json.loads(ef.read_text().splitlines()[-1])
        assert last_record["seq"] == 51


# ---------------------------------------------------------------------------
# AC#3: tail 续读（写 N 条 → 从 offset/since_seq 续读，重放不丢不重）
# ---------------------------------------------------------------------------

class TestTailReplay:
    def test_tail_from_offset(self, runner: PipelineRunner) -> None:
        """从指定 offset 续读：前面的行不重放，offset 之后的行全部读到。"""
        job_id = "tailjob001"
        # 先写 5 条
        for i in range(5):
            runner._emit(job_id, {"type": "node_status", "job_id": job_id, "node": f"n{i}", "state": {}})

        ef = _events_path(runner, job_id)
        # 读到第 3 行末尾的 offset（用 readline() 循环才能安全 tell()）
        offset_after_3 = 0
        with ef.open("r", encoding="utf-8") as fh:
            for _ in range(3):
                fh.readline()
            offset_after_3 = fh.tell()

        # 模拟 tail：再写 3 条
        for i in range(5, 8):
            runner._emit(job_id, {"type": "node_status", "job_id": job_id, "node": f"n{i}", "state": {}})

        # 从 offset_after_3 读：应包含第 4、5 条原有 + 3 条新增 = 5 行
        with ef.open("r", encoding="utf-8") as fh:
            fh.seek(offset_after_3)
            tail_records = [json.loads(l) for l in fh if l.strip()]

        seqs = [r["seq"] for r in tail_records]
        assert seqs == [4, 5, 6, 7, 8], f"got seqs: {seqs}"

    def test_tail_since_seq(self, runner: PipelineRunner) -> None:
        """since_seq=N 重放：只返回 seq > N 的事件，不重不丢。"""
        job_id = "tailjob002"
        for _ in range(10):
            runner._emit(job_id, {"type": "job_updated", "job_id": job_id})

        ef = _events_path(runner, job_id)
        since = 5
        replay = []
        with ef.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if int(obj.get("seq") or 0) > since:
                    replay.append(obj)

        assert [r["seq"] for r in replay] == [6, 7, 8, 9, 10]

    def test_tail_no_replay_default(self, runner: PipelineRunner) -> None:
        """since_seq 未传时，从 snapshot 时刻的 offset 之后 tail：历史不重放。"""
        job_id = "tailjob003"
        for _ in range(5):
            runner._emit(job_id, {"type": "job_updated", "job_id": job_id})

        ef = _events_path(runner, job_id)
        # 记录 snapshot 时刻 offset
        snapshot_offset = ef.stat().st_size

        # snapshot 后再写 3 条
        for _ in range(3):
            runner._emit(job_id, {"type": "job_updated", "job_id": job_id})

        # tail 从 snapshot_offset 开始：只读到新增 3 条
        with ef.open("r", encoding="utf-8") as fh:
            fh.seek(snapshot_offset)
            new_records = [json.loads(l) for l in fh if l.strip()]

        assert [r["seq"] for r in new_records] == [6, 7, 8]


# ---------------------------------------------------------------------------
# 内存 EventBus 仍工作（向后兼容验证）
# ---------------------------------------------------------------------------

class TestEventBusBackcompat:
    def test_bus_still_receives_events(self, runner: PipelineRunner) -> None:
        """_emit 内部仍调 bus.publish，订阅者可以同步拿到事件。"""
        loop = asyncio.new_event_loop()
        try:
            async def _inner() -> None:
                job_id = "buscmpat001"
                q = runner.bus.subscribe(job_id)
                runner._emit(job_id, {"type": "job_updated", "job_id": job_id})
                event = q.get_nowait()
                assert event["type"] == "job_updated"
            loop.run_until_complete(_inner())
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# 缺口2 守护：events.jsonl 末行 torn write（进程被 SIGKILL 写半截）
# ---------------------------------------------------------------------------

class TestTornWriteRecovery:
    def test_torn_last_line_not_counted_and_not_concatenated(self, tmp_path: Path) -> None:
        """末行残缺时：① seq 恢复忽略残行(不 +1)；② 新记录补 \\n 隔断、不与残行粘连。"""
        job_id = "tornjob"
        r1 = PipelineRunner(video_jobs_dir=tmp_path)
        for _ in range(3):
            r1._emit(job_id, {"type": "job_updated", "job_id": job_id})

        ef = r1._events_file(job_id)
        # 模拟进程被杀写半截：追加一个无换行、坏 JSON 的残行
        with ef.open("a", encoding="utf-8") as fh:
            fh.write('{"job_id": "tornjob", "type": "node_st')  # 无 \n、非法 JSON

        # 新 runner 模拟重启后恢复 + 再 emit 一条
        r2 = PipelineRunner(video_jobs_dir=tmp_path)
        r2._emit(job_id, {"type": "job_updated", "job_id": job_id})

        lines = [l for l in ef.read_text().splitlines() if l.strip()]
        oks, bads = [], []
        for l in lines:
            try:
                oks.append(json.loads(l))
            except json.JSONDecodeError:
                bads.append(l)

        # ② 残行独占一行(被 \n 隔断)、没吞掉新行：恰好 1 个坏行、新行可独立解析
        assert len(bads) == 1, f"残行应被隔断为独立坏行，got bads={bads}"
        # ① 残行不计 seq：3 条有效 + 残行(不计) + 新事件 → 新事件 seq=4 而非 5
        assert oks[-1]["seq"] == 4, f"残行不应计入 seq，期望 4 got {oks[-1]['seq']}"
