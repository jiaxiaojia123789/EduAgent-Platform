import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.api_router import api_v1_router
from app.services.rag.milvus_manager import milvus_manager
from app.core.redis_client import redis_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.PROJECT_NAME} (Environment: {settings.ENVIRONMENT})")

    # 1. Connect to Milvus 2.4
    try:
        milvus_manager.connect()
    except Exception as e:
        logger.warning(f"Milvus 连接失败，进入降级模式: {e}")

    # 2. Connect to Redis（异步任务队列、幂等锁、SSE Pub/Sub 后端）
    await redis_manager.connect()

    yield

    # 3. 关闭 Redis 连接池
    await redis_manager.disconnect()

    logger.info(f"Shutting down {settings.PROJECT_NAME}")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="教育垂类 AI 中台：基于 LangGraph 多智能体、Agent Harness 护栏、MinerU 混合 RAG 与 Milvus 2.4",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include v1 API Router
app.include_router(api_v1_router, prefix=settings.API_V1_STR)


@app.get("/")
async def root():
    return {
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
        "status": "online",
        "milvus_connected": milvus_manager.is_connected,
        "redis_connected": redis_manager.is_connected,
    }


@app.get("/healthz")
async def healthz():
    return {
        "status": "healthy",
        "milvus": "connected" if milvus_manager.is_connected else "fallback_mode",
        "redis": "connected" if redis_manager.is_connected else "fallback_mode",
        "environment": settings.ENVIRONMENT
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
