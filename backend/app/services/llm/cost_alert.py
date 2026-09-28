"""
LLM 成本告警服务
================
基于 P4-20 租户配额与 P3-15 LLM 指标，实现成本告警与限流：

1. 实时计数：每次 LLM 调用后，按 tenant + user + model 累加 token 用量
2. 阈值告警：日累计 token / 估算成本超阈值 → 触发告警钩子
3. 多级响应：
   - warning（80% 阈值）：仅告警，记录到日志
   - critical（100% 阈值）：阻断后续调用，返回 429
   - budget_exceeded（>120%）：强制降级到便宜模型（如 qwen-turbo 替代 qwen-max）
4. 告警钩子：可注入自定义回调（飞书/钉钉/PagerDuty），默认仅 structlog

设计要点：
- 单位：token 是基础，估算成本按 model 单价换算（CNY/1K tokens）
- 阈值配置：per_tenant + per_user 两层，per_user 阈值默认更小
- 失败不阻断主流程（告警是 best-effort）
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.core.config import settings
from app.core.redis_client import redis_manager
from app.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 模型单价表（CNY / 1K tokens，参考各厂商 2024 公开价格）
# ============================================================

MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # 阿里云 DashScope
    "qwen-max": {"prompt": 0.040, "completion": 0.120},
    "qwen-plus": {"prompt": 0.0008, "completion": 0.002},
    "qwen3.5-plus": {"prompt": 0.0008, "completion": 0.002},
    "qwen-turbo": {"prompt": 0.0003, "completion": 0.0006},
    "qwen3-turbo": {"prompt": 0.0003, "completion": 0.0006},
    "text-embedding-v3": {"prompt": 0.0007, "completion": 0.0},
    # OpenAI（参考）
    "gpt-4o": {"prompt": 0.018, "completion": 0.072},
    "gpt-4o-mini": {"prompt": 0.001, "completion": 0.003},
    # 默认（未知模型按 qwen-plus 估算）
    "_default": {"prompt": 0.0008, "completion": 0.002},
}


def estimate_cost_cny(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """估算单次调用的成本（CNY）。"""
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["_default"])
    return (
        (prompt_tokens / 1000) * pricing["prompt"]
        + (completion_tokens / 1000) * pricing["completion"]
    )


# ============================================================
# 告警级别
# ============================================================

class AlertLevel:
    INFO = "info"
    WARNING = "warning"          # 80% 阈值
    CRITICAL = "critical"        # 100% 阈值，应阻断
    BUDGET_EXCEEDED = "budget_exceeded"  # 120% 阈值，应降级


@dataclass
class CostAlert:
    """单次告警事件。"""
    level: str
    tenant_id: str
    user_id: Optional[str]
    scope: str  # "tenant" | "user"
    metric: str  # "tokens" | "cost_cny"
    current_value: float
    threshold: float
    usage_pct: float
    message: str


# 告警钩子类型
AlertHook = Callable[[CostAlert], Awaitable[None]]


# ============================================================
# 成本告警服务
# ============================================================

@dataclass
class CostThresholdConfig:
    """成本阈值配置。"""
    # 租户日累计阈值
    tenant_daily_token_warning: int = 1_600_000      # 80% of 2M
    tenant_daily_token_critical: int = 2_000_000    # 100%
    tenant_daily_cost_cny_warning: float = 80.0     # 80% of 100 CNY
    tenant_daily_cost_cny_critical: float = 100.0

    # 单用户日累计阈值
    user_daily_token_warning: int = 80_000
    user_daily_token_critical: int = 100_000
    user_daily_cost_cny_warning: float = 8.0
    user_daily_cost_cny_critical: float = 10.0

    # 触发降级的阈值（>120% critical）
    budget_exceeded_pct: float = 1.2

    # 强制降级到的便宜模型
    fallback_model: str = "qwen-turbo"


class CostAlertService:
    """
    成本告警服务：
    - record_usage：在 LLM 调用后记录 token / 估算成本
    - check_thresholds：检查是否超阈值，触发告警钩子
    - 应急降级：超 120% 时返回 fallback_model 供路由层切换
    """

    def __init__(
        self,
        config: Optional[CostThresholdConfig] = None,
        alert_hooks: Optional[List[AlertHook]] = None,
    ):
        self.config = config or CostThresholdConfig()
        self._hooks: List[AlertHook] = list(alert_hooks or [])
        # 内存缓存：当日已告警过的 (scope, tenant_id/user_id, level) 集合
        # 避免同一阈值短时间内重复告警（去重窗口）
        self._alerted: set = set()

    def add_hook(self, hook: AlertHook) -> None:
        """注册告警钩子（飞书/钉钉/PagerDuty 等）。"""
        self._hooks.append(hook)

    async def record_usage(
        self,
        tenant_id: str,
        user_id: Optional[str],
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        记录单次 LLM 调用的 token 用量，并触发阈值检查。
        返回 {cost_cny, alerts: [...], should_block: bool, fallback_model: Optional[str]}
        """
        total_tokens = prompt_tokens + completion_tokens
        cost_cny = estimate_cost_cny(model, prompt_tokens, completion_tokens)

        # Redis hash 累计：cost:usage:{tenant_id}:{user_id|tenant}:{date}
        today = date.today().isoformat()
        alerts: List[CostAlert] = []

        # 1. 租户维度累计
        tenant_usage = await self._incr_usage(
            key=f"cost:tenant:{tenant_id}:{today}",
            tokens=total_tokens,
            cost=cost_cny,
        )
        # 2. 用户维度累计（仅在 user_id 提供时）
        user_usage = None
        if user_id:
            user_usage = await self._incr_usage(
                key=f"cost:user:{tenant_id}:{user_id}:{today}",
                tokens=total_tokens,
                cost=cost_cny,
            )

        # 3. 阈值检查
        alerts.extend(self._check_tenant(tenant_id, tenant_usage))
        if user_usage:
            alerts.extend(self._check_user(tenant_id, user_id, user_usage))

        # 4. 触发告警钩子（best-effort）
        for alert in alerts:
            await self._trigger_hooks(alert)

        # 5. 判定是否应阻断 / 降级
        should_block = any(a.level == AlertLevel.CRITICAL for a in alerts)
        budget_exceeded = any(
            a.usage_pct >= self.config.budget_exceeded_pct
            for a in alerts
        )
        fallback_model = (
            self.config.fallback_model
            if budget_exceeded
            else None
        )

        return {
            "cost_cny": round(cost_cny, 4),
            "alerts": [a.__dict__ for a in alerts],
            "should_block": should_block,
            "fallback_model": fallback_model,
        }

    async def _incr_usage(self, key: str, tokens: int, cost: float) -> Dict[str, float]:
        """累加并返回新值。"""
        if not (redis_manager.is_connected and redis_manager.client):
            return {"tokens": 0.0, "cost_cny": 0.0}

        try:
            pipe = redis_manager.client.pipeline()
            pipe.hincrby(key, "tokens", tokens)
            pipe.hincrbyfloat(key, "cost_cny", cost)
            pipe.expire(key, 36 * 3600)
            results = await pipe.execute()
            return {"tokens": float(results[0]), "cost_cny": float(results[1])}
        except Exception as e:
            logger.warning(f"[CostAlert] 用量累加失败: {e}")
            return {"tokens": 0.0, "cost_cny": 0.0}

    def _check_tenant(self, tenant_id: str, usage: Dict[str, float]) -> List[CostAlert]:
        """检查租户维度阈值。"""
        alerts: List[CostAlert] = []
        tokens = usage.get("tokens", 0)
        cost = usage.get("cost_cny", 0)

        # token 阈值
        if tokens >= self.config.tenant_daily_token_critical:
            alerts.append(CostAlert(
                level=AlertLevel.CRITICAL,
                tenant_id=tenant_id,
                user_id=None,
                scope="tenant",
                metric="tokens",
                current_value=tokens,
                threshold=self.config.tenant_daily_token_critical,
                usage_pct=tokens / self.config.tenant_daily_token_critical,
                message=f"租户日 token 超限：{tokens}/{self.config.tenant_daily_token_critical}",
            ))
        elif tokens >= self.config.tenant_daily_token_warning:
            alerts.append(CostAlert(
                level=AlertLevel.WARNING,
                tenant_id=tenant_id,
                user_id=None,
                scope="tenant",
                metric="tokens",
                current_value=tokens,
                threshold=self.config.tenant_daily_token_warning,
                usage_pct=tokens / self.config.tenant_daily_token_critical,
                message=f"租户日 token 接近上限：{tokens}/{self.config.tenant_daily_token_critical}",
            ))

        # 成本阈值
        if cost >= self.config.tenant_daily_cost_cny_critical:
            alerts.append(CostAlert(
                level=AlertLevel.CRITICAL,
                tenant_id=tenant_id,
                user_id=None,
                scope="tenant",
                metric="cost_cny",
                current_value=cost,
                threshold=self.config.tenant_daily_cost_cny_critical,
                usage_pct=cost / self.config.tenant_daily_cost_cny_critical,
                message=f"租户日成本超限：¥{cost:.2f}/{self.config.tenant_daily_cost_cny_critical}",
            ))
        elif cost >= self.config.tenant_daily_cost_cny_warning:
            alerts.append(CostAlert(
                level=AlertLevel.WARNING,
                tenant_id=tenant_id,
                user_id=None,
                scope="tenant",
                metric="cost_cny",
                current_value=cost,
                threshold=self.config.tenant_daily_cost_cny_warning,
                usage_pct=cost / self.config.tenant_daily_cost_cny_critical,
                message=f"租户日成本接近上限：¥{cost:.2f}/{self.config.tenant_daily_cost_cny_critical}",
            ))

        return alerts

    def _check_user(
        self,
        tenant_id: str,
        user_id: Optional[str],
        usage: Dict[str, float],
    ) -> List[CostAlert]:
        """检查单用户维度阈值。"""
        alerts: List[CostAlert] = []
        tokens = usage.get("tokens", 0)
        cost = usage.get("cost_cny", 0)

        if tokens >= self.config.user_daily_token_critical:
            alerts.append(CostAlert(
                level=AlertLevel.CRITICAL,
                tenant_id=tenant_id,
                user_id=user_id,
                scope="user",
                metric="tokens",
                current_value=tokens,
                threshold=self.config.user_daily_token_critical,
                usage_pct=tokens / self.config.user_daily_token_critical,
                message=f"用户日 token 超限：{tokens}/{self.config.user_daily_token_critical}",
            ))
        elif tokens >= self.config.user_daily_token_warning:
            alerts.append(CostAlert(
                level=AlertLevel.WARNING,
                tenant_id=tenant_id,
                user_id=user_id,
                scope="user",
                metric="tokens",
                current_value=tokens,
                threshold=self.config.user_daily_token_warning,
                usage_pct=tokens / self.config.user_daily_token_critical,
                message=f"用户日 token 接近上限：{tokens}/{self.config.user_daily_token_critical}",
            ))

        if cost >= self.config.user_daily_cost_cny_critical:
            alerts.append(CostAlert(
                level=AlertLevel.CRITICAL,
                tenant_id=tenant_id,
                user_id=user_id,
                scope="user",
                metric="cost_cny",
                current_value=cost,
                threshold=self.config.user_daily_cost_cny_critical,
                usage_pct=cost / self.config.user_daily_cost_cny_critical,
                message=f"用户日成本超限：¥{cost:.2f}/{self.config.user_daily_cost_cny_critical}",
            ))

        return alerts

    async def _trigger_hooks(self, alert: CostAlert) -> None:
        """触发所有告警钩子（best-effort）。"""
        # 去重：同一 (scope, id, level, metric) 5 分钟内不重复告警
        dedup_key = (alert.scope, alert.tenant_id, alert.user_id, alert.level, alert.metric)
        if dedup_key in self._alerted:
            return
        self._alerted.add(dedup_key)
        # 5 分钟后清除（简化：进程内集合，重启清空）
        asyncio.get_event_loop().call_later(300, self._alerted.discard, dedup_key)

        logger.warning(
            "cost_alert_triggered",
            level=alert.level,
            tenant_id=alert.tenant_id,
            user_id=alert.user_id,
            scope=alert.scope,
            metric=alert.metric,
            current=alert.current_value,
            threshold=alert.threshold,
            usage_pct=round(alert.usage_pct, 4),
            message=alert.message,
        )

        for hook in self._hooks:
            try:
                ret = hook(alert)
                if asyncio.isawaitable(ret):
                    await ret
            except Exception as e:
                logger.warning(f"[CostAlert] 告警钩子执行失败: {e}")


# 单例
cost_alert_service = CostAlertService()


# ============================================================
# 内置告警钩子：飞书 Webhook（占位实现）
# ============================================================

async def feishu_webhook_hook(alert: CostAlert) -> None:
    """
    飞书 Webhook 告警钩子（占位实现，生产环境配置 FEISHU_WEBHOOK_URL 后启用）。
    """
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL")
    if not webhook_url:
        return  # 未配置直接跳过

    import httpx
    payload = {
        "msg_type": "text",
        "content": {
            "text": (
                f"[EduAgent 成本告警]\n"
                f"级别: {alert.level}\n"
                f"租户: {alert.tenant_id}\n"
                f"用户: {alert.user_id or '-'}\n"
                f"维度: {alert.scope}.{alert.metric}\n"
                f"当前: {alert.current_value}\n"
                f"阈值: {alert.threshold}\n"
                f"占比: {alert.usage_pct * 100:.1f}%\n"
                f"详情: {alert.message}"
            )
        },
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(webhook_url, json=payload)
    except Exception as e:
        logger.warning(f"[CostAlert] 飞书告警发送失败: {e}")


# 自动注册内置钩子
cost_alert_service.add_hook(feishu_webhook_hook)
