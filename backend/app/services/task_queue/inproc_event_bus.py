"""
进程内 SSE 事件总线（无 Redis 开发降级通道）
================================================
当本机未启动 Redis 时，任务以 FastAPI BackgroundTasks 进程内 fallback 方式执行，
Celery 的 Redis Pub/Sub 不可用。本模块提供与 Redis 通道语义一致的进程内实现：

- 订阅时先回放该 task 的历史事件（断线重连补偿，上限 64 条），再接收实时事件；
- 收到 done/error 终态事件后自动结束订阅；
- 30s 无事件下发 SSE keep-alive 注释行；
- 终态且无订阅者的通道在 TTL 后自动回收。

注意：仅适用于「Web 进程 == 执行进程」的 fallback 模式；
Celery 跨进程部署时事件必须走 Redis Pub/Sub。
"""
import asyncio
import collections
import json
import logging
import time
from typing import Deque, Dict, Set

logger = logging.getLogger(__name__)

MAX_HISTORY = 64
BUS_TTL_SECONDS = 3600
KEEPALIVE_SECONDS = 30.0


class _TaskChannel:
    __slots__ = ("history", "subscribers", "terminal", "created_at")

    def __init__(self):
        self.history: Deque[str] = collections.deque(maxlen=MAX_HISTORY)
        self.subscribers: Set["asyncio.Queue[str]"] = set()
        self.terminal: bool = False
        self.created_at: float = time.monotonic()


class InProcEventBus:
    def __init__(self):
        self._channels: Dict[str, _TaskChannel] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _is_terminal(event_json: str) -> bool:
        try:
            return json.loads(event_json).get("event_type") in ("done", "error")
        except Exception:
            return False

    async def publish(self, task_id: str, event_json: str) -> None:
        """发布事件：落历史快照并分发给全部在线订阅者。"""
        async with self._lock:
            ch = self._channels.get(task_id)
            if ch is None:
                ch = _TaskChannel()
                self._channels[task_id] = ch
            ch.history.append(event_json)
            if self._is_terminal(event_json):
                ch.terminal = True
            queues = list(ch.subscribers)
        for q in queues:
            try:
                q.put_nowait(event_json)
            except asyncio.QueueFull:
                logger.warning(f"[InProcBus] 订阅队列已满，丢弃事件 task={task_id}")

    async def subscribe(self, task_id: str):
        """SSE 异步生成器：先回放历史，再实时接收，终态自动结束。"""
        async with self._lock:
            ch = self._channels.get(task_id)
            if ch is None:
                ch = _TaskChannel()
                self._channels[task_id] = ch
            history = list(ch.history)
            need_queue = not ch.terminal
            if need_queue:
                queue: "asyncio.Queue[str]" = asyncio.Queue(maxsize=512)
                ch.subscribers.add(queue)

        # 1. 历史补偿
        for item in history:
            yield f"data: {item}\n\n"
            if self._is_terminal(item):
                return

        if not need_queue:
            return

        # 2. 实时事件 + keep-alive
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {item}\n\n"
                if self._is_terminal(item):
                    return
        finally:
            async with self._lock:
                ch.subscribers.discard(queue)
                self._gc_locked()

    def _gc_locked(self) -> None:
        now = time.monotonic()
        dead = [
            tid for tid, ch in self._channels.items()
            if ch.terminal and not ch.subscribers and now - ch.created_at > BUS_TTL_SECONDS
        ]
        for tid in dead:
            self._channels.pop(tid, None)


# 全局单例
inproc_bus = InProcEventBus()
