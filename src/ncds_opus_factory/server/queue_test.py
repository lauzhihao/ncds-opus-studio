"""
queue_test.py — RedisQueue 单测，使用 fakeredis 注入，不依赖真 redis-server。

驱动方式：sync def test_* + asyncio.run(...)，与项目其余异步测试保持一致
（项目未配 asyncio_mode=auto，不用 pytest.mark.asyncio）。

覆盖：
- lpush → brpop 往返同值
- FIFO 顺序：lpush t1/t2/t3 → brpop 依次 t1/t2/t3
- brpop 超时（空队列）返回 None
- incr_quota：正常放行 / 越额 DECR 回退 / TTL 首次设置
- get_quota 读当前计数
- delete 清空队列（delete 后 brpop 超时 None）
- lpush 在连接故障时抛 ConnectionError，而 brpop 同情况不抛（返回 None）
- ping 在连接故障时返回 False
"""

import asyncio

import fakeredis.aioredis as fake_aioredis
import pytest
import redis.exceptions

from ncds_opus_factory.server.queue import RedisQueue


# ---------------------------------------------------------------------------
# 辅助：每个 test 获取独立的 FakeRedis 实例（隔离 key 空间）
# ---------------------------------------------------------------------------

def make_queue() -> tuple[RedisQueue, fake_aioredis.FakeRedis]:
    """创建共用同一 FakeServer 的 queue + 裸 client（验证 Redis 侧 key）。"""
    server = fake_aioredis.FakeServer()
    client = fake_aioredis.FakeRedis(server=server)
    queue = RedisQueue(client=client)
    return queue, client


# ---------------------------------------------------------------------------
# 往返：lpush → brpop
# ---------------------------------------------------------------------------

def test_lpush_brpop_roundtrip():
    """lpush 一个 task_id，brpop 取回相同值。"""
    async def _run():
        queue, _ = make_queue()
        await queue.lpush("shenkuo", "task-abc")
        result = await queue.brpop("shenkuo", timeout=1)
        assert result == "task-abc"
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# FIFO 顺序验证
# ---------------------------------------------------------------------------

def test_fifo_order():
    """
    连续 lpush t1/t2/t3，brpop 依次应得到 t1/t2/t3（FIFO）。
    LPUSH+BRPOP = FIFO；若误用 LPUSH+BLPOP 则为 LIFO，此用例会失败。
    """
    async def _run():
        queue, _ = make_queue()
        for tid in ["t1", "t2", "t3"]:
            await queue.lpush("shenkuo", tid)
        results = []
        for _ in range(3):
            val = await queue.brpop("shenkuo", timeout=1)
            assert val is not None
            results.append(val)
        assert results == ["t1", "t2", "t3"]
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 沈括刷新节流锁：窗口内只有首次抢到
# ---------------------------------------------------------------------------

def test_acquire_shenkuo_refresh_first_wins_then_throttled():
    """同一 aweme_id：首次 acquire 抢到 True，窗口内再 acquire 一律 False（节流 + 防并发）。"""
    async def _run():
        queue, client = make_queue()
        first = await queue.acquire_shenkuo_refresh("aw-1", ttl_seconds=3600)
        second = await queue.acquire_shenkuo_refresh("aw-1", ttl_seconds=3600)
        assert first is True
        assert second is False
        # 落了带 TTL 的 key
        assert await client.ttl("nof:shenkuo:refresh:aw-1") > 0
        # 不同作品互不影响
        assert await queue.acquire_shenkuo_refresh("aw-2", ttl_seconds=3600) is True
    asyncio.run(_run())


def test_acquire_shenkuo_refresh_fail_open_on_redis_error():
    """Redis 故障时 fail-open 返回 True（功能降级为每次都刷，不卡死）。"""
    class _ErrorClient:
        async def set(self, *a, **k):
            raise redis.exceptions.ConnectionError("boom")

    async def _run():
        queue = RedisQueue(client=_ErrorClient())
        assert await queue.acquire_shenkuo_refresh("aw-x") is True
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# brpop 超时返回 None
# ---------------------------------------------------------------------------

def test_brpop_timeout_returns_none():
    """空队列 brpop 应在 timeout 内返回 None，而非挂起。"""
    async def _run():
        queue, _ = make_queue()
        result = await queue.brpop("empty-cmd", timeout=1)
        assert result is None
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# delete 后 brpop 超时 None
# ---------------------------------------------------------------------------

def test_delete_clears_queue():
    """lpush 若干后 delete，再 brpop 应得 None（队列已空）。"""
    async def _run():
        queue, _ = make_queue()
        await queue.lpush("shenkuo", "old-task-1")
        await queue.lpush("shenkuo", "old-task-2")
        await queue.delete("shenkuo")
        result = await queue.brpop("shenkuo", timeout=1)
        assert result is None
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# task hash / claim：Redis 是执行协调真相源
# ---------------------------------------------------------------------------

def test_register_task_and_claim_once():
    """登记 pending 任务后只能被 claim 一次，第二次 claim 被 Redis 状态挡住。"""
    async def _run():
        queue, _ = make_queue()
        await queue.register_task("task-1", "shenkuo", status="pending", source="user")

        assert await queue.task_status("task-1") == "pending"
        assert await queue.claim_task("task-1") is True
        assert await queue.task_status("task-1") == "running"
        assert await queue.claim_task("task-1") is False
        assert await queue.is_inflight("task-1") is True

        await queue.mark_task_status("task-1", "completed")
        await queue.remove_inflight("task-1")
        assert await queue.task_status("task-1") == "completed"
        assert await queue.is_inflight("task-1") is False
    asyncio.run(_run())


def test_cancelled_task_cannot_be_claimed():
    """取消写进 Redis 后，队列里即便还有旧 task_id 也不能再被 worker claim。"""
    async def _run():
        queue, _ = make_queue()
        await queue.register_task("task-cancel", "liuyong", status="pending")
        await queue.mark_task_status("task-cancel", "cancelled")

        assert await queue.claim_task("task-cancel") is False
        assert await queue.task_status("task-cancel") == "cancelled"
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# incr_quota：正常放行
# ---------------------------------------------------------------------------

def test_incr_quota_within_limit():
    """连续 incr_quota 到 limit 都应 ok=True，计数从 1 到 limit。"""
    async def _run():
        queue, _ = make_queue()
        limit = 3
        for expected_n in range(1, limit + 1):
            n, ok = await queue.incr_quota("shenkuo", "2026-06-15", limit)
            assert ok is True
            assert n == expected_n
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# incr_quota：越额 DECR 回退
# ---------------------------------------------------------------------------

def test_incr_quota_over_limit_decr_rollback():
    """
    第 limit+1 次 incr_quota 应返回 ok=False，且 n == limit（DECR 回退生效）。
    确保计数不因越额而永久多 1。
    """
    async def _run():
        queue, _ = make_queue()
        limit = 2
        # 先打满
        for _ in range(limit):
            _, ok = await queue.incr_quota("bucket", "2026-06-15", limit)
            assert ok is True
        # 越额这一次
        n_returned, ok = await queue.incr_quota("bucket", "2026-06-15", limit)
        assert ok is False
        # n_returned 应等于 limit（DECR 回退后的计数）
        assert n_returned == limit
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# incr_quota：首次 EXPIRE
# ---------------------------------------------------------------------------

def test_incr_quota_first_call_sets_ttl():
    """第一次 incr_quota 应给 key 设 TTL（防永久堆积）。"""
    async def _run():
        queue, raw_client = make_queue()
        await queue.incr_quota("shenkuo", "2026-06-15", 10)
        # 直接查 Redis key 的 TTL
        ttl = await raw_client.ttl("nof:quota:2026-06-15:shenkuo")
        # TTL > 0 说明 expire 已设置
        assert ttl > 0
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_quota：读取当前计数
# ---------------------------------------------------------------------------

def test_get_quota_returns_current_count():
    """get_quota 应返回与 incr_quota 一致的当前计数。"""
    async def _run():
        queue, _ = make_queue()
        await queue.incr_quota("shenkuo", "2026-06-15", 10)
        await queue.incr_quota("shenkuo", "2026-06-15", 10)
        count = await queue.get_quota("shenkuo", "2026-06-15")
        assert count == 2
    asyncio.run(_run())


def test_get_quota_nonexistent_returns_zero():
    """key 不存在时 get_quota 应返回 0。"""
    async def _run():
        queue, _ = make_queue()
        count = await queue.get_quota("nonexistent-bucket", "2099-01-01")
        assert count == 0
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# incr_quota 越额后 get_quota 计数准确（DECR 回退不超额）
# ---------------------------------------------------------------------------

def test_incr_quota_over_limit_count_stays_at_limit():
    """
    越额后 get_quota 应仍返回 limit（不因超额调用而累计到 limit+1）。
    验证 DECR 回退在 Redis 侧真实生效。
    """
    async def _run():
        queue, _ = make_queue()
        limit = 3
        for _ in range(limit):
            await queue.incr_quota("bucket", "2026-06-15", limit)
        # 超额调用多次
        for _ in range(3):
            _, ok = await queue.incr_quota("bucket", "2026-06-15", limit)
            assert ok is False
        count = await queue.get_quota("bucket", "2026-06-15")
        assert count == limit
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 连接故障：lpush 抛 / brpop 不抛
# ---------------------------------------------------------------------------

class _ErrorClient:
    """
    仅用于注入测试的假 client，所有操作都抛 ConnectionError。
    模拟 Redis 完全不可达的场景。
    """

    async def lpush(self, *args, **kwargs):
        raise redis.exceptions.ConnectionError("test: redis unreachable")

    async def brpop(self, *args, **kwargs):
        raise redis.exceptions.ConnectionError("test: redis unreachable")

    async def ping(self, *args, **kwargs):
        raise redis.exceptions.ConnectionError("test: redis unreachable")

    async def aclose(self):
        pass


def test_lpush_raises_on_connection_error():
    """
    lpush 在 Redis 连接故障时必须直接抛出 ConnectionError。
    调用方 8810 据此走 503+meta failed 降级；绝不静默吞异常。
    """
    async def _run():
        queue = RedisQueue(client=_ErrorClient())
        with pytest.raises(redis.exceptions.ConnectionError):
            await queue.lpush("shenkuo", "task-x")
    asyncio.run(_run())


def test_brpop_does_not_raise_on_connection_error():
    """
    brpop 在 Redis 连接故障时不抛出，而是指数退避后返回 None。
    保证 worker while 循环不因 redis 抖动而退出。
    """
    async def _run():
        queue = RedisQueue(client=_ErrorClient())
        # 不应抛异常，应在退避后返回 None
        result = await queue.brpop("shenkuo", timeout=1)
        assert result is None
    asyncio.run(_run())


def test_brpop_backoff_escalates_and_resets():
    """brpop 退避跨调用累积(真指数)、成功后重置——防回归成恒定退避(死代码 bug)。"""
    async def _run():
        q = RedisQueue(client=_ErrorClient())
        assert q._brpop_backoff == 0.5
        await q.brpop("shenkuo", timeout=1)   # 失败 → 退避翻倍(真睡 0.5s)
        assert q._brpop_backoff == 1.0
        await q.brpop("shenkuo", timeout=1)   # 再失败 → 再翻倍(真睡 1.0s)
        assert q._brpop_backoff == 2.0
        # 换可用 client + 预置一条 → brpop 成功 → 退避重置回初值
        good = fake_aioredis.FakeRedis(server=fake_aioredis.FakeServer())
        await good.lpush("nof:q:shenkuo", b"x")
        q._client = good
        got = await q.brpop("shenkuo", timeout=1)
        assert got == "x"
        assert q._brpop_backoff == 0.5
    asyncio.run(_run())


def test_ping_returns_false_on_connection_error():
    """
    ping 在 Redis 不可达时应返回 False，而非抛异常（让 loop 静默跳过 tick）。
    """
    async def _run():
        queue = RedisQueue(client=_ErrorClient())
        result = await queue.ping()
        assert result is False
    asyncio.run(_run())
