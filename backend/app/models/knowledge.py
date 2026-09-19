import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, Text, JSON, Boolean, ForeignKey
from app.core.database import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(128), nullable=False, index=True)
    description = Column(Text, nullable=True)
    owner_id = Column(String(36), nullable=False, index=True)
    embedding_model = Column(String(64), default="text-embedding-v3")
    dimension = Column(Integer, default=1024)
    milvus_collection = Column(String(64), default="edu_knowledge_chunks")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Document(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kb_id = Column(String(36), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    file_size = Column(Integer, default=0)
    minio_path = Column(String(512), nullable=True)
    parse_status = Column(String(32), default="PENDING")  # PENDING, PARSING, SUCCESS, FAILED
    parser_type = Column(String(32), default="MinerU")
    chunk_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    doc_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    kb_id = Column(String(36), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    tokens = Column(Integer, default=0)
    formula_count = Column(Integer, default=0)
    table_count = Column(Integer, default=0)
    page_number = Column(Integer, default=1)
    vector_id = Column(String(64), nullable=True, index=True)  # ID in Milvus
    metadata_json = Column(JSON, default=dict)
