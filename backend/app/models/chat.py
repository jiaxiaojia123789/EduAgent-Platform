import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, Text, JSON, ForeignKey
from app.core.database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False, index=True)
    title = Column(String(255), default="新对话", nullable=False)
    agent_type = Column(String(64), default="supervisor", nullable=False)  # supervisor, lesson_plan, etc.
    thread_id = Column(String(64), unique=True, index=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(32), nullable=False)  # user, assistant, system, tool
    content = Column(Text, nullable=False)
    thought_process = Column(Text, nullable=True)  # Deep Thinking traces, collapsed in Doubao UI
    citations = Column(JSON, default=list)  # Referenced chunks: [{title, page, snippet, score}]
    token_usage = Column(JSON, default=dict)  # {prompt_tokens, completion_tokens, total_tokens}
    # 扩展数据：{plan_dag, sub_results, artifact, artifact_type, agent_name, agent_avatar}
    extra = Column(JSON, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
