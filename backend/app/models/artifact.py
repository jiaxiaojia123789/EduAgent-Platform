import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, Text, JSON, ForeignKey
from app.core.database import Base


class GeneratedArtifact(Base):
    __tablename__ = "generated_artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False, index=True)
    session_id = Column(String(36), ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    artifact_type = Column(String(64), nullable=False)  # LESSON_PLAN, EXAM_PAPER, SLIDE_OUTLINE, MATH_PROOF
    title = Column(String(255), nullable=False)
    content_json = Column(JSON, nullable=False)  # Structured representation (Pydantic models)
    content_markdown = Column(Text, nullable=True)  # Markdown / LaTeX representation
    export_word_path = Column(String(512), nullable=True)
    export_pdf_path = Column(String(512), nullable=True)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
