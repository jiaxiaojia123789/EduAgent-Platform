"""
LangGraph Redis Checkpointer
解决原 SessionCheckpointer 内存字典进程重启即丢、无法跨 worker 共享的痛点

特性：
1. 状态持久化到 Redis Hash，TTL 7 天
2. 支持 thread 级别隔离
3. 支持时间旅行（按 step_index 索引）
4. 与 LangGraph BaseCheckpointSaver 接口对齐（可选实现）
"""
import json
import logging
from typing import Dict, Any, Optional

from app.core.redis_client import redis_manager

logger = logging.getLogger(__name__)

CHECKPOINT_PREFIX = "langgraph:checkpoint"
CHECKPOINT_TTL = 604800  # 7 天


class RedisCheckpointer:
    """
    Redis 持久化 Checkpointer
    替换原 SessionCheckpointer 的进程内字典实现
    """

    def _thread_key(self, thread_id: str) -> str:
        return f"{CHECKPOINT_PREFIX}:{thread_id}"

    def _step_key(self, thread_id: str, step_index: int) -> str:
        return f"{CHECKPOINT_PREFIX}:{thread_id}:step:{step_index}"

    async def save_checkpoint(self, thread_id: str, step_index: int, state: Dict[str, Any]):
        """保存状态快照"""
        if redis_manager.is_connected and redis_manager.client:
            try:
                # 把不可直接序列化的对象（如 Sequence）转成 list
                safe_state = self._safe_serialize(state)
                state_json = json.dumps(safe_state, ensure_ascii=False, default=str)

                # 索引：step_index -> state_json
                await redis_manager.client.hset(
                    self._thread_key(thread_id),
                    str(step_index),
                    state_json,
                )
                await redis_manager.client.expire(self._thread_key(thread_id), CHECKPOINT_TTL)

                # 维护最新 step 指针
                await redis_manager.client.set(
                    f"{CHECKPOINT_PREFIX}:{thread_id}:latest",
                    str(step_index),
                    ex=CHECKPOINT_TTL,
                )
                logger.debug(f"[RedisCheckpoint] Saved thread={thread_id} step={step_index}")
            except Exception as e:
                logger.error(f"[RedisCheckpoint] 保存失败 thread={thread_id}: {e}")

    async def get_latest_checkpoint(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """获取最新快照"""
        if redis_manager.is_connected and redis_manager.client:
            try:
                latest_step_str = await redis_manager.client.get(
                    f"{CHECKPOINT_PREFIX}:{thread_id}:latest"
                )
                if not latest_step_str:
                    return None
                state_json = await redis_manager.client.hget(
                    self._thread_key(thread_id),
                    latest_step_str,
                )
                return json.loads(state_json) if state_json else None
            except Exception as e:
                logger.error(f"[RedisCheckpoint] 读取最新失败 thread={thread_id}: {e}")
        return None

    async def get_checkpoint_at_step(self, thread_id: str, step_index: int) -> Optional[Dict[str, Any]]:
        """时间旅行：按 step 索引获取历史状态"""
        if redis_manager.is_connected and redis_manager.client:
            try:
                state_json = await redis_manager.client.hget(
                    self._thread_key(thread_id),
                    str(step_index),
                )
                return json.loads(state_json) if state_json else None
            except Exception as e:
                logger.error(f"[RedisCheckpoint] 时间旅行读取失败: {e}")
        return None

    async def list_steps(self, thread_id: str) -> list:
        """列出该 thread 的所有 step 索引"""
        if redis_manager.is_connected and redis_manager.client:
            try:
                data = await redis_manager.client.hgetall(self._thread_key(thread_id))
                return sorted(int(k) for k in (data or {}).keys())
            except Exception as e:
                logger.error(f"[RedisCheckpoint] 列出步骤失败: {e}")
        return []

    def _safe_serialize(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """把 state 中的特殊容器转成 JSON 可序列化的形式"""
        safe = {}
        for k, v in state.items():
            if k == "messages":
                # messages 是 Annotated[Sequence, operator.add] 的累积列表
                try:
                    safe[k] = list(v)
                except Exception:
                    safe[k] = []
            elif isinstance(v, (list, tuple)):
                safe[k] = list(v)
            elif isinstance(v, dict):
                safe[k] = self._safe_serialize(v)
            else:
                safe[k] = v
        return safe


# 全局单例
redis_checkpointer = RedisCheckpointer()
