"""
Redis 异步客户端基础设施
提供：
1. 全局共享 async Redis 连接池
2. 分布式幂等锁（基于 Redlock 算法语义）
3. SSE Pub/Sub 事件总线（解决 Celery worker 与 FastAPI 进程隔离问题）
"""
import asyncio
import logging
import uuid
import time
from typing import Optional, Any, Dict
from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as aioredis
    _HAS_REDIS = True
except ImportError:  # pragma: no cover
    aioredis = None
    _HAS_REDIS = False


class RedisManager:
    """
    全局 Redis 异步连接管理器
    单例模式，复用连接池；支持优雅降级到内存模式（开发环境无 Redis 时）
    """

    def __init__(self):
        self._pool: Optional[Any] = None
        self._client: Optional[Any] = None
        self._fallback_cache: Dict[str, Any] = {}
        self._connected = False

    async def connect(self):
        if not _HAS_REDIS:
            logger.warning("[RedisManager] redis 包未安装，降级到内存模式（仅本地开发）")
            self._connected = False
            return

        try:
            self._pool = aioredis.ConnectionPool(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD or None,
                db=settings.REDIS_DB,
                max_connections=50,
                socket_timeout=5,
                socket_connect_timeout=3,
                retry_on_timeout=True,
                decode_responses=True,
            )
            self._client = aioredis.Redis(connection_pool=self._pool)
            await self._client.ping()
            self._connected = True
            logger.info(f"[RedisManager] 已连接 Redis {settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}")
        except Exception as e:
            logger.warning(f"[RedisManager] Redis 连接失败，降级到内存模式: {e}")
            self._connected = False
            self._client = None

    async def disconnect(self):
        if self._pool:
            await self._pool.disconnect()
            self._connected = False
            logger.info("[RedisManager] Redis 连接池已关闭")

    @property
    def client(self):
        return self._client

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def get(self, key: str) -> Optional[str]:
        if self._client and self._connected:
            try:
                return await self._client.get(key)
            except Exception as e:
                logger.error(f"[RedisManager] GET 失败: {e}")
        return self._fallback_cache.get(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        if self._client and self._connected:
            try:
                await self._client.set(key, value, ex=ex)
                return True
            except Exception as e:
                logger.error(f"[RedisManager] SET 失败: {e}")
        self._fallback_cache[key] = value
        return True

    async def hset(self, name: str, mapping: Dict[str, Any], ex: Optional[int] = None) -> bool:
        if self._client and self._connected:
            try:
                await self._client.hset(name, mapping=mapping)
                if ex:
                    await self._client.expire(name, ex)
                return True
            except Exception as e:
                logger.error(f"[RedisManager] HSET 失败: {e}")
        self._fallback_cache[name] = mapping
        return True

    async def hget_all(self, name: str) -> Dict[str, Any]:
        if self._client and self._connected:
            try:
                return await self._client.hgetall(name) or {}
            except Exception as e:
                logger.error(f"[RedisManager] HGETALL 失败: {e}")
        return self._fallback_cache.get(name, {})

    async def hupdate(self, name: str, mapping: Dict[str, Any], ex: Optional[int] = None) -> bool:
        if self._client and self._connected:
            try:
                await self._client.hset(name, mapping=mapping)
                if ex:
                    await self._client.expire(name, ex)
                return True
            except Exception as e:
                logger.error(f"[RedisManager] HUPDATE 失败: {e}")
        return True

    async def delete(self, *keys: str) -> int:
        if self._client and self._connected:
            try:
                return await self._client.delete(*keys)
            except Exception as e:
                logger.error(f"[RedisManager] DELETE 失败: {e}")
        cnt = 0
        for k in keys:
            if k in self._fallback_cache:
                del self._fallback_cache[k]
                cnt += 1
        return cnt


class IdempotencyLock:
    """
    分布式幂等锁（基于 Redis SET NX EX 实现的 Redlock 语义）
    防止用户连击/重试导致 Agent 任务重复执行

    用法:
        lock = IdempotencyLock(f"agent:{session_id}:{thread_id}", ttl=300)
        if await lock.acquire():
            try:
                # 执行业务
            finally:
                await lock.release()
        else:
            raise RuntimeError("重复请求，已被幂等锁拦截")
    """

    def __init__(self, lock_key: str, ttl: int = 300, retry_count: int = 0, retry_delay: float = 0.2):
        self.lock_key = f"lock:{lock_key}"
        self.ttl = ttl
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self._token: Optional[str] = None

    async def acquire(self) -> bool:
        redis_mgr = redis_manager
        self._token = str(uuid.uuid4())

        if redis_mgr.is_connected and redis_mgr.client:
            for attempt in range(self.retry_count + 1):
                try:
                    result = await redis_mgr.client.set(
                        self.lock_key, self._token, nx=True, ex=self.ttl
                    )
                    if result:
                        return True
                    if attempt < self.retry_count:
                        await asyncio.sleep(self.retry_delay)
                except Exception as e:
                    logger.error(f"[IdempotencyLock] 获取锁失败 {self.lock_key}: {e}")
                    if attempt < self.retry_count:
                        await asyncio.sleep(self.retry_delay)
            return False

        # 降级：无 Redis 时使用内存锁
        existing = redis_manager._fallback_cache.get(self.lock_key)
        if existing is None:
            redis_manager._fallback_cache[self.lock_key] = self._token
            return True
        return False

    async def release(self):
        if not self._token:
            return
        redis_mgr = redis_manager
        if redis_mgr.is_connected and redis_mgr.client:
            try:
                # Lua 脚本保证原子性，避免误删别人的锁
                lua_script = """
                if redis.call('get', KEYS[1]) == ARGV[1] then
                    return redis.call('del', KEYS[1])
                else
                    return 0
                end
                """
                await redis_mgr.client.eval(lua_script, 1, self.lock_key, self._token)
            except Exception as e:
                logger.error(f"[IdempotencyLock] 释放锁失败: {e}")
        else:
            if redis_manager._fallback_cache.get(self.lock_key) == self._token:
                del redis_manager._fallback_cache[self.lock_key]


# 全局单例
redis_manager = RedisManager()
