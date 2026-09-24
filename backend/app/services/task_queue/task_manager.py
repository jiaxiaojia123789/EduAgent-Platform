"""
异步任务调度器（企业级重构）

重构说明：
- 旧实现：进程内 dict + asyncio.Queue + BackgroundTasks，重启即丢、无法跨 worker
- 新实现：
  1. 任务状态持久化到 Redis Hash（redis_task_store）
  2. 任务执行投递到 Celery 队列（celery_app.run_agent_workflow_task.delay）
  3. SSE 事件通过 Redis Pub/Sub 跨进程广播（celery_app.subscribe_task_events）
  4. 用户连击幂等锁（IdempotencyLock）
  5. 兼容模式：Celery 不可用时自动降级到 FastAPI BackgroundTasks
"""
import logging
import uuid
from typing import Dict, Any, Optional, AsyncGenerator

from app.services.task_queue.redis_task_store import redis_task_store
from app.core.redis_client import redis_manager, IdempotencyLock

logger = logging.getLogger(__name__)


class TaskManager:
    """
    企业级任务调度器
    对外 API 保持向后兼容（create_task / execute_task_async / get_task_status / stream_task_events）
    内部实现切换到 Celery + Redis + Pub/Sub
    """

    def __init__(self):
        self._celery_available = False
        try:
            from app.core.celery_app import celery_app
            self._celery_app = celery_app
            self._celery_available = celery_app is not None
        except Exception as e:
            logger.warning(f"[TaskManager] Celery 不可用，降级到 BackgroundTasks: {e}")
            self._celery_app = None
            self._celery_available = False

    async def create_task(
        self,
        session_id: str,
        thread_id: str,
        agent_type: str,
        user_id: str = "u-001",
        user_message: str = "",
    ) -> str:
        """创建任务并持久化到 Redis"""
        return await redis_task_store.create_task(
            session_id=session_id,
            thread_id=thread_id,
            agent_type=agent_type,
            user_id=user_id,
            user_message=user_message,
        )

    async def submit_task(
        self,
        task_id: str,
        user_message: str,
        session_id: str,
        thread_id: str,
        user_id: str = "u-001",
        user_role: str = "teacher",
        agent_type: str = "supervisor",
        kb_ids: Optional[list] = None,
        conversation_id: Optional[str] = None,
        sub_agent_mode: bool = False,
    ) -> Dict[str, Any]:
        """
        提交任务到执行队列
        优先走 Celery，降级走 BackgroundTasks（在 api 层注入 background_tasks）
        返回：{"dispatcher": "celery"/"fallback"/"duplicate", "task_id": ...}
        """
        # 1. 幂等锁：防止用户连击
        lock_key = f"agent:{session_id}:{thread_id}"
        lock = IdempotencyLock(lock_key, ttl=600)  # 锁 10 分钟
        acquired = await lock.acquire()
        if not acquired:
            # 检查是否已有同 thread 任务
            existing = await redis_task_store.get_task(task_id)
            if existing and existing.get("status") in ("PENDING", "RUNNING", "WAITING_APPROVAL"):
                return {
                    "dispatcher": "duplicate",
                    "task_id": task_id,
                    "message": f"任务 {task_id} 已在执行中，请勿重复提交"
                }

        # 2. 投递到 Celery
        if self._celery_available:
            try:
                self._celery_app.send_task(
                    "agent.run_workflow",
                    kwargs={
                        "task_id": task_id,
                        "user_message": user_message,
                        "session_id": session_id,
                        "thread_id": thread_id,
                        "user_id": user_id,
                        "user_role": user_role,
                        "agent_type": agent_type,
                        "kb_ids": kb_ids,
                        "conversation_id": conversation_id,
                        "sub_agent_mode": sub_agent_mode,
                    },
                    queue="agent_heavy",
                )
                return {
                    "dispatcher": "celery",
                    "task_id": task_id,
                    "message": "任务已投递至 Celery 队列"
                }
            except Exception as e:
                logger.error(f"[TaskManager] Celery 投递失败，降级到 BackgroundTasks: {e}")

        # 3. 降级路径：由 caller 注入 background_tasks 执行
        return {
            "dispatcher": "fallback",
            "task_id": task_id,
            "message": "Celery 不可用，使用进程内调度"
        }

    async def execute_task_async(
        self,
        task_id: str,
        user_message: str,
        user_id: str = "u-001",
        user_role: str = "teacher",
        kb_ids: Optional[list] = None,
        conversation_id: Optional[str] = None,
        sub_agent_mode: bool = False,
    ):
        """
        进程内 fallback 执行器
        当 Celery 不可用时由 FastAPI BackgroundTasks 调用
        仍然通过任务事件总线（Redis 或进程内）推送事件，SSE 体验一致
        """
        task = await redis_task_store.get_task(task_id)
        if not task:
            return

        await redis_task_store.update_status(task_id, "RUNNING", current_node="Supervisor")

        try:
            from app.services.agent.teaching_graph import teaching_graph_runner
            from app.harness.base import AgentHarness
            from app.core.celery_app import publish_task_event
            from app.services.task_queue.result_persistence import (
                bind_conversation,
                build_done_payload,
                friendly_error_message,
                persist_task_failure,
                persist_task_result,
                persist_user_message,
            )

            harness = AgentHarness(session_id=task["session_id"], user_role=user_role)

            async def event_callback(event: Dict[str, Any]):
                # 覆盖 task_id（节点内部用 session_id 占位）
                event["task_id"] = task_id
                await publish_task_event(task_id, event)

            await redis_task_store.update_status(task_id, "RUNNING", current_node="Supervisor")

            # 绑定历史对话并落用户消息（conversation_id 为空时新建，done 时回传前端）
            conversation_id = bind_conversation(
                conversation_id,
                user_id,
                task["agent_type"],
                task["session_id"],
                task["thread_id"],
            )
            persist_user_message(conversation_id, user_message)

            result = await teaching_graph_runner.run_workflow(
                user_message=user_message,
                session_id=task["session_id"],
                thread_id=task["thread_id"],
                user_id=user_id,
                user_role=user_role,
                explicit_agent=task["agent_type"],
                kb_ids=kb_ids,
                harness=harness,
                event_callback=event_callback,
                sub_agent_mode=sub_agent_mode,
                task_id=task_id,
                conversation_id=conversation_id,
            )

            # ---- HITL 暂停：不落 assistant、不发 done，等待教师审批 ----
            if result.get("run_status") == "waiting_approval":
                await redis_task_store.update_status(
                    task_id, "WAITING_APPROVAL", current_node="HITL_Gate"
                )
                await publish_task_event(task_id, {
                    "event_type": "approval_required",
                    "task_id": task_id,
                    "payload": {
                        "task_id": task_id,
                        "conversation_id": conversation_id,
                        "output": result.get("output", ""),
                        "quality_score": result.get("quality_score", 0.0),
                        "artifact": result.get("artifact"),
                    },
                })
                logger.info(f"[TaskManager] 任务 {task_id} 已暂停，等待教师审批")
                return

            await self._finalize_success(task_id, conversation_id, result)

        except Exception as e:
            await self._finalize_failure(task_id, task, conversation_id, e)

    async def resume_task(
        self,
        task_id: str,
        approved: bool,
        comments: Optional[str] = None,
    ):
        """
        HITL 审批恢复（fallback/同进程模式）：
        从 teaching_graph_runner 的暂停注册表续跑，终态后复用同一套落库/推流收尾逻辑。
        """
        from app.services.agent.teaching_graph import teaching_graph_runner
        from app.core.celery_app import publish_task_event

        task = await redis_task_store.get_task(task_id)
        if not task:
            return
        paused = teaching_graph_runner.get_paused_run(task_id)
        conversation_id = paused.conversation_id if paused else None

        async def event_callback(event: Dict[str, Any]):
            event["task_id"] = task_id
            await publish_task_event(task_id, event)

        await redis_task_store.update_status(task_id, "RUNNING", current_node="HITL_Gate")
        try:
            result = await teaching_graph_runner.resume(
                task_id=task_id,
                approved=approved,
                comments=comments,
                event_callback=event_callback,
            )

            # 被驳回返工后再次进入审批：继续保持暂停态
            if result.get("run_status") == "waiting_approval":
                await redis_task_store.update_status(
                    task_id, "WAITING_APPROVAL", current_node="HITL_Gate"
                )
                await publish_task_event(task_id, {
                    "event_type": "approval_required",
                    "task_id": task_id,
                    "payload": {
                        "task_id": task_id,
                        "conversation_id": conversation_id,
                        "output": result.get("output", ""),
                        "quality_score": result.get("quality_score", 0.0),
                        "artifact": result.get("artifact"),
                    },
                })
                return

            await self._finalize_success(task_id, conversation_id, result)
        except Exception as e:
            await self._finalize_failure(task_id, task, conversation_id, e)

    async def _finalize_success(self, task_id: str, conversation_id, result: Dict[str, Any]):
        """任务终态收尾：Redis 完成态 + assistant 落库 + artifact/done 事件"""
        from app.core.celery_app import publish_task_event
        from app.services.task_queue.result_persistence import (
            build_done_payload,
            persist_task_result,
        )

        await redis_task_store.complete_task(task_id, result)
        persist_task_result(conversation_id, result)

        if result.get("artifact"):
            await publish_task_event(task_id, {
                "event_type": "artifact",
                "task_id": task_id,
                "payload": result["artifact"]
            })
        await publish_task_event(task_id, {
            "event_type": "done",
            "task_id": task_id,
            "payload": build_done_payload(conversation_id, result)
        })

    async def _finalize_failure(self, task_id: str, task: dict, conversation_id, exc: Exception):
        """任务异常收尾：Redis 失败态 + 错误消息落库 + error 事件"""
        logger.exception(f"[TaskManager] 执行失败 task={task_id}: {exc}")
        await redis_task_store.fail_task(task_id, str(exc))
        try:
            from app.core.celery_app import publish_task_event
            from app.services.task_queue.result_persistence import (
                friendly_error_message,
                persist_task_failure,
            )
            agent_type = (task or {}).get("agent_type", "supervisor")
            persist_task_failure(conversation_id, str(exc), agent_type)
            await publish_task_event(task_id, {
                "event_type": "error",
                "task_id": task_id,
                "payload": {
                    "error": friendly_error_message(str(exc)),
                    "raw_error": str(exc)[:500],
                    "conversation_id": conversation_id,
                    "task_id": task_id,
                }
            })
        except Exception:
            logger.exception(f"[TaskManager] 失败事件发布失败 task={task_id}")

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """从 Redis 读取任务状态"""
        return await redis_task_store.get_task(task_id)

    async def stream_task_events(self, task_id: str) -> AsyncGenerator[str, None]:
        """
        SSE 事件流生成器
        从 Redis Pub/Sub 订阅事件，统一 yield 给 FastAPI StreamingResponse
        """
        from app.core.celery_app import subscribe_task_events
        async for chunk in subscribe_task_events(task_id):
            yield chunk


# 全局单例
task_manager = TaskManager()
