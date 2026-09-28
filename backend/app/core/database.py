import warnings
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.core.config import settings

# Create Async Engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    [DEPRECATED] 启动期自动建表，已被 alembic 迁移工具替代。

    - 新部署：alembic upgrade head
    - 已部署的存量环境：alembic stamp 0001_init 标记基线，之后走 alembic

    此函数仅作为兜底逻辑保留（CI 无 alembic 时回退到 create_all），
    生产环境必须走 alembic，避免 schema 漂移。
    """
    warnings.warn(
        "init_db() 已被 alembic 替代，生产环境请使用 `alembic upgrade head`。"
        "本函数仅作为 CI/开发兜底使用。",
        DeprecationWarning,
        stacklevel=2,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
