"""
OpenTelemetry 分布式追踪集成
==============================
能力：
1. 自动埋点 FastAPI 路由（每次 HTTP 请求生成 span）
2. 自动埋点 httpx（DashScope/MinerU/外部 HTTP 调用）→ 子 span
3. 自动埋点 SQLAlchemy（DB 查询）→ 子 span
4. 自动埋点 Redis（缓存/锁/Pub/Sub）→ 子 span
5. LangGraph 节点：通过 _with_node_metrics 已记录 elapsed_ms，
   进一步可在节点入口/出口包一层 span，与 chain span 串联

输出端：
- OTLP HTTP/gRPC 推送到 collector（Jaeger/Tempo/Honeycomb 等）
- 未配置 endpoint 时降级为 ConsoleSpanExporter（trace 打印到 stdout，
  便于开发态排错）

设计约束：
- 初始化在 lifespan 最早阶段（早于 Milvus/Redis 连接）
- 失败不阻断启动（遥测降级，业务优先）
- 与 structlog 联动：trace_id 写入 contextvar，让所有日志带同一 trace_id
"""
import logging
from typing import Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor

from app.core.config import settings
from app.core.logging import bind_trace_id, get_logger

logger = get_logger(__name__)

_tracer_provider: Optional[TracerProvider] = None


def _build_resource() -> Resource:
    """构建服务标识：service.name / deployment.environment 等。"""
    attrs = {
        "service.name": settings.OTEL_SERVICE_NAME,
        "service.version": settings.VERSION,
    }
    # 用户在 OTEL_RESOURCE_ATTRIBUTES 里写的 key=value,key=value 解析进去
    if settings.OTEL_RESOURCE_ATTRIBUTES:
        for kv in settings.OTEL_RESOURCE_ATTRIBUTES.split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                attrs[k.strip()] = v.strip()
    return Resource.create(attrs)


def init_telemetry(app, endpoint: Optional[str] = None) -> None:
    """
    初始化 TracerProvider 与自动埋点。

    endpoint:
    - 显式传入 → 走 OTLP HTTP 推送
    - None → 走 ConsoleSpanExporter（trace 打印到 stdout，开发态）
    - "none" → 不导出（仅生成无出口的 trace，最低开销）
    """
    global _tracer_provider

    if _tracer_provider is not None:
        logger.info("telemetry_already_initialized")
        return

    try:
        provider = TracerProvider(resource=_build_resource())

        exporter_kind = settings.OTEL_TRACES_EXPORTER.lower()
        if endpoint and exporter_kind == "otlp":
            exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("otel_exporter_otlp", endpoint=endpoint)
        elif exporter_kind == "console":
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            logger.info("otel_exporter_console")
        elif exporter_kind == "none":
            logger.info("otel_exporter_disabled")
        else:
            # 默认：开发态无 endpoint 也走 console
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            logger.info("otel_exporter_console_fallback")

        trace.set_tracer_provider(provider)
        _tracer_provider = provider

        # 自动埋点（任一失败仅告警，不阻断）
        try:
            FastAPIInstrumentor.instrument_app(app)
        except Exception as e:
            logger.warning("instrument_fastapi_failed", error=str(e))

        try:
            HTTPXClientInstrumentor().instrument()
        except Exception as e:
            logger.warning("instrument_httpx_failed", error=str(e))

        try:
            # 需要已创建 engine 才能埋点
            from app.core.database import engine
            SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
        except Exception as e:
            logger.warning("instrument_sqlalchemy_failed", error=str(e))

        try:
            RedisInstrumentor().instrument()
        except Exception as e:
            logger.warning("instrument_redis_failed", error=str(e))

        # 让当前 span 的 trace_id 同步到 structlog contextvar
        _install_trace_context_correlation()

        logger.info(
            "telemetry_ready",
            service=settings.OTEL_SERVICE_NAME,
            version=settings.VERSION,
            exporter=exporter_kind,
        )
    except Exception as e:
        logger.warning("telemetry_init_failed", error=str(e))


def _install_trace_context_correlation() -> None:
    """
    把 OTel 当前 span 的 trace_id 同步到 structlog contextvar，
    实现「同一请求的所有日志带同一 trace_id」。

    实现方式：用 OTel 的 SpanProcessor 在 span 启动时调 bind_trace_id。
    """
    from opentelemetry.sdk.trace import SpanProcessor, ReadableSpan

    class _LogCorrelationProcessor(SpanProcessor):
        def on_start(self, span, parent_context=None):
            try:
                trace_id_hex = f"{span.get_span_context().trace_id:032x}"
                bind_trace_id(trace_id_hex)
            except Exception:
                pass

        def on_end(self, span: ReadableSpan) -> None:
            pass

        def shutdown(self) -> None: pass

        def force_flush(self, timeout_millis: int = 30000) -> bool: return True

    if _tracer_provider is not None:
        _tracer_provider.add_span_processor(_LogCorrelationProcessor())


def get_tracer(name: str = __name__):
    """获取 tracer，供手动埋点使用（如 LangGraph 节点）。"""
    return trace.get_tracer(name)


def shutdown_telemetry() -> None:
    """应用关闭时刷出所有未导出的 span。"""
    global _tracer_provider
    if _tracer_provider is not None:
        try:
            _tracer_provider.force_flush()
            _tracer_provider.shutdown()
        except Exception as e:
            logger.warning("telemetry_shutdown_failed", error=str(e))
        finally:
            _tracer_provider = None
