import os
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    PROJECT_NAME: str = "EduAgent-Platform"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    API_V1_STR: str = "/api/v1"
    
    # Security & Auth
    SECRET_KEY: str = "super-secret-key-please-change-in-production-min-32-chars"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day
    ALGORITHM: str = "HS256"
    BACKEND_CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000", "*"]

    # Alibaba Bailian (DashScope)
    # 真实密钥必须从 .env 读取，不要硬编码到代码里（安全最佳实践）
    DASHSCOPE_API_KEY: str = Field(default="", env="DASHSCOPE_API_KEY")
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    DEFAULT_LLM_MODEL: str = "qwen3.5-plus"        # 思维链模型，自动 reasoning_tokens
    ROUTER_HEAVY_MODEL: str = "qwen-max"            # 高复杂度任务
    ROUTER_LIGHT_MODEL: str = "qwen-turbo"          # 意图分类等轻量任务
    DEFAULT_EMBEDDING_MODEL: str = "text-embedding-v3"
    EMBEDDING_DIMENSION: int = 1024

    # MinerU Official API (Magic-PDF Cloud)
    # 真实密钥必须从 .env 读取
    MINERU_API_TOKEN: str = Field(default="", env="MINERU_API_TOKEN")
    MINERU_API_BASE: str = "https://mineru.net/api/v4"
    MINERU_MODEL_VERSION: str = "vlm"               # vlm 模式（视觉语言模型）

    # BGE Cross-Encoder Reranker
    # 真 BGE 重排：默认 BAAI/bge-reranker-base（~278MB，中英文，CPU 可跑）
    # 若需更强多语言可改 BAAI/bge-reranker-v2-m3（~568MB）
    # 国内下载模型需设环境变量 HF_ENDPOINT=https://hf-mirror.com
    RERANKER_MODEL_NAME: str = "BAAI/bge-reranker-base"
    RERANKER_ENABLE_BGE: bool = True        # 关闭则直接走启发式降级
    RERANKER_BATCH_SIZE: int = 32           # CrossEncoder 批量推理批大小
    RERANKER_CACHE_DIR: Optional[str] = None  # None=使用 HF 默认缓存（~/.cache/huggingface）

    # Milvus 2.4 Vector DB
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_USER: str = ""
    MILVUS_PASSWORD: str = ""
    MILVUS_COLLECTION: str = "edu_knowledge_chunks"

    # PostgreSQL Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password123@localhost:5432/edu_agent_db"

    # Redis Cache & Broker
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None
    REDIS_DB: int = 0

    # MinIO / Object Storage
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET_NAME: str = "edu-agent-artifacts"
    MINIO_SECURE: bool = False

    # Langfuse Observability
    LANGFUSE_PUBLIC_KEY: Optional[str] = None
    LANGFUSE_SECRET_KEY: Optional[str] = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # Agent Harness Limits & Guardrails
    AGENT_MAX_STEPS: int = 15
    AGENT_MAX_TOKEN_BUDGET: int = 16000
    AGENT_TIMEOUT_SECONDS: int = 120
    HITL_AUTO_APPROVE: bool = False

    # 外部调用超时强杀 Watchdog（秒）
    LLM_CALL_TIMEOUT: int = 90          # 单次非流式 LLM 调用硬超时（qwen3.5 带思维链较慢）
    LLM_STREAM_IDLE_TIMEOUT: int = 30   # 流式：两个 token 之间最大间隔
    LLM_STREAM_TOTAL_TIMEOUT: int = 150 # 流式：整次生成总截止时间
    EMBEDDING_CALL_TIMEOUT: int = 35    # Embedding 调用硬超时
    SANDBOX_TOTAL_TIMEOUT: int = 20     # 代码沙箱整次执行硬超时

    # 多智能体协作
    MAX_REFLECT_RETRIES: int = 2        # Reflect 回退补执行上限
    # 上下文压缩水位（占 token 预算比例）与保留策略
    CONTEXT_WATERMARK_L1: float = 0.7   # L1 规则裁剪
    CONTEXT_WATERMARK_L2: float = 0.8   # L2 滚动摘要
    CONTEXT_WATERMARK_L3: float = 0.9   # L3 归档召回
    CONTEXT_KEEP_RECENT: int = 3        # 最近 N 轮原文不压缩
    CONTEXT_ARCHIVE_TTL: int = 604800   # 归档原文保留 7 天（对齐 checkpointer）

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "allow"


settings = Settings()
