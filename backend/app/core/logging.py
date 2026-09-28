"""
结构化日志 + 关联 ID 注入
================================
使用 structlog 输出 JSON 格式日志，并在每条日志中自动注入：
- request_id：单次 HTTP 请求唯一 ID（响应头回传 X-Request-ID）
- trace_id：OpenTelemetry 分布式追踪 ID（若已初始化）
- user_id：当前认证用户（若已认证）
- user_role：当前用户角色

设计要点：
1. contextvar 携带请求级元数据，asyncio 安全
2. 标准 logging 与 structlog 双向桥接，既有 logger 调用无需改造
3. 兼容 CI/开发环境：未配置 OTel 时 trace_id 自动回退到 request_id
4. LangGraph 节点可通过 config["configurable"] 注入相同 request_id，
   实现「HTTP 请求 → agent 节点 → LLM 调用」全链路关联
"""
import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any, Dict, Optional

import structlog

# ============================================================
# 请求级上下文变量（contextvar，asyncio 安全）
# ============================================================

_request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_user_id_ctx: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
_user_role_ctx: ContextVar[Optional[str]] = ContextVar("user_role", default=None)
_trace_id_ctx: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)


def get_request_id() -> Optional[str]:
    """供下游（LangGraph config / SSE 事件）取用，回退到 trace_id。"""
    return _request_id_ctx.get() or _trace_id_ctx.get()


def get_user_id() -> Optional[str]:
    return _user_id_ctx.get()


def bind_request_context(
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_role: Optional[str] = None,
) -> str:
    """
    在请求入口（中间件）调用一次，后续该请求所有日志自动带上。
    返回最终使用的 request_id（无传入则新生成）。
    """
    rid = request_id or str(uuid.uuid4())
    _request_id_ctx.set(rid)
    if user_id is not None:
        _user_id_ctx.set(user_id)
    if user_role is not None:
        _user_role_ctx.set(user_role)

    # 同步到 structlog 上下文，便于 logger.bind 自动带值
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=rid,
        user_id=user_id,
        user_role=user_role,
    )
    return rid


def bind_trace_id(trace_id: Optional[str]) -> None:
    """OpenTelemetry span 启动时调用，让日志带上同一 trace_id。"""
    _trace_id_ctx.set(trace_id)
    structlog.contextvars.bind_contextvars(trace_id=trace_id)


def clear_request_context() -> None:
    """请求结束时清理，避免 contextvar 泄漏到下个请求（FastAPI 中间件 finally 调用）。"""
    _request_id_ctx.set(None)
    _user_id_ctx.set(None)
    _user_role_ctx.set(None)
    _trace_id_ctx.set(None)
    structlog.contextvars.clear_contextvars()


# ============================================================
# structlog 配置（开发态控制台 / 生产态 JSON）
# ============================================================

def _shared_processors() -> list:
    """所有环境共用的处理链（顺序敏感）。"""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def configure_logging(environment: str = "development", debug: bool = False) -> None:
    """
    在应用启动时（main.py lifespan）调用一次，全局生效。

    - 生产/CI：JSON 输出，便于 ELK/Loki 采集
    - 开发：彩色控制台，便于人眼阅读
    - 同时桥接标准 logging，旧 logger 调用也走同一渲染器
    """
    is_prod = environment.lower() in {"production", "prod", "ci"}

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if is_prod
        else structlog.dev.ConsoleRenderer(colors=not is_prod)
    )

    processors = _shared_processors() + [renderer]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if debug else logging.INFO
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 桥接：标准 logging → structlog 渲染器，旧代码 logger.info() 也输出结构化
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=_shared_processors(),
        )
    )
    root = logging.getLogger()
    # 移除可能存在的旧 handler，避免重复输出
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # 抑制过于吵闹的第三方库日志
    for noisy in ("httpx", "httpcore", "asyncio", "sentence_transformers", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str = __name__) -> Any:
    """获取 structlog logger，自动带 request_id 等上下文。"""
    return structlog.get_logger(name)
