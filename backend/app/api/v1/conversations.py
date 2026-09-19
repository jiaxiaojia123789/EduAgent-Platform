"""
历史对话 API
- GET    /conversations            按 agent 列出当前用户的历史对话
- GET    /conversations/{id}       获取对话详情（含全部消息，用于恢复）
- DELETE /conversations/{id}       删除对话
"""
from fastapi import APIRouter, HTTPException, Query

from app.services.chat.conversation_storage import conversation_storage

router = APIRouter(prefix="/conversations", tags=["Conversation History"])


@router.get("")
async def list_conversations(
    agent_type: str = Query(default="supervisor", description="智能体类型，用于隔离历史"),
    user_id: str = Query(default="u-001"),
):
    """列出指定 agent 下的历史对话（最近更新在前）"""
    items = conversation_storage.list_sessions(user_id=user_id, agent_type=agent_type)
    return {"agent_type": agent_type, "total": len(items), "conversations": items}


@router.get("/{session_id}")
async def get_conversation(session_id: str):
    """获取对话详情：会话元数据 + 全部消息（恢复窗口用）"""
    session = conversation_storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="对话不存在或已被删除")
    messages = conversation_storage.list_messages(session_id)
    return {"session": session, "messages": messages}


@router.delete("/{session_id}")
async def delete_conversation(session_id: str):
    """删除一条历史对话（连带删除其全部消息）"""
    ok = conversation_storage.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="对话不存在或已被删除")
    return {"deleted": True, "session_id": session_id}
