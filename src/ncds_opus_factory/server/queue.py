"""
server/queue.py — async Redis 封装，供 8810 producer 与 nof-worker consumer 共用。

设计依据：S3-redis-worker-design.md 决策 A/B/D。
- key 前缀统一 nof:
- LPUSH 入 + BRPOP 出 = FIFO（先进先出，与 asyncio.Queue 一致）
- lpush 连接故障直接抛出（调用方 8810 据此走 503+meta failed 降级）
- brpop 连接故障内部捕获，指数退避后返回 None（让 worker while 循环自然重试）
- incr_quota 使用 Redis 原子计数，越额 DECR 回退保证跨进程并发下不超发
"""

import asyncio
import logging
import os
from typing import Optional

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
# TimeoutError 是 RedisError 的子类（与 ConnectionError 兄弟关系）。
# brpop 设了 socket_timeout 时，服务端正常空轮询（无新任务）会在 socket 层
# 先于服务端 nil 超时，抛 RedisTimeoutError 而非返回 None——这是正常现象，
# 不应走退避分支。必须在 except RedisError 之前单独截获。
from redis.exceptions import TimeoutError as RedisTimeoutError

logger = logging.getLogger(__name__)

# key 前缀，所有 nof 相关 key 统一挂在此命名空间下
_PREFIX = "nof:"

# 配额 key 默认 TTL（秒），可通过环境变量覆盖
_DEFAULT_QUOTA_TTL_HOURS = 48
_QUOTA_TTL_SECONDS = int(os.environ.get("NOF_QUOTA_TTL_HOURS", _DEFAULT_QUOTA_TTL_HOURS)) * 3600

# brpop 连接故障退避参数（秒）
_BACKOFF_INIT = 0.5
_BACKOFF_MAX = 5.0


def _queue_key(cmd: str) -> str:
    """队列 key：nof:q:{cmd}"""
    return f"{_PREFIX}q:{cmd}"


def _quota_key(bucket: str, date: str) -> str:
    """配额 key：nof:quota:{date}:{bucket}"""
    return f"{_PREFIX}quota:{date}:{bucket}"


class RedisQueue:
    """
    async Redis 封装。

    构造函数接受可注入的 client（方便 fakeredis 单测）；
    默认从 REDIS_URL 环境变量建立连接。
    """

    def __init__(self, client: Optional[aioredis.Redis] = None) -> None:
        # 允许外部注入 client（单测用 fakeredis 注入）
        if client is not None:
            self._client = client
            self._owns_client = False  # 外部注入的 client 生命周期由外部管理
        else:
            url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
            self._client = aioredis.from_url(url, decode_responses=False)
            self._owns_client = True
        # brpop 退避状态放实例属性：worker 每次 brpop 失败返回 None、外层 while 重试，
        # 退避必须跨调用累积才是真指数(放方法局部会每次重置成初值=恒定退避)。
        self._brpop_backoff = _BACKOFF_INIT

    # -------------------------------------------------------------------------
    # 队列操作
    # -------------------------------------------------------------------------

    async def lpush(self, cmd: str, task_id: str) -> None:
        """
        LPUSH nof:q:{cmd} task_id，左进右出 = FIFO。

        连接故障直接抛出 ConnectionError，让调用方 8810 走 503+meta failed 降级。
        绝不静默吞异常——防止幽灵 pending 任务永久卡死。
        """
        key = _queue_key(cmd)
        # 不 try/except：连接故障直接上抛
        await self._client.lpush(key, task_id)

    async def brpop(self, cmd: str, timeout: int = 5) -> Optional[str]:
        """
        BRPOP nof:q:{cmd} timeout，右出 = FIFO 出队端。

        与 lpush 的"直接抛"相反：连接故障在此内部消化，
        做指数退避（0.5s → 封顶 5s）后返回 None，
        让 worker while 循环自然重试，不退出 worker 进程。

        成功/超时后退避计数重置。
        返回 task_id str，超时返回 None。
        """
        key = _queue_key(cmd)
        try:
            result = await self._client.brpop([key], timeout=timeout)
            # 成功/超时：退避重置回初值
            self._brpop_backoff = _BACKOFF_INIT
            if result is None:
                return None
            # result = (key_bytes, value_bytes)
            return result[1].decode()
        except RedisTimeoutError:
            # 正常空轮询超时（socket_timeout <= brpop timeout 时，socket 层先于服务端
            # nil 超时抛出 RedisTimeoutError）——与「brpop 返回 None」语义相同，即无新任务。
            # 重置退避回初值（不惩罚正常空闲），直接返回 None，不打告警日志。
            self._brpop_backoff = _BACKOFF_INIT
            return None
        except (RedisConnectionError, RedisError) as exc:
            # 真连接故障（网络断开/Redis 宕机）：退避后返回 None，让 worker 外层 while 自然重试。
            # 退避跨调用累积(实例属性)才是真指数；封顶 _BACKOFF_MAX。
            wait = self._brpop_backoff
            logger.warning("brpop connection error, backoff %.1fs: %s", wait, exc)
            await asyncio.sleep(wait)
            self._brpop_backoff = min(wait * 2, _BACKOFF_MAX)
            return None

    # -------------------------------------------------------------------------
    # 工具：删队列（recover 启动时清空旧队列）
    # -------------------------------------------------------------------------

    async def delete(self, cmd: str) -> None:
        """DEL nof:q:{cmd}，worker recover 启动时清空旧队列。"""
        key = _queue_key(cmd)
        await self._client.delete(key)

    # -------------------------------------------------------------------------
    # 配额原子计数（决策 B）
    # -------------------------------------------------------------------------

    async def incr_quota(self, bucket: str, date: str, limit: int) -> tuple[int, bool]:
        """
        跨进程并发安全的配额原子计数。

        流程：
        1. INCR key → n
        2. 若 n == 1（首次），EXPIRE key ttl（防 key 永久堆积）
        3. 若 n > limit，DECR 回退（保证计数不永久超额），返回 (n-1, False) 超额
        4. 否则返回 (n, True) 放行

        语义：并发下最多 limit 个 caller 拿到 ok=True，DECR 回退防超发。
        """
        key = _quota_key(bucket, date)
        n = await self._client.incr(key)
        if n == 1:
            # 首次设置 key，加 TTL 防止永久堆积
            await self._client.expire(key, _QUOTA_TTL_SECONDS)
        if n > limit:
            # 超额：DECR 回退，返回回退后的计数（=limit）和 False
            await self._client.decr(key)
            return (n - 1, False)
        return (n, True)

    async def get_quota(self, bucket: str, date: str) -> int:
        """GET nof:quota:{date}:{bucket} → int，不存在返 0。供 quota_remaining 软预查。"""
        key = _quota_key(bucket, date)
        val = await self._client.get(key)
        if val is None:
            return 0
        return int(val)

    # -------------------------------------------------------------------------
    # 探活 / 生命周期
    # -------------------------------------------------------------------------

    async def ping(self) -> bool:
        """
        PING 探活，worker loop 在 submit 前调用。
        连不上返回 False（让 loop 静默跳过当前 tick，不创建垃圾 failed meta）。
        """
        try:
            return await self._client.ping()
        except (RedisConnectionError, RedisError):
            return False

    # -------------------------------------------------------------------------
    # inflight 跨进程 Set（S3 步7，blocker#6）
    # key: nof:inflight，SADD/SREM/SISMEMBER/DEL
    # -------------------------------------------------------------------------

    _INFLIGHT_KEY = f"{_PREFIX}inflight"

    async def add_inflight(self, task_id: str) -> None:
        """SADD nof:inflight task_id：标记任务在途（worker _run 入口调用）。"""
        await self._client.sadd(self._INFLIGHT_KEY, task_id)

    async def remove_inflight(self, task_id: str) -> None:
        """SREM nof:inflight task_id：任务结束/取消后清除在途标记（worker _run finally 调用）。"""
        await self._client.srem(self._INFLIGHT_KEY, task_id)

    async def is_inflight(self, task_id: str) -> bool:
        """SISMEMBER nof:inflight task_id：跨进程判断任务是否在途（restore 路由调用）。"""
        return bool(await self._client.sismember(self._INFLIGHT_KEY, task_id))

    async def clear_inflight(self) -> None:
        """DEL nof:inflight：worker 启动恢复时清除上次进程遗留的脏在途标记。

        worker 进程重启后内存里没有真正在途的任务；Redis inflight Set 里若还有残留，
        是上次进程崩溃前来不及 SREM 留下的脏状态，不清则 restore 永久返回 409（blocker#6）。
        """
        await self._client.delete(self._INFLIGHT_KEY)

    async def aclose(self) -> None:
        """关连接归还，优雅停服防泄漏。仅关自己创建的 client。"""
        if self._owns_client:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# 模块级单例工厂
# ---------------------------------------------------------------------------

_default_queue: Optional[RedisQueue] = None


def get_default_queue() -> RedisQueue:
    """
    获取模块级默认 RedisQueue 单例（读 REDIS_URL 环境变量）。
    供 8810/worker 复用，避免重复创建连接池。
    """
    global _default_queue
    if _default_queue is None:
        _default_queue = RedisQueue()
    return _default_queue
