import logging
from typing import Dict, Any, Optional, Awaitable, Callable

logger = logging.getLogger(__name__)


class SessionCheckpointer:
    """
    Session & State Persistence Manager
    Provides checkpointing across agent conversation steps, supporting:
    - Thread-level isolation
    - State snapshotting & recovery
    - Time-travel inspection

    重构说明：
    - 旧实现使用进程内字典，重启即丢、无法跨 worker 共享
    - 现已委托给 RedisCheckpointer（持久化），保留同步 API 向后兼容
    - 同步方法在内部自动调度 async 实现
    """

    def __init__(self):
        # 内存缓存仅作为 Redis 不可用时的降级方案
        self._checkpoints: Dict[str, Dict[int, Dict[str, Any]]] = {}
        # 真实持久化后端
        from app.services.agent.redis_checkpointer import redis_checkpointer
        self._redis = redis_checkpointer

    def _run_async(self, coro):
        """在同步上下文中运行协程，复用事件循环"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 已在事件循环内：不能阻塞，走 fire-and-forget
                # 这种情况实际不会触发（caller 是 async）
                asyncio.ensure_future(coro)
                return None
        except RuntimeError:
            loop = None

        if loop is None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    def save_checkpoint(self, thread_id: str, step_index: int, state: Dict[str, Any]):
        """同步入口：保存状态（向后兼容 graph.py 的同步调用）"""
        try:
            coro = self._redis.save_checkpoint(thread_id, step_index, state)
            self._run_async(coro)
        except Exception as e:
            logger.warning(f"[Checkpointer] Redis 持久化失败，降级到内存: {e}")
            if thread_id not in self._checkpoints:
                self._checkpoints[thread_id] = {}
            self._checkpoints[thread_id][step_index] = dict(state)

        logger.debug(f"[Checkpointer] Saved checkpoint for thread {thread_id} at step {step_index}")

    async def asave_checkpoint(self, thread_id: str, step_index: int, state: Dict[str, Any]):
        """异步入口：推荐使用"""
        try:
            await self._redis.save_checkpoint(thread_id, step_index, state)
        except Exception as e:
            logger.warning(f"[Checkpointer] Redis 持久化失败，降级到内存: {e}")
            if thread_id not in self._checkpoints:
                self._checkpoints[thread_id] = {}
            self._checkpoints[thread_id][step_index] = dict(state)

    def get_latest_checkpoint(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """同步入口：获取最新快照"""
        try:
            coro = self._redis.get_latest_checkpoint(thread_id)
            result = self._run_async(coro)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(f"[Checkpointer] Redis 读取失败，降级到内存: {e}")
        if thread_id not in self._checkpoints or not self._checkpoints[thread_id]:
            return None
        max_step = max(self._checkpoints[thread_id].keys())
        return self._checkpoints[thread_id][max_step]

    async def aget_latest_checkpoint(self, thread_id: str) -> Optional[Dict[str, Any]]:
        try:
            result = await self._redis.get_latest_checkpoint(thread_id)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(f"[Checkpointer] Redis 读取失败，降级到内存: {e}")
        if thread_id not in self._checkpoints or not self._checkpoints[thread_id]:
            return None
        max_step = max(self._checkpoints[thread_id].keys())
        return self._checkpoints[thread_id][max_step]

    def get_checkpoint_at_step(self, thread_id: str, step_index: int) -> Optional[Dict[str, Any]]:
        """Time-travel replay: fetch state at a specific past step."""
        try:
            coro = self._redis.get_checkpoint_at_step(thread_id, step_index)
            result = self._run_async(coro)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(f"[Checkpointer] Redis 读取失败，降级到内存: {e}")
        return self._checkpoints.get(thread_id, {}).get(step_index)


session_checkpointer = SessionCheckpointer()
