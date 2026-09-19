"""
Agent Harness: 外部调用超时强杀（Watchdog）

统一封装所有外部依赖调用（LLM / Embedding / 代码沙箱等）的截止时间：
- with_timeout          : 给协程加硬超时，超时即取消 await 并抛出
                          ExternalCallTimeoutError，调用链立即被强杀
- run_sync_with_timeout : 给同步阻塞函数（requests / subprocess 封装）
                          加硬超时，通过 asyncio.wait_for + to_thread 实现

设计说明（Windows / CPython 限制）：
- asyncio 的取消只能释放"等待侧"协程，无法真正杀死 to_thread 中的工作线程；
  因此被包装的同步函数自身必须设置底层超时（如 requests timeout、
  subprocess kill），保证挂起的线程最终退出，本模块负责让上层业务
  在截止时间到达时立即失败、释放锁与并发槽，不再无限 await。
"""
import asyncio
import functools
import logging
from typing import Any, Callable, Awaitable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ExternalCallTimeoutError(RuntimeError):
    """外部调用超过截止时间，被 watchdog 强杀。"""

    def __init__(self, name: str, timeout: float, elapsed: float):
        super().__init__(
            f"外部调用 '{name}' 超过 {timeout}s 截止时间（实际等待 {elapsed:.1f}s），已触发强杀"
        )
        self.call_name = name
        self.timeout = timeout


async def with_timeout(
    awaitable: Awaitable[T],
    timeout: float,
    name: str,
) -> T:
    """
    给协程加硬超时。超时则取消协程并抛出 ExternalCallTimeoutError。
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError:
        elapsed = loop.time() - start
        logger.warning(f"[TimeoutWatchdog] 强杀外部调用 '{name}' after {elapsed:.1f}s")
        raise ExternalCallTimeoutError(name, timeout, elapsed)


async def run_sync_with_timeout(
    func: Callable[..., T],
    timeout: float,
    name: str,
    *args: Any,
    **kwargs: Any,
) -> T:
    """
    给同步阻塞函数加硬超时：先 to_thread 再 wait_for。
    超时后业务侧立即失败（底层线程由函数自身超时机制收尾）。
    """
    return await with_timeout(
        asyncio.to_thread(functools.partial(func, *args, **kwargs)),
        timeout=timeout,
        name=name,
    )
