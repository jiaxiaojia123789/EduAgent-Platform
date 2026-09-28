"""init schema: users / roles / chat_sessions / chat_messages / knowledge_bases / documents / document_chunks / generated_artifacts

Revision ID: 0001_init
Revises:
Create Date: 2026-09-28 00:00:00

基线版本：把现有 SQLAlchemy 模型对应的 schema 作为初始迁移。
对于已部署的环境，使用 `alembic stamp 0001_init` 标记当前为该版本，跳过 DDL；
新环境直接 `alembic upgrade head` 建表。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=128), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="teacher"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])

    # roles
    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("permissions", sa.Text(), nullable=True),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    # chat_sessions
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default="新对话"),
        sa.Column("agent_type", sa.String(length=64), nullable=False, server_default="supervisor"),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("thread_id", name="uq_chat_sessions_thread_id"),
    )
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])
    op.create_index("ix_chat_sessions_thread_id", "chat_sessions", ["thread_id"])

    # chat_messages
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("thought_process", sa.Text(), nullable=True),
        sa.Column("citations", sa.JSON(), server_default=sa.text("'[]'")),
        sa.Column("token_usage", sa.JSON(), server_default=sa.text("'{}'")),
        sa.Column("extra", sa.JSON(), server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])

    # knowledge_bases
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("embedding_model", sa.String(length=64), server_default="text-embedding-v3"),
        sa.Column("dimension", sa.Integer(), server_default="1024"),
        sa.Column("milvus_collection", sa.String(length=64), server_default="edu_knowledge_chunks"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_knowledge_bases_name", "knowledge_bases", ["name"])
    op.create_index("ix_knowledge_bases_owner_id", "knowledge_bases", ["owner_id"])

    # documents
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "kb_id",
            sa.String(length=36),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.Integer(), server_default="0"),
        sa.Column("minio_path", sa.String(length=512), nullable=True),
        sa.Column("parse_status", sa.String(length=32), server_default="PENDING"),
        sa.Column("parser_type", sa.String(length=32), server_default="MinerU"),
        sa.Column("chunk_count", sa.Integer(), server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_documents_kb_id", "documents", ["kb_id"])

    # document_chunks
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "doc_id",
            sa.String(length=36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kb_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tokens", sa.Integer(), server_default="0"),
        sa.Column("formula_count", sa.Integer(), server_default="0"),
        sa.Column("table_count", sa.Integer(), server_default="0"),
        sa.Column("page_number", sa.Integer(), server_default="1"),
        sa.Column("vector_id", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), server_default=sa.text("'{}'")),
    )
    op.create_index("ix_document_chunks_doc_id", "document_chunks", ["doc_id"])
    op.create_index("ix_document_chunks_kb_id", "document_chunks", ["kb_id"])
    op.create_index("ix_document_chunks_vector_id", "document_chunks", ["vector_id"])

    # generated_artifacts
    op.create_table(
        "generated_artifacts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=True),
        sa.Column("export_word_path", sa.String(length=512), nullable=True),
        sa.Column("export_pdf_path", sa.String(length=512), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_generated_artifacts_user_id", "generated_artifacts", ["user_id"])
    op.create_index("ix_generated_artifacts_session_id", "generated_artifacts", ["session_id"])


def downgrade() -> None:
    op.drop_table("generated_artifacts")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("knowledge_bases")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("roles")
    op.drop_table("users")
