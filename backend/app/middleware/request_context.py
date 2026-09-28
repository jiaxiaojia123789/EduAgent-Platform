"""
RequestContextMiddleware
========================
注入关联 ID 与用户上下文到每个 HTTP 请求，配合 structlog 让所有日志可串联：

工作流：
1. 入站：读取 X-Request-ID 头（无则生成 UUID），写入 contextvar 与响应头
2. 认证后：从 request.state.user / JWT 解析出 user_id、user_role，再绑定
3. 出站：响应头回传 X-Request-ID，便于前端排查问题时直接报 RID 给运维
4. 异常：捕获未处理异常并打 error 日志（带 request_id），避免日志散落

性能：纯内存 contextvar，无外部依赖，亚毫秒级开销。
"""
import time
import uuid
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from app.core.logging import (
    bind_request_context,
    clear_request_context,
    get_logger,
)

logger = get_logger(__name__)

_REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    注入 request_id / user_id / user_role 到 contextvar，
    让下游所有 structlog 调用自动带上，且响应头回传 X-Request-ID。
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # 1. 解析或生成 request_id
        request_id = request.headers.get(_REQUEST_ID_HEADER) or str(uuid.uuid4())

        # 2. 从已认证的 request.state 提取用户（认证中间件已写入）
        user_id = getattr(request.state, "user_id", None) or getattr(
            request.state, "user", None
        )
        user_role = getattr(request.state, "user_role", None)

        bind_request_context(
            request_id=request_id,
            user_id=str(user_id) if user_id else None,
            user_role=user_role,
        )

        # 3. 给 ASGI scope 也打个标记，下游（SSE 推送）可读取
        request.state.request_id = request_id

        start = time.monotonic()
        status_code = 500
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            # 4. 响应头回传 X-Request-ID，前端排查可直接报 RID
            response.headers[_REQUEST_ID_HEADER] = request_id
            return response
        except Exception as exc:
            logger.exception(
                "unhandled_exception",
                method=request.method,
                path=request.url.path,
                error=str(exc),
            )
            raise
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            # 4xx/5xx 单独 warn，便于监控告警按 level=WARNING 抓异常请求
            log_fn = (
                logger.warning if status_code >= 400 else logger.info
            )
            log_fn(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=status_code,
                elapsed_ms=elapsed_ms,
                client_host=request.client.host if request.client else "",
            )
            clear_request_context()


def install_request_context(app: ASGIApp) -> None:
    """供 main.py 调用，注册中间件。"""
    app.add_middleware(RequestContextMiddleware)
