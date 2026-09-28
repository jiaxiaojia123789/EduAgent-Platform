"""
多租户隔离服务
================
教学 SaaS 场景：多个学校/机构共用同一实例，但数据互相隔离。

设计：
- TenantContext：contextvar 携带 tenant_id，全链路可访问
- TenantQuotaService：按租户配额限流（每日 LLM 调用次数 / token 总量）
- 数据隔离：所有业务表加 tenant_id 列，查询时强制 WHERE tenant_id = ?
  （生产环境配合 PostgreSQL Row-Level Security 更强；当前用 ORM 层过滤）

使用方式：
    # 中间件
    @app.middleware("http")
    async def tenant_middleware(request, call_next):
        tenant_id = request.headers.get("X-Tenant-ID") or "default"
        bind_tenant_context(tenant_id, user_id)
        response = await call_next(request)
        clear_tenant_context()
        return response

    # 业务代码
    from app.services.tenant.tenant_service import get_current_tenant
    tenant_id = get_current_tenant()
    session.query(ChatSession).filter(
        ChatSession.tenant_id == tenant_id,
        ChatSession.user_id == user_id,
    ).all()
"""
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.core.redis_client import redis_manager

logger = logging.getLogger(__name__)


# ============================================================
# 租户上下文（contextvar，asyncio 安全）
# ============================================================

_tenant_id_ctx: ContextVar[Optional[str]] = ContextVar("tenant_id", default=None)
_tenant_quota_ctx: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "tenant_quota", default=None
)


def bind_tenant_context(
    tenant_id: str,
    user_id: Optional[str] = None,
    quota: Optional[Dict[str, Any]] = None,
) -> None:
    """在请求中间件中调用一次，绑定当前请求的租户上下文。"""
    _tenant_id_ctx.set(tenant_id)
    if quota:
        _tenant_quota_ctx.set(quota)


def get_current_tenant() -> str:
    """获取当前请求的 tenant_id（默认 'default'）。"""
    return _tenant_id_ctx.get() or "default"


def get_current_quota() -> Optional[Dict[str, Any]]:
    """获取当前租户的配额配置（来自 tenant_quotas 表）。"""
    return _tenant_quota_ctx.get()


def clear_tenant_context() -> None:
    """请求结束清理。"""
    _tenant_id_ctx.set(None)
    _tenant_quota_ctx.set(None)


# ============================================================
# 租户配额服务
# ============================================================

@dataclass
class TenantQuota:
    """单租户配额配置。"""
    tenant_id: str
    name: str
    daily_llm_calls_limit: int = 1000         # 每日 LLM 调用上限
    daily_token_limit: int = 2_000_000       # 每日 token 总量上限
    max_concurrent_tasks: int = 5            # 并发任务上限
    allowed_models: list = field(default_factory=list)  # 允许的模型列表（空 = 全部）
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "name": self.name,
            "daily_llm_calls_limit": self.daily_llm_calls_limit,
            "daily_token_limit": self.daily_token_limit,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "allowed_models": self.allowed_models,
            "enabled": self.enabled,
        }


# 内置默认配额（生产环境应放 DB 表 tenant_quotas 管理）
_DEFAULT_QUOTA = TenantQuota(
    tenant_id="default",
    name="Default Tenant",
    daily_llm_calls_limit=int(os.environ.get("TENANT_DEFAULT_DAILY_CALLS", "1000")),
    daily_token_limit=int(os.environ.get("TENANT_DEFAULT_DAILY_TOKENS", "2000000")),
    max_concurrent_tasks=int(os.environ.get("TENANT_DEFAULT_MAX_CONCURRENT", "5")),
    allowed_models=[],  # 空 = 全部允许
    enabled=True,
)


class TenantQuotaService:
    """
    租户配额查询与计数：
    - get_quota(tenant_id)：返回配额配置（Redis 缓存，TTL 5min）
    - incr_usage(tenant_id, tokens)：累加当日用量，返回 [new_calls, new_tokens]
    - check_quota(tenant_id)：检查是否超限，返回 (ok, reason)
    """

    def __init__(self):
        self._quota_cache: Dict[str, TenantQuota] = {"default": _DEFAULT_QUOTA}

    async def get_quota(self, tenant_id: str) -> TenantQuota:
        """获取租户配额（先内存 → Redis → 默认）。"""
        if tenant_id in self._quota_cache:
            return self._quota_cache[tenant_id]

        # Redis 拿（生产环境从 tenant_quotas 表同步）
        if redis_manager.is_connected and redis_manager.client:
            try:
                raw = await redis_manager.client.get(f"tenant:quota:{tenant_id}")
                if raw:
                    import json
                    data = json.loads(raw)
                    quota = TenantQuota(**data)
                    self._quota_cache[tenant_id] = quota
                    return quota
            except Exception as e:
                logger.debug(f"[TenantQuota] Redis 拿配额失败: {e}")

        # 兜底：返回默认配额
        return _DEFAULT_QUOTA

    async def incr_usage(self, tenant_id: str, tokens: int = 0) -> tuple:
        """
        累加当日用量，返回 [new_calls, new_tokens]。
        Redis hash: tenant:usage:{tenant_id}:{date} = {calls: N, tokens: M}
        TTL 36h（跨天后自动失效）
        """
        from datetime import date
        today = date.today().isoformat()
        key = f"tenant:usage:{tenant_id}:{today}"

        if not (redis_manager.is_connected and redis_manager.client):
            # 无 Redis：无法统计，返回 0/0 表示不限制
            return (0, 0)

        try:
            pipe = redis_manager.client.pipeline()
            pipe.hincrby(key, "calls", 1)
            pipe.hincrby(key, "tokens", int(tokens))
            pipe.expire(key, 36 * 3600)
            results = await pipe.execute()
            return (results[0], results[1])
        except Exception as e:
            logger.warning(f"[TenantQuota] 用量累加失败 tenant={tenant_id}: {e}")
            return (0, 0)

    async def check_quota(self, tenant_id: str) -> tuple:
        """检查租户当日是否超限，返回 (ok, reason)。"""
        quota = await self.get_quota(tenant_id)
        if not quota.enabled:
            return (False, "租户已被禁用，请联系管理员")

        if not (redis_manager.is_connected and redis_manager.client):
            # 无 Redis 视为不限制
            return (True, "")

        from datetime import date
        today = date.today().isoformat()
        key = f"tenant:usage:{tenant_id}:{today}"

        try:
            usage = await redis_manager.client.hgetall(key)
            calls = int(usage.get("calls", 0))
            tokens = int(usage.get("tokens", 0))

            if calls >= quota.daily_llm_calls_limit:
                return (False, f"日 LLM 调用次数超限：{calls}/{quota.daily_llm_calls_limit}")
            if tokens >= quota.daily_token_limit:
                return (False, f"日 token 总量超限：{tokens}/{quota.daily_token_limit}")
            return (True, "")
        except Exception as e:
            logger.warning(f"[TenantQuota] 配额检查失败 tenant={tenant_id}: {e}")
            return (True, "")  # 检查失败放行，避免影响主流程

    async def set_quota(self, tenant_id: str, quota: TenantQuota) -> None:
        """更新租户配额（运维操作，会同步 Redis + 内存缓存）。"""
        self._quota_cache[tenant_id] = quota
        if redis_manager.is_connected and redis_manager.client:
            try:
                import json
                await redis_manager.client.set(
                    f"tenant:quota:{tenant_id}",
                    json.dumps(quota.to_dict()),
                    ex=300,  # 5min TTL
                )
            except Exception as e:
                logger.warning(f"[TenantQuota] 配额写入失败: {e}")


# 单例
tenant_quota_service = TenantQuotaService()
