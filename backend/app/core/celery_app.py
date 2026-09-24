"""
Celery 应用与 Worker 任务定义
解决 FastAPI BackgroundTasks 无法跨进程扩展、无法持久化的痛点
架构：
  - FastAPI 进程负责接收请求 -> 投递到 Celery 队列 -> 立即返回 task_id
  - Celery Worker 进程消费任务 -> 执行 graph_engine -> 通过 Redis Pub/Sub 推送 SSE 事件
  - FastAPI SSE 端点订阅 Redis channel -> 转发给前端

启动 worker:
    celery -A app.core.celery_app.celery_app worker --loglevel=info --concurrency=4
启动 flower 监控:
    celery -A app.core.celery_app.celery_app flower
"""
import asyncio
import json
import logging
import os
from typing import Optional, Dict, Any, List

from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    from celery import Celery
    _HAS_CELERY = True
except ImportError:
    Celery = None
    _HAS_CELERY = False

# Redis broker / backend URL 构造
def _build_redis_url(db: Optional[int] = None) -> str:
    pwd = f":{settings.REDIS_PASSWORD}@" if settings.REDIS_PASSWORD else ""
    return f"redis://{pwd}{settings.REDIS_HOST}:{settings.REDIS_PORT}/{db if db is not None else settings.REDIS_DB}"

BROKER_URL = _build_redis_url(settings.REDIS_DB)
RESULT_BACKEND_URL = _build_redis_url(settings.REDIS_DB + 1 if settings.REDIS_DB < 15 else settings.REDIS_DB)
EVENT_BUS_PREFIX = "sse:task"

celery_app: Optional["Celery"] = None


async def publish_task_event(task_id: str, event: Dict[str, Any]) -> None:
    """
    将事件发布到任务事件总线（模块级，Celery worker 与 fallback 执行器共用）：
    - Redis 可用：Pub/Sub 实时推送 + List 暂存最近 64 条（断线重连补偿）
    - Redis 不可用：走进程内 InProcEventBus（仅单进程 fallback 有效）
    """
    from app.core.redis_client import redis_manager
    from app.services.task_queue.inproc_event_bus import inproc_bus

    event_json = json.dumps(event, ensure_ascii=False, default=str)

    if redis_manager.is_connected and redis_manager.client:
        channel = f"{EVENT_BUS_PREFIX}:{task_id}"
        try:
            await redis_manager.client.publish(channel, event_json)
            list_key = f"events:{task_id}"
            await redis_manager.client.rpush(list_key, event_json)
            await redis_manager.client.ltrim(list_key, -64, -1)
            await redis_manager.client.expire(list_key, 3600)
        except Exception as e:
            logger.error(f"[TaskEvent] Redis 发布失败 task={task_id}: {e}")
    else:
        # 无 Redis：进程内总线（BackgroundTasks fallback 与 SSE 端点同进程）
        await inproc_bus.publish(task_id, event_json)


if _HAS_CELERY:
    celery_app = Celery(
        "edu_agent_worker",
        broker=BROKER_URL,
        backend=RESULT_BACKEND_URL,
    )

    celery_app.conf.update(
        # 序列化与结果
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_expires=3600,  # 结果保留 1 小时
        # 任务路由：长任务走专用队列
        task_routes={
            "agent.run_workflow": {"queue": "agent_heavy"},
            "agent.run_sync": {"queue": "agent_light"},
        },
        task_default_queue="default",
        # 并发：基于 asyncio 池，每 worker 可处理多协程
        worker_concurrency=4,
        task_acks_late=True,  # 任务完成后才 ack，崩溃时自动重投递
        task_reject_on_worker_lost=True,
        # 可靠性：可见性超时（防止任务被重复消费）
        broker_transport_options={"visibility_timeout": 1800},
        # 监控
        worker_send_task_events=True,
        task_send_sent_event=True,
        worker_prefetch_multiplier=1,  # 长任务场景下避免一个 worker 抢占过多任务
        # 优雅关闭
        worker_max_tasks_per_child=200,  # 防止内存泄漏
    )

    @celery_app.task(name="agent.run_workflow", bind=True, max_retries=2, default_retry_delay=10)
    def run_agent_workflow_task(
        self,
        task_id: str,
        user_message: str,
        session_id: str,
        thread_id: str,
        user_id: str = "u-001",
        user_role: str = "teacher",
        agent_type: str = "supervisor",
        kb_ids: Optional[List[str]] = None,
        conversation_id: Optional[str] = None,
        sub_agent_mode: bool = False,
    ) -> Dict[str, Any]:
        """
        Celery 任务：执行 Agent 多智能体工作流
        所有 SSE 事件通过 Redis Pub/Sub 推送到 channel 'sse:task:{task_id}'
        conversation_id 用于任务终态时把 assistant 结果补写到历史对话库
        """
        # Celery 5.x 同步上下文，需要在新事件循环里跑 asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _async_run_workflow(
                    task_id=task_id,
                    user_message=user_message,
                    session_id=session_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    user_role=user_role,
                    agent_type=agent_type,
                    kb_ids=kb_ids,
                    conversation_id=conversation_id,
                    sub_agent_mode=sub_agent_mode,
                )
            )
            return result
        except Exception as e:
            logger.exception(f"[Celery Task {task_id}] 执行失败: {e}")
            loop.run_until_complete(_publish_event(task_id, {
                "event_type": "error",
                "task_id": task_id,
                "payload": {"error": str(e), "task_id": task_id}
            }))
            # 重试（指数退避由 default_retry_delay + Celery 内部退避机制）
            raise self.retry(exc=e, countdown=10 * (self.request.retries + 1))
        finally:
            loop.close()


    async def _async_run_workflow(
        task_id: str,
        user_message: str,
        session_id: str,
        thread_id: str,
        user_id: str,
        user_role: str,
        agent_type: str,
        kb_ids: Optional[List[str]],
        conversation_id: Optional[str] = None,
        sub_agent_mode: bool = False,
    ) -> Dict[str, Any]:
        # 延迟导入避免循环依赖
        from app.services.agent.teaching_graph import teaching_graph_runner
        from app.harness.base import AgentHarness
        from app.services.task_queue.redis_task_store import redis_task_store
        from app.services.task_queue.result_persistence import (
            bind_conversation,
            build_done_payload,
            friendly_error_message,
            persist_task_failure,
            persist_task_result,
            persist_user_message,
        )

        harness = AgentHarness(session_id=session_id, user_role=user_role)

        # 事件回调：把思维链步骤实时推送到 Redis Pub/Sub
        async def event_callback(event: Dict[str, Any]):
            await _publish_event(task_id, event)

        # 更新任务状态为 RUNNING
        await redis_task_store.update_status(task_id, "RUNNING", current_node="Supervisor")

        # 绑定历史对话并落用户消息（conversation_id 为空时新建，done 时回传前端）
        conversation_id = bind_conversation(
            conversation_id, user_id, agent_type, session_id, thread_id
        )
        persist_user_message(conversation_id, user_message)

        try:
            result = await teaching_graph_runner.run_workflow(
                user_message=user_message,
                session_id=session_id,
                thread_id=thread_id,
                user_id=user_id,
                user_role=user_role,
                explicit_agent=agent_type,
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
                await redis_task_store.set_conversation_link(task_id, conversation_id)
                await _publish_event(task_id, {
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
                logger.info(f"[Celery Task {task_id}] 已暂停，等待教师审批")
                return result

            # 写回 Redis
            await redis_task_store.complete_task(task_id, result)
            # assistant 结果补写历史对话库（与 /sync-run 契约一致）
            persist_task_result(conversation_id, result)
            # 推送结束事件
            if result.get("artifact"):
                await _publish_event(task_id, {
                    "event_type": "artifact",
                    "task_id": task_id,
                    "payload": result["artifact"]
                })
            await _publish_event(task_id, {
                "event_type": "done",
                "task_id": task_id,
                "payload": build_done_payload(conversation_id, result)
            })
            return result
        except Exception as e:
            await redis_task_store.fail_task(task_id, str(e))
            persist_task_failure(conversation_id, str(e), agent_type)
            await _publish_event(task_id, {
                "event_type": "error",
                "task_id": task_id,
                "payload": {
                    "error": friendly_error_message(str(e)),
                    "raw_error": str(e)[:500],
                    "conversation_id": conversation_id,
                    "task_id": task_id,
                }
            })
            raise


    # 发布函数定义在模块级（celery 包缺失时 fallback 执行器仍可 import）
    _publish_event = publish_task_event
else:
    logger.warning("[CeleryApp] celery 包未安装，worker 任务不可用")


async def subscribe_task_events(task_id: str):
    """
    FastAPI SSE 端点调用此函数订阅 task 事件流
    生成器模式，yield 标准的 SSE data 字符串（data 始终为 JSON 字符串，前端固定 JSON.parse）
    """
    from app.core.redis_client import redis_manager

    channel = f"{EVENT_BUS_PREFIX}:{task_id}"
    list_key = f"events:{task_id}"

    # Redis 不可用：进程内总线（与 fallback 执行器同进程，支持本地无 Redis 开发）
    if not (redis_manager.is_connected and redis_manager.client):
        from app.services.task_queue.inproc_event_bus import inproc_bus
        async for chunk in inproc_bus.subscribe(task_id):
            yield chunk
        return

    # 1. 先读取 List 中的历史事件（断线重连补偿）
    try:
        history = await redis_manager.client.lrange(list_key, 0, -1)
        for item in history:
            yield f"data: {item}\n\n"
            try:
                parsed = json.loads(item)
                if parsed.get("event_type") in ("done", "error"):
                    return
            except Exception:
                pass

        # 2. 订阅 Pub/Sub 接收后续实时事件
        pubsub = redis_manager.client.pubsub()
        await pubsub.subscribe(channel)
        try:
            # 带 keep-alive 的循环
            while True:
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True), timeout=30.0
                    )
                    if message and message.get("type") == "message":
                        data = message.get("data", "")
                        yield f"data: {data}\n\n"
                        try:
                            parsed = json.loads(data)
                            if parsed.get("event_type") in ("done", "error"):
                                return
                        except Exception:
                            pass
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
    except Exception as e:
        logger.error(f"[SSE Subscribe] 订阅失败: {e}")
        err = json.dumps(
            {"event_type": "error", "payload": {"error": f"订阅失败: {str(e)}"}},
            ensure_ascii=False,
        )
        yield f"data: {err}\n\n"
