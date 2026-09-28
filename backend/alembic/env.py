"""
Alembic 迁移环境
==================
工作方式：
1. 复用 app.core.config.settings.DATABASE_URL（与运行时同一来源）
2. async URL 转 sync（postgresql+asyncpg:// → postgresql://）以适配 alembic sync API
3. target_metadata 指向 app.core.database.Base.metadata，
   autogenerate 能自动对比 schema 与 DB 差异生成迁移脚本
4. offline/online两种模式：
   - offline：生成 SQL 脚本不连 DB（生产环境预演）
   - online：直接连 DB 应用迁移
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# 确保能 import app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402

# 重要：导入所有 model 模块，让 Base.metadata 知道所有表
from app.models import user, chat, knowledge, artifact  # noqa: F401, E402

config = context.config

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata：autogenerate 用此对比 DB schema
target_metadata = Base.metadata


def _resolve_sync_url() -> str:
    """
    把 async URL 转成 sync 版本：
    - postgresql+asyncpg://... → postgresql://...
    - sqlite+aiosqlite:///... → sqlite:///...
    """
    url = settings.DATABASE_URL
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("sqlite+aiosqlite://", "sqlite://")
    return url


# 动态注入 URL（覆盖 alembic.ini 的占位）
config.set_main_option("sqlalchemy.url", _resolve_sync_url())


def run_migrations_offline() -> None:
    """
    Offline 模式：仅生成 SQL 脚本，不连接 DB
    使用场景：生产环境迁移预演
        alembic upgrade head --sql > preview.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Online 模式：连接 DB 应用迁移
    使用场景：开发/CI 环境直接应用
        alembic upgrade head
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
