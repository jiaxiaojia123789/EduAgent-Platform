"""
EduAgent-Platform FastAPI 入口
================================
职责：
1. 启动 structlog 结构化日志（全局生效，含旧 logger）
2. 安装 RequestContextMiddleware 注入 request_id/user_id/trace_id
3. 初始化 OpenTelemetry（若已配置 OTLP 导出端点）
4. 装配 OpenAPI 文档（标签分组 + 错误响应示例 + 通用错误码）
5. 生命周期：Milvus / Redis 连接与降级
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.api.v1.api_router import api_v1_router
from app.core.metrics import router as metrics_router
from app.services.rag.milvus_manager import milvus_manager
from app.core.redis_client import redis_manager

# 早于 FastAPI 装配：日志先就绪，启动期错误也能结构化输出
configure_logging(environment=settings.ENVIRONMENT, debug=settings.DEBUG)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting_application", project=settings.PROJECT_NAME, env=settings.ENVIRONMENT)

    # 1. OpenTelemetry（可选：未配置 OTLP 端点时跳过，避免生产环境无 collector 报错）
    try:
        from app.core.telemetry import init_telemetry, shutdown_telemetry
        init_telemetry(app, endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT)
        logger.info("telemetry_initialized", exporter=settings.OTEL_EXPORTER_OTLP_ENDPOINT or "stdout")
    except Exception as e:
        logger.warning("telemetry_init_failed", error=str(e))

    # 2. Connect to Milvus 2.4
    try:
        milvus_manager.connect()
    except Exception as e:
        logger.warning("milvus_connect_failed_fallback", error=str(e))

    # 3. Connect to Redis
    await redis_manager.connect()

    # 4. P4-21: 注册内置 A/B 实验（默认关闭，仅暴露能力）
    try:
        from app.services.agent.ab_experiment import auto_register_default_experiments
        auto_register_default_experiments()
    except Exception as e:
        logger.warning("ab_experiments_register_failed", error=str(e))

    yield

    # 5. 关闭连接池与遥测
    await redis_manager.disconnect()
    try:
        from app.core.telemetry import shutdown_telemetry
        shutdown_telemetry()
    except Exception:
        pass

    logger.info("application_shutdown", project=settings.PROJECT_NAME)


# ============================================================
# OpenAPI 标签分组（/docs 侧边栏按业务域分组，便于查找）
# ============================================================
OPENAPI_TAGS = [
    {"name": "Authentication", "description": "用户注册、登录、JWT 鉴权"},
    {"name": "Agent Workflow Engine", "description": "教学智能体编排：异步任务、SSE 流式、HITL 审批、图可视化"},
    {"name": "Conversations", "description": "历史对话管理与消息回放"},
    {"name": "Knowledge Base", "description": "知识库与文档管理（MinerU 解析 + Milvus 索引）"},
    {"name": "Artifacts", "description": "生成的教案/试卷/课件等制品的查询与导出"},
    {"name": "MCP & Skills", "description": "MCP 工具与 Skill 调用"},
    {"name": "Memory", "description": "长期教学画像与记忆管理"},
    {"name": "Experimentation", "description": "A/B 实验配置与统计（LangGraph 节点变体分流）"},
]

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "教育垂类 AI 中台：基于 LangGraph 多智能体编排、Agent Harness 护栏、"
        "MinerU 混合 RAG 与 Milvus 2.4。\n\n"
        "## 鉴权\n"
        "- 除 `/healthz` `/readyz` `/docs` 外所有端点需 Bearer JWT\n"
        "- API Key 兼容：`Authorization: Bearer <api_key>`\n"
        "- 开发环境匿名访问：`X-User-Id: u-001`（仅 dev/CI 生效）\n\n"
        "## 错误响应\n"
        "所有 4xx/5xx 响应统一结构：`{detail: {code, message, details?}, request_id}`"
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=OPENAPI_TAGS,
    contact={
        "name": "EduAgent Team",
        "url": "https://github.com/your-org/ai_education",
    },
    license_info={
        "name": "MIT",
        "url": "https://opensource.org/licenses/MIT",
    },
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# RequestContextMiddleware：注入 request_id/user_id/trace_id
# 必须晚于 CORS（CORS 需处理预检），但早于路由分发
from app.middleware.request_context import install_request_context  # noqa: E402
from app.middleware.tenant_context import install_tenant_context  # noqa: E402

install_request_context(app)
install_tenant_context(app)

# 业务路由
app.include_router(api_v1_router, prefix=settings.API_V1_STR)

# Prometheus 指标端点（无需认证，便于 Prometheus 抓取）
app.include_router(metrics_router)


# ============================================================
# 自定义 OpenAPI schema：注入通用错误响应示例
# ============================================================
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=OPENAPI_TAGS,
    )

    # 给所有路径挂上 401/403/500 错误响应示例
    error_response_ref = {"$ref": "#/components/schemas/ErrorResponse"}
    for _, path_item in openapi_schema.get("paths", {}).items():
        for method, op in path_item.items():
            if method not in {"get", "post", "put", "delete", "patch"}:
                continue
            responses = op.setdefault("responses", {})
            for code in ("401", "403", "500"):
                responses.setdefault(
                    code,
                    {
                        "description": {
                            "401": "未认证或 token 失效",
                            "403": "无权限访问",
                            "500": "服务内部错误",
                        }[code],
                        "content": {
                            "application/json": {"schema": error_response_ref}
                        },
                    },
                )

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


# ============================================================
# 健康检查 / 元信息端点（无需认证）
# ============================================================
@app.get("/", tags=["Meta"])
async def root():
    return {
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
        "status": "online",
        "milvus_connected": milvus_manager.is_connected,
        "redis_connected": redis_manager.is_connected,
    }


@app.get("/healthz", tags=["Meta"], summary="存活探针（liveness）")
async def healthz():
    """Kubernetes liveness probe：进程活着即返回 200。"""
    return {
        "status": "healthy",
        "milvus": "connected" if milvus_manager.is_connected else "fallback_mode",
        "redis": "connected" if redis_manager.is_connected else "fallback_mode",
        "environment": settings.ENVIRONMENT,
    }


@app.get("/readyz", tags=["Meta"], summary="就绪探针（readiness）")
async def readyz():
    """Kubernetes readiness probe：依赖就绪才返回 200，否则 503 让流量切走。"""
    dependencies_ok = milvus_manager.is_connected and redis_manager.is_connected
    return {
        "ready": dependencies_ok,
        "milvus": "connected" if milvus_manager.is_connected else "fallback_mode",
        "redis": "connected" if redis_manager.is_connected else "fallback_mode",
    }


@app.get("/api/v1/agents/experiments", tags=["Experimentation"], summary="A/B 实验统计")
async def experiments_stats():
    """P4-21: 查看所有 A/B 实验的变体执行结果聚合。"""
    from app.services.agent.ab_experiment import experiment_registry
    return {
        "experiments": experiment_registry.stats(),
        "configs": [
            {
                "name": e.name,
                "enabled": e.enabled,
                "variants": [
                    {"name": v.name, "weight": v.weight, "prompt_id": v.prompt_id, "agent": v.agent}
                    for v in e.variants
                ],
            }
            for e in experiment_registry._experiments.values()
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
