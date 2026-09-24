"""
Redis 持久化任务状态存储
解决原 TaskManager 进程内字典重启即丢的致命缺陷
所有任务状态以 Hash 形式存于 Redis，TTL 24 小时
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from app.core.redis_client import redis_manager

logger = logging.getLogger(__name__)

TASK_KEY_PREFIX = "agent:task"
TASK_INDEX_KEY = "agent:task:index"
TASK_TTL_SECONDS = 86400  # 任务记录保留 24 小时

MAX_STEPS_KEPT = 256  # 单任务最多保留步骤数


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(value: Any) -> str:
    """统一序列化，保证 datetime / dict 均可入库"""
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value) if value is not None else ""


class RedisTaskStore:
    """
    任务持久化存储：基于 Redis Hash + List
    - Hash 存任务元数据（status / progress / output ...）
    - List 存步骤轨迹（按时间顺序追加，自动 ltrim）
    """

    def _task_key(self, task_id: str) -> str:
        return f"{TASK_KEY_PREFIX}:{task_id}"

    def _steps_key(self, task_id: str) -> str:
        return f"{TASK_KEY_PREFIX}:{task_id}:steps"

    def _conv_link_key(self, task_id: str) -> str:
        return f"{TASK_KEY_PREFIX}:{task_id}:conversation"

    async def set_conversation_link(self, task_id: str, conversation_id: Optional[str]) -> None:
        """任务暂停时记录绑定的 conversation_id，供跨进程 HITL 恢复时找回。"""
        await redis_manager.set(
            self._conv_link_key(task_id), conversation_id or "", ex=TASK_TTL_SECONDS
        )

    async def get_conversation_link(self, task_id: str) -> Optional[str]:
        value = await redis_manager.get(self._conv_link_key(task_id))
        return value or None

    async def create_task(
        self,
        session_id: str,
        thread_id: str,
        agent_type: str,
        user_id: str = "u-001",
        user_message: str = "",
    ) -> str:
        task_id = str(uuid.uuid4())
        now = _utcnow_iso()
        mapping = {
            "task_id": task_id,
            "session_id": session_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "agent_type": agent_type,
            "user_message": user_message[:2000],  # 截断，避免单键过长
            "status": "PENDING",
            "progress": "0",
            "current_node": "Harness_Init",
            "output_message": "",
            "artifact": "",
            "citations": "",
            "error_message": "",
            "created_at": now,
            "updated_at": now,
        }
        await redis_manager.hset(self._task_key(task_id), mapping=mapping, ex=TASK_TTL_SECONDS)
        # 加入索引集合（用于管理后台列表）
        if redis_manager.is_connected and redis_manager.client:
            try:
                await redis_manager.client.sadd(TASK_INDEX_KEY, task_id)
                await redis_manager.client.expire(TASK_INDEX_KEY, TASK_TTL_SECONDS)
            except Exception as e:
                logger.warning(f"[RedisTaskStore] 索引写入失败: {e}")
        return task_id

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """读取单任务状态，附带步骤轨迹"""
        data = await redis_manager.hget_all(self._task_key(task_id))
        if not data:
            return None

        # 类型转换
        result = {
            "task_id": data.get("task_id", ""),
            "session_id": data.get("session_id", ""),
            "thread_id": data.get("thread_id", ""),
            "user_id": data.get("user_id", ""),
            "agent_type": data.get("agent_type", ""),
            "user_message": data.get("user_message", ""),
            "status": data.get("status", "PENDING"),
            "progress": int(data.get("progress", "0") or 0),
            "current_node": data.get("current_node", ""),
            "output_message": data.get("output_message", "") or None,
            "error_message": data.get("error_message", "") or None,
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
        }

        # 反序列化 artifact / citations
        artifact_raw = data.get("artifact", "")
        result["artifact"] = json.loads(artifact_raw) if artifact_raw else None
        citations_raw = data.get("citations", "")
        result["citations"] = json.loads(citations_raw) if citations_raw else []

        # 加载步骤列表
        result["steps"] = await self.get_steps(task_id)
        return result

    async def get_steps(self, task_id: str) -> List[Dict[str, Any]]:
        if redis_manager.is_connected and redis_manager.client:
            try:
                raw_list = await redis_manager.client.lrange(self._steps_key(task_id), 0, -1)
                return [json.loads(item) for item in raw_list]
            except Exception as e:
                logger.error(f"[RedisTaskStore] 读取步骤失败: {e}")
        return []

    async def append_step(self, task_id: str, step: Dict[str, Any]):
        """追加单条步骤轨迹"""
        if redis_manager.is_connected and redis_manager.client:
            try:
                await redis_manager.client.rpush(
                    self._steps_key(task_id),
                    json.dumps(step, ensure_ascii=False, default=str)
                )
                # 限制步骤数量
                await redis_manager.client.ltrim(self._steps_key(task_id), -MAX_STEPS_KEPT, -1)
                await redis_manager.client.expire(self._steps_key(task_id), TASK_TTL_SECONDS)
            except Exception as e:
                logger.error(f"[RedisTaskStore] 追加步骤失败: {e}")

    async def update_status(
        self,
        task_id: str,
        status: str,
        progress: Optional[int] = None,
        current_node: Optional[str] = None,
    ):
        mapping: Dict[str, Any] = {
            "status": status,
            "updated_at": _utcnow_iso(),
        }
        if progress is not None:
            mapping["progress"] = str(progress)
        if current_node:
            mapping["current_node"] = current_node
        await redis_manager.hupdate(self._task_key(task_id), mapping=mapping)

    async def complete_task(self, task_id: str, result: Dict[str, Any]):
        """任务完成时一次性写入最终产物"""
        mapping = {
            "status": "COMPLETED",
            "progress": "100",
            "current_node": "Done",
            "output_message": _serialize(result.get("output", "")),
            "artifact": _serialize(result.get("artifact")) if result.get("artifact") else "",
            "citations": _serialize(result.get("citations", [])),
            "updated_at": _utcnow_iso(),
        }
        await redis_manager.hupdate(self._task_key(task_id), mapping=mapping)

        # 追加 trace 汇总
        trace_summary = result.get("trace_summary", {})
        steps = trace_summary.get("steps", []) if trace_summary else []
        for step in steps:
            await self.append_step(task_id, step)

    async def fail_task(self, task_id: str, error_message: str):
        await redis_manager.hupdate(self._task_key(task_id), mapping={
            "status": "FAILED",
            "error_message": error_message,
            "updated_at": _utcnow_iso(),
        })

    async def list_tasks(self, limit: int = 50) -> List[str]:
        """返回最近任务 ID 列表（用于管理后台）"""
        if redis_manager.is_connected and redis_manager.client:
            try:
                return list(await redis_manager.client.smembers(TASK_INDEX_KEY) or [])[:limit]
            except Exception as e:
                logger.error(f"[RedisTaskStore] 读取任务索引失败: {e}")
        return []


# 全局单例
redis_task_store = RedisTaskStore()
