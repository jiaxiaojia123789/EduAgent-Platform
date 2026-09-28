"""
TenantContextMiddleware
=======================
从请求头/URL 路径/JWT claim 提取 tenant_id，绑定到 contextvar，
让下游所有代码可通过 get_current_tenant() 拿到当前租户。

提取优先级：
1. JWT claim 里的 tenant_id（认证后从 token 解析）
2. X-Tenant-ID 请求头（仅 dev/staging 允许，生产必须走 JWT）
3. URL 路径前缀 /t/{tenant_id}/...（可选）
4. 兜底 'default'（单租户场景）

设计要点：
- 中间件晚于 RequestContextMiddleware 注册（同 chain 顺序倒序生效）
- 失败不阻断请求（用 default 兜底）
- 配额检查在请求入口做一次，超限直接 429 返回
"""
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.core.logging import get_logger
from app.services.tenant.tenant_service import (
    bind_tenant_context,
    clear_tenant_context,
    tenant_quota_service,
)

logger = get_logger(__name__)

# 不需要租户隔离的路径（健康检查、文档、metrics）
_EXEMPT_PATHS = {"/", "/healthz", "/readyz", "/docs", "/redoc", "/openapi.json", "/metrics"}


class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    注入 tenant_id + 配额检查到每个 HTTP 请求。

    超限场景：直接返回 429 Too Many Requests，避免消耗后端资源。
    """

    def __init__(self, app: ASGIApp, enforce_in_prod: bool = True) -> None:
        super().__init__(app)
        # 生产环境强制走 JWT claim，不接受 X-Tenant-ID 头
        self._enforce_in_prod = enforce_in_prod

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # 健康检查/文档/metrics 不需要租户上下文
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        # 1. 提取 tenant_id
        tenant_id = self._extract_tenant_id(request)

        # 2. 查配额（带缓存）
        quota = await tenant_quota_service.get_quota(tenant_id)

        # 3. 绑定上下文
        bind_tenant_context(tenant_id, quota=quota.to_dict())

        # 4. 配额检查（仅在非 GET 请求做，GET 不消耗 LLM 调用）
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and quota.enabled:
            ok, reason = await tenant_quota_service.check_quota(tenant_id)
            if not ok:
                logger.warning(
                    "tenant_quota_exceeded",
                    tenant_id=tenant_id,
                    reason=reason,
                    path=request.url.path,
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": {
                            "code": "TENANT_QUOTA_EXCEEDED",
                            "message": f"租户配额已超限：{reason}",
                        },
                        "request_id": getattr(request.state, "request_id", None),
                    },
                )

        try:
            return await call_next(request)
        finally:
            clear_tenant_context()

    def _extract_tenant_id(self, request: Request) -> str:
        """提取 tenant_id，优先级：JWT claim > X-Tenant-ID 头 > 路径前缀 > default。"""
        from app.core.config import settings

        # 1. JWT claim（认证中间件已解析到 request.state）
        tenant = getattr(request.state, "tenant_id", None)
        if tenant:
            return tenant

        # 2. X-Tenant-ID 头（仅 dev/staging 允许）
        if settings.ENVIRONMENT.lower() in {"development", "staging", "ci"} or not self._enforce_in_prod:
            tenant_header = request.headers.get("X-Tenant-ID")
            if tenant_header:
                return tenant_header

        # 3. URL 路径前缀 /t/{tenant_id}/...
        path_parts = request.url.path.strip("/").split("/", 2)
        if len(path_parts) >= 2 and path_parts[0] == "t":
            return path_parts[1]

        # 4. 兜底
        return "default"


def install_tenant_context(app: ASGIApp, enforce_in_prod: bool = True) -> None:
    """供 main.py 调用，注册中间件。"""
    app.add_middleware(
        TenantContextMiddleware,
        enforce_in_prod=enforce_in_prod,
    )
