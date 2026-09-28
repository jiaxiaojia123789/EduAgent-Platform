"""
Prometheus 指标端点
==================
导出 /metrics 端点，输出 Prometheus exposition format 文本。

数据源：
1. prompt_call_metrics 表（agent 维度聚合）：调用数、延迟 p50/p95、token 成本、缓存命中率
2. Redis 连接状态 / Milvus 连接状态 / LangGraph checkpointer 后端
3. 进程级基础指标（Python runtime_info）

设计：
- 不依赖 prometheus_client 库，避免额外依赖；手写文本格式即可
- 端点无需认证（与 /healthz/readyz 一致），便于 Prometheus 抓取
- 采集时序：每次抓取实时跑一次 SQL 聚合（数据量小，<100ms）
- 标签设计：agent / model 双维度，便于在 Grafana 按 agent 切片
"""
from typing import List
from fastapi import APIRouter, Response
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.services.rag.milvus_manager import milvus_manager
from app.core.redis_client import redis_manager

router = APIRouter(tags=["Metrics"])


def _fmt_gauge(name: str, help_text: str, value: float, labels: dict = None) -> str:
    """格式化 Prometheus gauge 指标行。"""
    label_str = ""
    if labels:
        parts = [f'{k}="{v}"' for k, v in labels.items()]
        label_str = "{" + ",".join(parts) + "}"
    return (
        f"# HELP {name} {help_text}\n"
        f"# TYPE {name} gauge\n"
        f"{name}{label_str} {value}\n"
    )


def _fmt_counter(name: str, help_text: str, value: float, labels: dict = None) -> str:
    """格式化 Prometheus counter 指标行。"""
    label_str = ""
    if labels:
        parts = [f'{k}="{v}"' for k, v in labels.items()]
        label_str = "{" + ",".join(parts) + "}"
    return (
        f"# HELP {name} {help_text}\n"
        f"# TYPE {name} counter\n"
        f"{name}{label_str} {value}\n"
    )


def _build_metrics_text() -> str:
    """生成 Prometheus exposition format 文本。"""
    lines: List[str] = []

    # ---- 基础服务信息 ----
    lines.append(_fmt_gauge(
        "edu_agent_info",
        "EduAgent service metadata",
        1.0,
        {"version": settings.VERSION, "env": settings.ENVIRONMENT},
    ))

    # ---- 依赖连接状态 ----
    lines.append(_fmt_gauge(
        "edu_agent_dependency_up",
        "Dependency connectivity (1=up, 0=fallback)",
        1.0 if milvus_manager.is_connected else 0.0,
        {"dependency": "milvus"},
    ))
    lines.append(_fmt_gauge(
        "edu_agent_dependency_up",
        "Dependency connectivity (1=up, 0=fallback)",
        1.0 if redis_manager.is_connected else 0.0,
        {"dependency": "redis"},
    ))

    # ---- LangGraph checkpointer 后端 ----
    try:
        from app.services.agent.teaching_graph import checkpointer_backend
        backend = checkpointer_backend()
    except Exception:
        backend = "unknown"
    lines.append(_fmt_gauge(
        "edu_agent_checkpointer_backend",
        "LangGraph checkpointer backend (1=redis, 0=memory)",
        1.0 if backend == "redis" else 0.0,
        {"backend": backend},
    ))

    # ---- Circuit Breaker 状态 ----
    try:
        from app.harness.circuit_breaker import all_breakers
        for breaker in all_breakers():
            state_val = 1.0 if breaker["state"] == "CLOSED" else (
                0.0 if breaker["state"] == "OPEN" else 0.5
            )
            lines.append(_fmt_gauge(
                "edu_agent_circuit_breaker_state",
                "Circuit breaker state (1=CLOSED, 0.5=HALF_OPEN, 0=OPEN)",
                state_val,
                {"breaker": breaker["name"]},
            ))
            lines.append(_fmt_gauge(
                "edu_agent_circuit_breaker_failures",
                "Circuit breaker consecutive failure count",
                breaker["failure_count"],
                {"breaker": breaker["name"]},
            ))
    except Exception:
        pass

    # ---- LLM 供应商路由状态 ----
    try:
        from app.services.llm.provider import get_llm_router
        router = get_llm_router()
        for route in router.stats():
            state_val = 1.0 if route["state"] == "CLOSED" else (
                0.0 if route["state"] == "OPEN" else 0.5
            )
            lines.append(_fmt_gauge(
                "edu_agent_llm_provider_state",
                "LLM provider circuit breaker state (1=CLOSED, 0.5=HALF_OPEN, 0=OPEN)",
                state_val,
                {"provider": route["provider"], "priority": route["priority"]},
            ))
            lines.append(_fmt_gauge(
                "edu_agent_llm_provider_failures",
                "LLM provider consecutive failure count",
                route["failure_count"],
                {"provider": route["provider"]},
            ))
    except Exception:
        pass

    # ---- LLM 调用指标（按 agent 维度聚合最近 24h）----
    try:
        from app.services.llm.prompt_metrics import prompt_metrics
        agent_stats = prompt_metrics.agent_stats(since_hours=24)
    except Exception:
        agent_stats = []

    for stat in agent_stats:
        agent = stat.get("agent", "unknown")
        labels = {"agent": agent}

        lines.append(_fmt_counter(
            "edu_agent_llm_calls_total",
            "Total LLM calls per agent",
            stat["total_calls"],
            labels,
        ))
        lines.append(_fmt_counter(
            "edu_agent_llm_cached_calls_total",
            "LLM cache hit calls per agent",
            stat["cached_calls"],
            labels,
        ))
        lines.append(_fmt_gauge(
            "edu_agent_llm_cache_hit_rate",
            "LLM cache hit rate per agent (0-1)",
            stat["cache_hit_rate"],
            labels,
        ))
        lines.append(_fmt_counter(
            "edu_agent_llm_failed_calls_total",
            "Failed LLM calls per agent",
            stat["failed_calls"],
            labels,
        ))
        lines.append(_fmt_gauge(
            "edu_agent_llm_error_rate",
            "LLM error rate per agent (0-1)",
            stat["error_rate"],
            labels,
        ))
        lines.append(_fmt_gauge(
            "edu_agent_llm_latency_avg_ms",
            "LLM average latency per agent (ms)",
            stat["avg_latency_ms"],
            labels,
        ))
        if stat.get("p50_latency_ms") is not None:
            lines.append(_fmt_gauge(
                "edu_agent_llm_latency_p50_ms",
                "LLM p50 latency per agent (ms)",
                stat["p50_latency_ms"],
                labels,
            ))
        if stat.get("p95_latency_ms") is not None:
            lines.append(_fmt_gauge(
                "edu_agent_llm_latency_p95_ms",
                "LLM p95 latency per agent (ms)",
                stat["p95_latency_ms"],
                labels,
            ))
        lines.append(_fmt_counter(
            "edu_agent_llm_tokens_total",
            "Total LLM tokens per agent",
            stat["total_tokens"],
            labels,
        ))
        lines.append(_fmt_counter(
            "edu_agent_llm_prompt_tokens_total",
            "LLM prompt tokens per agent",
            stat["prompt_tokens"],
            labels,
        ))
        lines.append(_fmt_counter(
            "edu_agent_llm_completion_tokens_total",
            "LLM completion tokens per agent",
            stat["completion_tokens"],
            labels,
        ))

    return "".join(lines)


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def metrics():
    """Prometheus 拉取端点（不展示在 OpenAPI 文档中，避免污染）。"""
    return PlainTextResponse(
        content=_build_metrics_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
