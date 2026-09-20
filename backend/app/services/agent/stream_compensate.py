"""
打字机补偿（非流式 agent 适配层）
================================
教案/组卷/批阅等 agent 已实现 execute_stream，LLM token 经 on_token 实时透传；
socratic / academic_rag / math_solver 等内容型 agent 只有 execute（一次性返回）。
为让 SSE 主链路对所有智能体都呈现连续打字效果，在检测到 agent 未产出任何
真实 token 时，把其最终 Markdown 按固定片长经同一 on_token 通道分片补发。

这不是模型真实 token（真实 token 流以 execute_stream 路径为准），
仅为 UI 层的渐进式呈现补偿；done 事件的 output 始终是权威全文。
"""
import asyncio
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

TokenCallback = Callable[[str], Awaitable[None]]

# 每片字符数与间隔：约 500 字/秒，兼顾可读性与长文档等待时长
DEFAULT_SHARD_SIZE = 6
DEFAULT_INTERVAL_SECONDS = 0.012


async def replay_as_tokens(
    on_token: Optional[TokenCallback],
    text: str,
    shard_size: int = DEFAULT_SHARD_SIZE,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
) -> int:
    """把完整文本分片经 on_token 补发，返回发出的分片数；回调为空或文本为空时不动作。"""
    if on_token is None or not text:
        return 0
    emitted = 0
    try:
        for i in range(0, len(text), shard_size):
            await on_token(text[i:i + shard_size])
            emitted += 1
            if interval_seconds > 0:
                await asyncio.sleep(interval_seconds)
    except Exception as e:
        # 客户端断连等情况下回调可能失败，补偿中断不影响任务终态
        logger.debug(f"[StreamCompensate] 分片补发中断（已发 {emitted} 片）: {e}")
    return emitted


class TokenCounter:
    """包装 on_token：统计真实 token 回调次数，供调用方判断是否需要补偿。"""

    def __init__(self, on_token: Optional[TokenCallback]):
        self._on_token = on_token
        self.count = 0

    async def __call__(self, chunk: str) -> None:
        self.count += 1
        if self._on_token is not None:
            await self._on_token(chunk)

    async def compensate_if_silent(self, full_text: str) -> None:
        """全程无真实 token 时，对最终文本做分片补偿。"""
        if self.count == 0:
            await replay_as_tokens(self._on_token, full_text)
