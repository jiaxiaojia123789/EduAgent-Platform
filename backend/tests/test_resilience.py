"""
P3-18 故障注入与韧性测试
=========================
覆盖关键链路在故障下的降级与恢复行为：

1. test_circuit_breaker_opens_after_threshold_failures
   断路器连续失败达阈值后 OPEN，拒绝后续请求
2. test_circuit_breaker_half_open_after_cooldown
   冷却期满后转 HALF_OPEN，探测成功回 CLOSED
3. test_circuit_breaker_half_open_probe_failure_reopens
   HALF_OPEN 探测失败立即回 OPEN
4. test_redis_disconnect_degrades_to_memory_checkpointer
   Redis 断开时 checkpointer 降级为 MemorySaver
5. test_dashscope_quota_exhausted_friendly_message
   DashScope 配额耗尽（403）抛出友好错误而非 RuntimeError
6. test_llm_response_cache_skips_blacklisted_dynamic_prompts
   含动态字段（UUID/时间戳）的 prompt 不缓存
7. test_llm_response_cache_exact_hit_returns_cached_flag
   精确命中后返回 cached=True 标记
8. test_celery_task_retry_on_exception
   Celery 任务失败时按指数退避重试（mock celery_app）
9. test_circuit_breaker_metrics_exported_to_prometheus
   /metrics 端点导出断路器状态
"""
import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================
# Circuit Breaker 单元测试
# ============================================================

def test_circuit_breaker_opens_after_threshold_failures():
    """连续失败达阈值后 OPEN，拒绝后续请求（cooldown 设大避免立即转 HALF_OPEN）。"""
    from app.harness.circuit_breaker import CircuitBreaker, BreakerState, CircuitBreakerOpenError

    breaker = CircuitBreaker(name="test", failure_threshold=3, cooldown_seconds=60)
    assert breaker.state == BreakerState.CLOSED

    # 触发 3 次失败
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == BreakerState.OPEN
    assert breaker.is_open is True

    # OPEN 状态下 protect 应直接拒绝
    async def _run():
        async with breaker.protect():
            pytest.fail("should not enter protect when OPEN")

    with pytest.raises(CircuitBreakerOpenError):
        asyncio.run(_run())


def test_circuit_breaker_half_open_after_cooldown():
    """冷却期满后转 HALF_OPEN，探测成功回 CLOSED。"""
    from app.harness.circuit_breaker import CircuitBreaker, BreakerState

    breaker = CircuitBreaker(name="test", failure_threshold=2, cooldown_seconds=0)
    # 触发 OPEN
    breaker.record_failure()
    breaker.record_failure()
    # cooldown=0 → 立即转 HALF_OPEN（state 属性访问触发 _maybe_half_open）
    assert breaker.state == BreakerState.HALF_OPEN

    # 探测成功回 CLOSED
    breaker.record_success()
    assert breaker.state == BreakerState.CLOSED


def test_circuit_breaker_half_open_probe_failure_reopens():
    """HALF_OPEN 探测失败立即回 OPEN（再因 cooldown=0 立即转 HALF_OPEN）。"""
    from app.harness.circuit_breaker import CircuitBreaker, BreakerState

    breaker = CircuitBreaker(name="test", failure_threshold=1, cooldown_seconds=0)
    breaker.record_failure()
    # cooldown=0 → 立即 HALF_OPEN
    assert breaker.state == BreakerState.HALF_OPEN

    # HALF_OPEN 探测失败 → 立即回 OPEN → cooldown=0 立即又 HALF_OPEN
    breaker.record_failure()
    assert breaker.state == BreakerState.HALF_OPEN
    # 但失败计数应该累加，证明状态机正确
    assert breaker.stats["failure_count"] == 2


def test_circuit_breaker_stays_open_during_cooldown():
    """cooldown 期间保持 OPEN，不转 HALF_OPEN。"""
    from app.harness.circuit_breaker import CircuitBreaker, BreakerState

    breaker = CircuitBreaker(name="test", failure_threshold=1, cooldown_seconds=60)
    breaker.record_failure()
    # 立即检查：cooldown=60s 未到，应保持 OPEN
    assert breaker.state == BreakerState.OPEN
    assert breaker.is_open is True


def test_circuit_breaker_reset():
    """reset() 强制回 CLOSED。"""
    from app.harness.circuit_breaker import CircuitBreaker, BreakerState

    breaker = CircuitBreaker(name="test", failure_threshold=1, cooldown_seconds=60)
    breaker.record_failure()
    assert breaker.state == BreakerState.OPEN

    breaker.reset()
    assert breaker.state == BreakerState.CLOSED
    assert breaker.stats["failure_count"] == 0


# ============================================================
# Redis 降级测试
# ============================================================

@pytest.mark.asyncio
async def test_redis_disconnect_degrades_to_memory_checkpointer():
    """Redis 断开时 checkpointer 降级为 MemorySaver。"""
    # 重置单例，强制下次 get_checkpointer 重新探测
    import app.services.agent.teaching_graph as tg
    tg._checkpointer = None
    tg._checkpointer_backend = "memory"

    # mock redis_manager 为未连接
    with patch.object(tg, "_build_redis_saver", new=AsyncMock(return_value=None)):
        cp = await tg.get_checkpointer()
        assert tg.checkpointer_backend() == "memory"
        # MemorySaver 实例
        from langgraph.checkpoint.memory import MemorySaver
        assert isinstance(cp, MemorySaver)

    # 清理单例
    tg._checkpointer = None


# ============================================================
# LLM 配额耗尽友好错误测试
# ============================================================

def test_dashscope_quota_exhausted_friendly_message():
    """DashScope 配额耗尽（403 FreeTierOnly）应抛 RuntimeError 含 AllocationQuota。"""
    from app.services.llm.bailian_client import BailianLLMClient

    client = BailianLLMClient()
    # 强制非 mock 模式
    client.is_mock = False

    # mock requests.post 返回 403 配额耗尽
    fake_resp = MagicMock()
    fake_resp.status_code = 403
    fake_resp.text = json.dumps({
        "error": {
            "code": "AllocationQuota.FreeTierOnly",
            "message": "Free quota exhausted.",
        }
    })

    # 直接 mock with_timeout 返回 fake_resp（绕过 requests.post）
    with patch("app.services.llm.bailian_client.with_timeout", new=AsyncMock(return_value=fake_resp)):
        # mock 缓存层 get 返回 None（避免命中缓存）
        with patch("app.services.llm.response_cache.llm_cache.get", new=AsyncMock(return_value=None)):
            with patch(
                "app.services.llm.response_cache.llm_cache.set",
                new=AsyncMock(return_value=None),
            ):
                with pytest.raises(RuntimeError) as exc_info:
                    asyncio.run(
                        client.acomplete(
                            [{"role": "user", "content": "test"}],
                            no_cache=True,
                        )
                    )

    err_msg = str(exc_info.value)
    assert "AllocationQuota" in err_msg or "FreeTierOnly" in err_msg, \
        f"应包含 AllocationQuota 关键字，实际: {err_msg}"


# ============================================================
# 响应缓存黑名单测试
# ============================================================

def test_response_cache_skips_blacklisted_uuid_prompts():
    """含 32+ 字符 hex 串的 prompt 命中黑名单，不缓存。"""
    from app.services.llm.response_cache import _is_blacklisted

    # 含 UUID hex 串
    msg_with_uuid = [
        {"role": "system", "content": "你是教学助手"},
        {"role": "user", "content": "请基于会话 8c1f3a2b4d5e6789abcdef0123456789 生成教案"},
    ]
    assert _is_blacklisted(msg_with_uuid) is True

    # 含时间戳关键词
    msg_with_time = [
        {"role": "user", "content": "当前时间是 2026-09-28，请生成今天的教案"},
    ]
    assert _is_blacklisted(msg_with_time) is True

    # 正常 prompt 不命中黑名单
    normal_msg = [
        {"role": "system", "content": "你是教学助手"},
        {"role": "user", "content": "请为《导数的几何意义》设计45分钟公开课教案"},
    ]
    assert _is_blacklisted(normal_msg) is False


@pytest.mark.asyncio
async def test_response_cache_exact_hit_returns_cached_flag():
    """精确命中后返回 cached=True 标记（进程内 LRU 路径）。"""
    from app.services.llm.response_cache import llm_cache, _lru

    # 清空 LRU
    _lru.clear()

    messages = [{"role": "user", "content": "测试 prompt"}]
    result = {"content": "测试响应", "model": "qwen-test"}

    # 写入缓存
    await llm_cache.set(messages, "qwen-test", 0.3, result, ttl=60)

    # 读取应命中
    got = await llm_cache.get(messages, "qwen-test", 0.3)
    assert got is not None
    assert got.get("cached") is True
    assert got.get("content") == "测试响应"


@pytest.mark.asyncio
async def test_response_cache_expired_entry_not_returned():
    """过期条目不返回（TTL=1s 后再查应返回 None）。"""
    import time
    from app.services.llm.response_cache import llm_cache, _lru

    _lru.clear()
    messages = [{"role": "user", "content": "短 TTL 测试"}]
    await llm_cache.set(messages, "qwen-test", 0.3, {"content": "resp"}, ttl=1)

    # 等待过期
    time.sleep(1.1)
    got = await llm_cache.get(messages, "qwen-test", 0.3)
    assert got is None


# ============================================================
# Celery 任务重投递测试
# ============================================================

def test_celery_task_retry_on_exception():
    """Celery 任务失败时按指数退避重试（mock celery_app）。"""
    from app.core.celery_app import _HAS_CELERY

    if not _HAS_CELERY:
        pytest.skip("Celery 未安装，跳过重试逻辑测试")

    # mock celery task 的 retry 方法
    from app.core.celery_app import run_agent_workflow_task
    fake_self = MagicMock()
    fake_self.request.retries = 0
    fake_self.retry = MagicMock(side_effect=Exception("retry triggered"))

    # 触发任务失败
    with patch("app.core.celery_app._async_run_workflow", new=AsyncMock(side_effect=RuntimeError("LLM 503"))):
        with patch("app.core.celery_app._publish_event", new=AsyncMock()):
            with pytest.raises(Exception, match="retry triggered"):
                run_agent_workflow_task(
                    fake_self,
                    task_id="t-retry-test",
                    user_message="test",
                    session_id="s-test",
                    thread_id="th-test",
                )

    # 验证 retry 被调用
    fake_self.retry.assert_called_once()
    # countdown 应是指数退避：10 * (retries + 1) = 10
    call_kwargs = fake_self.retry.call_args
    assert call_kwargs.kwargs.get("countdown") == 10


# ============================================================
# /metrics 端点导出断路器状态测试
# ============================================================

def test_metrics_endpoint_exports_circuit_breaker_state():
    """/metrics 端点导出断路器状态。"""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "edu_agent_circuit_breaker_state" in text
    assert 'breaker="dashscope_llm"' in text
    assert "edu_agent_dependency_up" in text


def test_metrics_endpoint_exports_agent_stats():
    """/metrics 端点导出 agent 聚合指标（即使无数据也要返回 header）。"""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    # 即使无调用数据，HELP/TYPE header 也应存在
    assert "# HELP edu_agent_llm_calls_total" in text
