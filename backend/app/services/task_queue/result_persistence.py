"""
异步任务结果 -> 历史对话落库
============================
Celery worker（celery_app._async_run_workflow）与进程内 fallback
（task_manager.execute_task_async）两条执行路径共用，保证 SSE 流式链路
与 /sync-run 同步链路的历史持久化契约完全一致：用户消息在 /run 提交时落库，
assistant 结果（含 plan_dag / artifact / citations）在任务终态时补写。

conversation_storage 基于本地 sqlite3 单文件，worker 与 web 同机部署可直接共享。
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def friendly_error_message(error: str) -> str:
    """把上游错误转成前端可直接展示的中文提示。"""
    text = error or "未知错误"
    if "AllocationQuota" in text or "FreeTierOnly" in text:
        return (
            "阿里云百炼 API 免费额度已耗尽：请在百炼控制台充值或关闭「仅使用免费层」模式后重试。"
        )
    return f"任务执行失败：{text[:300]}"


def bind_conversation(
    conversation_id: Optional[str],
    user_id: str,
    agent_type: str,
    session_id: str,
    thread_id: str,
) -> Optional[str]:
    """
    异步执行器开场：携带 conversation_id 则校验沿用，否则新建归属当前 agent 的历史对话。
    返回最终绑定的 conversation_id；存储异常时返回 None（不阻断任务执行）。
    """
    try:
        from app.services.chat.conversation_storage import conversation_storage

        if conversation_id:
            conv = conversation_storage.get_session(conversation_id)
            if conv:
                return conversation_id
            # 前端传入的会话已失效：落到新会话，避免消息写丢
        conv = conversation_storage.create_session(
            user_id=user_id,
            agent_type=agent_type,
            session_id=session_id,
            thread_id=thread_id,
        )
        return conv["id"]
    except Exception:
        logger.exception("[ResultPersistence] 绑定历史对话失败")
        return None


def persist_user_message(conversation_id: Optional[str], content: str) -> None:
    """用户消息落库（首条消息时自动生成会话标题）。"""
    if not conversation_id:
        return
    try:
        from app.services.chat.conversation_storage import conversation_storage

        conversation_storage.auto_title_if_needed(conversation_id, content)
        conversation_storage.append_message(conversation_id, role="user", content=content)
    except Exception:
        logger.exception(f"[ResultPersistence] 用户消息落库失败 conv={conversation_id}")


def _agent_meta(agent_type: str) -> Dict[str, str]:
    """与 /sync-run 保持一致的智能体名称/头像元数据。"""
    try:
        from app.api.v1.agents import EDUCATION_AGENT_MATRIX
        meta = next((a for a in EDUCATION_AGENT_MATRIX if a["id"] == agent_type), None)
        if meta:
            return {"agent_name": meta["name"], "agent_avatar": meta["avatar"]}
    except Exception:
        pass
    return {"agent_name": agent_type or "智能体", "agent_avatar": "🤖"}


def persist_task_result(conversation_id: Optional[str], result: Dict[str, Any]) -> None:
    """任务成功：assistant 结果落库（best-effort，失败仅告警不影响任务终态）。"""
    if not conversation_id:
        return
    try:
        from app.services.chat.conversation_storage import conversation_storage

        agent_type = result.get("agent_type") or "supervisor"
        meta = _agent_meta(agent_type)
        extra = {
            "plan_dag": result.get("plan_dag"),
            "sub_results": result.get("sub_results"),
            "artifact": result.get("artifact"),
            "artifact_type": result.get("artifact_type"),
            **meta,
        }
        conversation_storage.append_message(
            conversation_id,
            role="assistant",
            content=result.get("output", "") or "生成完成",
            citations=result.get("citations") or [],
            extra=extra,
        )
    except Exception as e:
        logger.exception(f"[ResultPersistence] assistant 结果落库失败 conv={conversation_id}: {e}")


def persist_task_failure(conversation_id: Optional[str], error_message: str, agent_type: str = "supervisor") -> None:
    """任务失败：落一条 assistant 错误消息，保证历史对话不出现「有问无答」。"""
    if not conversation_id:
        return
    try:
        from app.services.chat.conversation_storage import conversation_storage

        meta = _agent_meta(agent_type)
        conversation_storage.append_message(
            conversation_id,
            role="assistant",
            content=f"抱歉，{friendly_error_message(error_message)}",
            extra={**meta, "task_failed": True},
        )
    except Exception as e:
        logger.exception(f"[ResultPersistence] 失败消息落库失败 conv={conversation_id}: {e}")


def build_done_payload(conversation_id: Optional[str], result: Dict[str, Any]) -> Dict[str, Any]:
    """组装 done 事件 payload：权威终态，前端据此对打字机内容做一次性定稿。"""
    return {
        "output": result.get("output", ""),
        "citations": result.get("citations", []),
        "agent_type": result.get("agent_type"),
        "artifact": result.get("artifact"),
        "artifact_type": result.get("artifact_type"),
        "plan_dag": result.get("plan_dag"),
        "sub_results": result.get("sub_results"),
        "trace_summary": result.get("trace_summary") or {},
        "conversation_id": conversation_id,
    }
