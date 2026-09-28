"""
LLM 响应缓存（语义缓存）
=========================
对 BailianLLMClient.acomplete 的缓存层，降低重复请求成本：

设计要点：
1. 缓存 key：messages + model + temperature 哈希 + 文本前 256 字符的 embedding
   - 完全相同的输入 → 精确命中（最快）
   - 语义相似（cosine >= 阈值）→ 语义命中（次优，但能省 LLM 调用）
2. TTL：默认 1 小时（教育场景：教师教案内容稳定，重复率高）
3. 黑名单：含动态字段（如时间戳/随机数/session_id）的 prompt 不缓存
4. 后端：Redis（推荐，跨进程共享）；不可用降级为进程内 LRU（仅单进程有效）
5. 缓存大小：进程内 LRU 上限 256 条，避免内存膨胀
6. 命中时打 cached=True 标记，prompt_metrics 可统计命中率

使用方式：
    from app.services.llm.response_cache import llm_cache
    cached = await llm_cache.get(messages, model)
    if cached is not None:
        return cached
    result = await bailian_client.acomplete(messages, model)
    await llm_cache.set(messages, model, result)
    return result

黑名单规则（任一命中即跳过缓存）：
- system message 含 "你是{当前时间}" / "你的会话 ID" / "用户的 token 是" 等动态注入词
- user message 含 UUID / 长随机字符串（>= 32 字符的 hex）
- 显式指定 no_cache=True
"""
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.redis_client import redis_manager

logger = logging.getLogger(__name__)

# 进程内 LRU 兜底（Redis 不可用时使用）
from collections import OrderedDict
_LRU_MAX = 256
_lru: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()

# 默认 TTL：1 小时
DEFAULT_TTL = 3600

# 语义相似度阈值（cosine，1.0 = 完全相同）
SEMANTIC_HIT_THRESHOLD = 0.92

# 黑名单关键词：出现这些词的 prompt 视为动态内容，不缓存
_BLACKLIST_KEYWORDS = {
    "当前时间", "你的会话 ID", "用户的 token 是", "今天的日期",
    "随机数", "this_session_id", "uuid", "UUID",
}

# 缓存开关（生产可关）
_ENABLED = True


def _is_blacklisted(messages: List[Dict[str, str]]) -> bool:
    """检测 prompt 是否含动态字段（命中黑名单则不缓存）。"""
    for msg in messages:
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        # 黑名单关键词
        for kw in _BLACKLIST_KEYWORDS:
            if kw in content:
                return True
        # 长随机 hex 串（32+ 字符的 hex，常见于 UUID/MD5/SHA）
        import re
        if re.search(r"\b[0-9a-fA-F]{32,}\b", content):
            return True
    return False


def _exact_key(messages: List[Dict[str, str]], model: str, temperature: float) -> str:
    """精确匹配 key：messages + model + temperature 的 SHA256。"""
    payload = json.dumps(
        {"m": messages, "model": model, "t": round(temperature, 2)},
        ensure_ascii=False, sort_keys=True,
    )
    return "llm:cache:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _semantic_index_key(model: str) -> str:
    """语义索引 key：存储该 model 的所有缓存 entry 的 embedding + payload 引用。"""
    return f"llm:semidx:{model}"


async def _get_embedding_for_cache(text: str) -> Optional[List[float]]:
    """复用 bailian_client 拿 embedding；失败返回 None，跳过语义匹配。"""
    try:
        from app.services.llm.bailian_client import bailian_client
        # 取 prompt 前缀做 embedding（256 字符以内）
        snippet = text[:256]
        return await bailian_client.get_embedding(snippet)
    except Exception as e:
        logger.debug(f"[LLMCache] embedding 不可用，跳过语义匹配: {e}")
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度。"""
    import math
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class LLMResponseCache:
    """LLM 响应缓存：Redis 优先，进程内 LRU 兜底。"""

    async def get(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.3,
        no_cache: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        查缓存：
        1. 精确匹配（哈希）
        2. 语义匹配（embedding cosine >= 阈值）

        no_cache=True 跳过缓存读取（强制 LLM 重新生成）
        """
        if not _ENABLED or no_cache:
            return None
        if _is_blacklisted(messages):
            return None

        key = _exact_key(messages, model, temperature)

        # 1. 精确匹配：先 Redis 后 LRU
        if redis_manager.is_connected and redis_manager.client:
            try:
                raw = await redis_manager.client.get(key)
                if raw:
                    entry = json.loads(raw)
                    if entry.get("expires_at", 0) > time.time():
                        logger.debug(f"[LLMCache] exact hit (redis) key={key[:24]}")
                        return {**entry["result"], "cached": True}
            except Exception as e:
                logger.debug(f"[LLMCache] redis exact 查询失败: {e}")

        if key in _lru:
            expires_at, result = _lru[key]
            if expires_at > time.time():
                _lru.move_to_end(key)
                logger.debug(f"[LLMCache] exact hit (lru) key={key[:24]}")
                return {**result, "cached": True}
            else:
                _lru.pop(key, None)

        # 2. 语义匹配（仅 Redis 可用且 embedding 可用时；性能开销较高，谨慎使用）
        if redis_manager.is_connected and redis_manager.client:
            try:
                # 取最后一个 user message 做语义索引
                user_msg = next(
                    (m["content"] for m in reversed(messages) if m.get("role") == "user"),
                    None,
                )
                if not user_msg:
                    return None
                emb = await _get_embedding_for_cache(user_msg)
                if emb is None:
                    return None

                idx_key = _semantic_index_key(model)
                # 索引结构：zset，member = cache_key, score = timestamp
                # value 字段存 embedding JSON
                candidates = await redis_manager.client.zrange(idx_key, 0, -1)
                for cand_key in candidates:
                    if isinstance(cand_key, bytes):
                        cand_key = cand_key.decode("utf-8")
                    cand_emb_raw = await redis_manager.client.get(f"{cand_key}:emb")
                    if not cand_emb_raw:
                        continue
                    cand_emb = json.loads(cand_emb_raw)
                    score = _cosine(emb, cand_emb)
                    if score >= SEMANTIC_HIT_THRESHOLD:
                        raw = await redis_manager.client.get(cand_key)
                        if raw:
                            entry = json.loads(raw)
                            if entry.get("expires_at", 0) > time.time():
                                logger.info(
                                    f"[LLMCache] semantic hit score={score:.3f} key={cand_key[:24]}"
                                )
                                return {**entry["result"], "cached": True}
            except Exception as e:
                logger.debug(f"[LLMCache] 语义匹配失败: {e}")

        return None

    async def set(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
        result: Dict[str, Any],
        ttl: int = DEFAULT_TTL,
        no_cache: bool = False,
    ) -> None:
        """写缓存：精确 key + 语义索引。"""
        if not _ENABLED or no_cache:
            return
        if _is_blacklisted(messages):
            return

        key = _exact_key(messages, model, temperature)
        entry = {
            "result": {k: v for k, v in result.items() if k != "cached"},
            "expires_at": time.time() + ttl,
            "model": model,
        }

        # 1. 写精确 key
        if redis_manager.is_connected and redis_manager.client:
            try:
                await redis_manager.client.set(key, json.dumps(entry), ex=ttl)
                # 2. 写语义索引：member = key, score = timestamp
                idx_key = _semantic_index_key(model)
                await redis_manager.client.zadd(idx_key, {key: time.time()})
                # 索引自身 TTL 与缓存一致，过期自动清理
                await redis_manager.client.expire(idx_key, ttl)

                # 3. 写 embedding（用于语义匹配查询）
                user_msg = next(
                    (m["content"] for m in reversed(messages) if m.get("role") == "user"),
                    None,
                )
                if user_msg:
                    emb = await _get_embedding_for_cache(user_msg)
                    if emb is not None:
                        await redis_manager.client.set(
                            f"{key}:emb", json.dumps(emb), ex=ttl
                        )
                return
            except Exception as e:
                logger.debug(f"[LLMCache] redis 写入失败，降级 LRU: {e}")

        # 兜底：进程内 LRU
        _lru[key] = (entry["expires_at"], entry["result"])
        _lru.move_to_end(key)
        if len(_lru) > _LRU_MAX:
            _lru.popitem(last=False)

    async def invalidate(self, messages: List[Dict[str, str]], model: str, temperature: float = 0.3) -> None:
        """精确失效：当 prompt 版本变更时调用。"""
        key = _exact_key(messages, model, temperature)
        if redis_manager.is_connected and redis_manager.client:
            try:
                await redis_manager.client.delete(key, f"{key}:emb")
            except Exception:
                pass
        _lru.pop(key, None)

    async def clear_all(self) -> None:
        """清空全部缓存（运维紧急场景使用）。"""
        global _lru
        _lru.clear()
        if redis_manager.is_connected and redis_manager.client:
            try:
                # 扫描所有 llm:cache:* 前缀的 key 删除
                async for k in redis_manager.client.scan_iter(match="llm:cache:*"):
                    await redis_manager.client.delete(k)
                async for k in redis_manager.client.scan_iter(match="llm:semidx:*"):
                    await redis_manager.client.delete(k)
                logger.info("[LLMCache] 已清空全部 Redis 缓存")
            except Exception as e:
                logger.warning(f"[LLMCache] 清空 Redis 失败: {e}")


llm_cache = LLMResponseCache()
