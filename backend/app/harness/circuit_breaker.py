"""
LLM 调用断路器（Circuit Breaker）
================================
保护系统免受上游 LLM 服务故障级联影响：

状态机：
- CLOSED（正常）：所有请求放行，记录成功/失败
- OPEN（熔断）：连续失败 >= threshold 次，直接拒绝请求 N 秒（cooldown）
- HALF_OPEN（探测）：cooldown 到期后放行一个请求，成功则回 CLOSED，失败则回 OPEN

设计要点：
1. 不依赖 Redis：进程内状态即可，断路器失效影响只在单进程
2. 线程安全：用 threading.Lock 保护状态切换
3. 异步友好：用于 acomplete/astream 的 wrap 调用
4. 配置可调：threshold / cooldown 通过 settings 暴露
5. 暴露状态给 /metrics：便于监控告警按状态触发动作

使用方式：
    from app.harness.circuit_breaker import llm_breaker

    async def call_llm():
        async with llm_breaker.protect("dashscope") as breaker:
            if breaker.is_open:
                raise RuntimeError("LLM 服务熔断中，请稍后重试")
            return await bailian_client.acomplete(...)
"""
import logging
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class BreakerState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class _BreakerInternal:
    state: BreakerState = BreakerState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_ts: float = 0.0
    opened_at: float = 0.0


class CircuitBreaker:
    """
    单一断路器实例。维护某个上游服务的健康状态。

    保护对象示例：
    - dashscope LLM API
    - mineru 文档解析 API
    - milvus 向量检索
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        cooldown_seconds: int = 30,
        half_open_probe_count: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_probe_count = half_open_probe_count
        self._internal = _BreakerInternal()
        self._lock = threading.Lock()
        # 半开探测期已放行的请求数（防止半开时大量请求涌入）
        self._half_open_used = 0

    @property
    def state(self) -> BreakerState:
        """当前状态（自动检查是否该从 OPEN → HALF_OPEN）。"""
        with self._lock:
            self._maybe_half_open()
            return self._internal.state

    @property
    def is_open(self) -> bool:
        """OPEN 状态下应拒绝请求（外层调用方判断）。"""
        return self.state == BreakerState.OPEN

    @property
    def stats(self) -> dict:
        """暴露给 /metrics 端点。"""
        with self._lock:
            return {
                "name": self.name,
                "state": self._internal.state.value,
                "failure_count": self._internal.failure_count,
                "success_count": self._internal.success_count,
            }

    def _maybe_half_open(self) -> None:
        """OPEN 状态冷却期满则转 HALF_OPEN（必须在 _lock 内调用）。"""
        if self._internal.state == BreakerState.OPEN:
            elapsed = time.monotonic() - self._internal.opened_at
            if elapsed >= self.cooldown_seconds:
                self._internal.state = BreakerState.HALF_OPEN
                self._half_open_used = 0
                logger.info(
                    f"[Breaker:{self.name}] OPEN → HALF_OPEN (cooldown {self.cooldown_seconds}s elapsed)"
                )

    def record_success(self) -> None:
        """记录一次成功调用：HALF_OPEN → CLOSED，其他状态重置失败计数。"""
        with self._lock:
            self._internal.success_count += 1
            if self._internal.state == BreakerState.HALF_OPEN:
                self._internal.state = BreakerState.CLOSED
                self._internal.failure_count = 0
                logger.info(f"[Breaker:{self.name}] HALF_OPEN → CLOSED (probe succeeded)")

    def record_failure(self) -> None:
        """记录一次失败调用：失败计数达阈值则 OPEN。"""
        with self._lock:
            self._internal.failure_count += 1
            self._internal.last_failure_ts = time.time()
            if self._internal.state == BreakerState.HALF_OPEN:
                # 半开探测失败，立即回到 OPEN
                self._internal.state = BreakerState.OPEN
                self._internal.opened_at = time.monotonic()
                logger.warning(f"[Breaker:{self.name}] HALF_OPEN → OPEN (probe failed)")
            elif self._internal.state == BreakerState.CLOSED:
                if self._internal.failure_count >= self.failure_threshold:
                    self._internal.state = BreakerState.OPEN
                    self._internal.opened_at = time.monotonic()
                    logger.warning(
                        f"[Breaker:{self.name}] CLOSED → OPEN "
                        f"(failures={self._internal.failure_count} >= {self.failure_threshold})"
                    )

    @asynccontextmanager
    async def protect(self, name: Optional[str] = None):
        """
        保护一次异步调用：
        - 进入前检查状态，OPEN 则直接抛错
        - 调用方在 try/except 里 record_success/record_failure
        - 用法：async with breaker.protect("dashscope"): ...
        """
        if self.is_open:
            raise CircuitBreakerOpenError(
                f"Circuit breaker [{self.name}] is OPEN. "
                f"Cooldown remaining: {self._cooldown_remaining()}s"
            )
        # HALF_OPEN 状态限流：仅放行 N 个探测请求
        with self._lock:
            if self._internal.state == BreakerState.HALF_OPEN:
                if self._half_open_used >= self.half_open_probe_count:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker [{self.name}] is HALF_OPEN with probe limit reached."
                    )
                self._half_open_used += 1

        try:
            yield self
        except Exception as e:
            self.record_failure()
            raise
        else:
            self.record_success()

    def _cooldown_remaining(self) -> float:
        """OPEN 状态下剩余冷却时间（秒）。"""
        with self._lock:
            if self._internal.state != BreakerState.OPEN:
                return 0.0
            elapsed = time.monotonic() - self._internal.opened_at
            return max(0.0, self.cooldown_seconds - elapsed)

    def reset(self) -> None:
        """强制重置（运维紧急场景）。"""
        with self._lock:
            self._internal = _BreakerInternal()
            self._half_open_used = 0
            logger.info(f"[Breaker:{self.name}] reset to CLOSED")


class CircuitBreakerOpenError(RuntimeError):
    """断路器 OPEN 状态下拒绝请求的专用异常。"""
    pass


# ============================================================
# 全局单例：保护关键上游服务
# ============================================================

llm_breaker = CircuitBreaker(
    name="dashscope_llm",
    failure_threshold=settings.MAX_REFLECT_RETRIES + 3,  # 默认 5
    cooldown_seconds=30,
)

mineru_breaker = CircuitBreaker(
    name="mineru_parse",
    failure_threshold=3,  # 文档解析失败容忍度更低
    cooldown_seconds=60,
)


def all_breakers() -> list:
    """暴露所有断路器状态给 /metrics 端点。"""
    return [llm_breaker.stats, mineru_breaker.stats]
