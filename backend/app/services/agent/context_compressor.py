"""
ContextCompressor — 上下文三级渐进压缩

在 token 预算水位触发时逐级压缩消息列表（100% 熔断仍是最后防线）：
  L1（70%）规则裁剪：超长消息只留首尾、合并空白与重复内容；零 LLM 成本
  L2（80%）滚动摘要：中间轮（不含 system/用户指令/最近 N 轮/公式表格）
     由 turbo 一次压缩为结构化要点并替换
     ——原文不删，先归档再替换，可还原
  L3（90%）归档召回：最旧消息存入归档（Redis，7d TTL；无 Redis 用内存），
     主上下文只留摘要指针；后续对话由 recall() 按查询词相关性零成本召回

保留优先级（高 → 低，越靠前越晚动）：
  用户指令 > system/课标约束 > 公式$$/表格 > 最近 N 轮 > 中间轮/过程内容
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.redis_client import redis_manager
from app.services.rag.chunker import estimate_tokens

logger = logging.getLogger(__name__)

# 单条消息在 L1 裁剪后保留的最大字符（首尾各一半）
L1_MSG_KEEP_CHARS = 600

SUMMARY_SYSTEM_PROMPT = """你是上下文压缩器。把以下中间对话压缩为结构化要点：
1. 保留：关键事实与数据、已做出的决策、待办事项、约束条件；
2. 数学公式（$...$ / $$...$$）与数字必须原样保留，不得改写；
3. 不要添加原文没有的信息。
严格输出 JSON：
{"facts": ["..."], "decisions": ["..."], "open_items": ["..."], "narrative": "一段话概述"}"""


def count_messages_tokens(messages: List[Dict[str, str]]) -> int:
    return sum(estimate_tokens(m.get("content", "")) for m in messages)


def _has_rich_structure(content: str) -> bool:
    """含公式块/表格的消息高优先级保留。"""
    return "$$" in content or re.search(r"\|\s*-{2,}\s*\|", content) is not None


class ContextCompressor:
    """三级上下文压缩器。"""

    def __init__(
        self,
        token_budget: int,
        archive_key: str,
        keep_recent: int = settings.CONTEXT_KEEP_RECENT,
    ):
        self.token_budget = token_budget
        self.archive_key = f"ctxarchive:{archive_key}"
        self.keep_recent = keep_recent

    # ------------------------------------------------------------------
    async def maybe_compress(
        self, messages: List[Dict[str, str]]
    ) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
        """按水位逐级压缩；未超 70% 原样返回。"""
        stats = {"before_tokens": count_messages_tokens(messages), "levels": []}
        ratio = stats["before_tokens"] / max(self.token_budget, 1)

        if ratio < settings.CONTEXT_WATERMARK_L1:
            stats["after_tokens"] = stats["before_tokens"]
            return messages, stats

        messages = self._l1_trim(messages)
        stats["levels"].append("L1")

        if count_messages_tokens(messages) / self.token_budget >= settings.CONTEXT_WATERMARK_L2:
            messages = await self._l2_summarize(messages)
            stats["levels"].append("L2")

        if count_messages_tokens(messages) / self.token_budget >= settings.CONTEXT_WATERMARK_L3:
            messages = await self._l3_archive(messages)
            stats["levels"].append("L3")

        stats["after_tokens"] = count_messages_tokens(messages)
        logger.info(
            f"[ContextCompressor] 压缩完成 {stats['before_tokens']}→"
            f"{stats['after_tokens']} tokens，级别={stats['levels']}"
        )
        return messages, stats

    # ------------------------------------------------------------------
    # L1：规则裁剪
    # ------------------------------------------------------------------
    def _l1_trim(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        result: List[Dict[str, str]] = []
        seen_contents = set()
        for m in messages:
            content = re.sub(r"[ \t]{2,}", " ", m.get("content", ""))
            content = re.sub(r"\n{3,}", "\n\n", content).strip()

            # 去重：完全相同的消息只保留一条
            sig = content[:200]
            if sig in seen_contents:
                continue
            seen_contents.add(sig)

            # 超长且无公式/表格的过程性消息：留首尾
            if len(content) > L1_MSG_KEEP_CHARS and not _has_rich_structure(content):
                head = content[: L1_MSG_KEEP_CHARS // 2]
                tail = content[-L1_MSG_KEEP_CHARS // 2 :]
                content = f"{head}\n…（已省略 {len(content) - L1_MSG_KEEP_CHARS} 字符）…\n{tail}"

            result.append({**m, "content": content})
        return result

    # ------------------------------------------------------------------
    # L2：滚动摘要（中间轮 → 结构化要点）
    # ------------------------------------------------------------------
    async def _l2_summarize(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        # 保护区：system、第一条用户指令、最后 N 轮；含公式/表格的轮次
        head: List[Dict[str, str]] = []
        i = 0
        while i < len(messages) and messages[i].get("role") in ("system", "developer"):
            head.append(messages[i])
            i += 1
        user_instruction: List[Dict[str, str]] = []
        if i < len(messages) and messages[i].get("role") == "user":
            user_instruction = [messages[i]]
            i += 1

        tail_start = len(messages) - self.keep_recent
        middle, tail = [], []
        for idx in range(i, len(messages)):
            if idx >= tail_start:
                tail.append(messages[idx])
            elif _has_rich_structure(messages[idx].get("content", "")):
                # 公式/表格不进摘要，原样保留到 head 尾部
                head.append(messages[idx])
            else:
                middle.append(messages[idx])

        if not middle:
            return messages

        # 原文先归档（可还原），再做摘要替换
        await self._append_archive(middle)

        try:
            from app.services.llm.bailian_client import bailian_client
            convo_text = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')[:800]}" for m in middle
            )
            resp = await bailian_client.acomplete(
                [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": convo_text[:6000]},
                ],
                model=settings.ROUTER_LIGHT_MODEL,
                temperature=0.0, max_tokens=900,
                response_format={"type": "json_object"},
            )
            summary = json.loads(resp.get("content", "{}"))
            summary_text = self._format_summary(summary)
        except Exception as e:
            logger.warning(f"[ContextCompressor] L2 摘要 LLM 失败，用截断兜底: {e}")
            joined = "\n".join(m.get("content", "")[:200] for m in middle)
            summary_text = f"【前期对话要点（规则截断）】\n{joined[:800]}"

        summary_msg = {
            "role": "system",
            "content": f"【以下为前期对话要点（上下文已自动压缩，原文已归档可召回）】\n{summary_text}",
        }
        return head + user_instruction + [summary_msg] + tail

    @staticmethod
    def _format_summary(summary: Dict[str, Any]) -> str:
        lines = []
        for key, label in [("facts", "关键事实"), ("decisions", "已做决策"), ("open_items", "待办事项")]:
            items = summary.get(key) or []
            if items:
                lines.append(f"■ {label}：")
                lines.extend(f"  - {x}" for x in items)
        narrative = summary.get("narrative")
        if narrative:
            lines.append(f"■ 概述：{narrative}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # L3：归档最旧消息，只留指针
    # ------------------------------------------------------------------
    async def _l3_archive(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        # 第一条用户消息视为用户指令，永不归档
        first_user_idx = next(
            (i for i, m in enumerate(messages) if m.get("role") == "user"), None
        )
        keep_from = len(messages) - self.keep_recent

        to_archive: List[Dict[str, str]] = []
        kept: List[Dict[str, str]] = []
        for idx, m in enumerate(messages):
            protected = (
                m.get("role") in ("system", "developer")
                or idx == first_user_idx
                or idx >= keep_from
                or _has_rich_structure(m.get("content", ""))
            )
            if protected:
                kept.append(m)
            else:
                to_archive.append(m)

        if not to_archive:
            return messages

        await self._append_archive(to_archive)
        pointer = {
            "role": "system",
            "content": (
                f"【已归档 {len(to_archive)} 条早期消息（7 天内可按相关性召回，"
                f"归档键 {self.archive_key}）】"
            ),
        }
        # 指针紧跟用户指令之后，保持后续轮次顺序
        insert_at = first_user_idx + 1 if first_user_idx is not None else 0
        return kept[:insert_at] + [pointer] + kept[insert_at:]

    # ------------------------------------------------------------------
    # 归档存储 / 召回
    # ------------------------------------------------------------------
    async def _append_archive(self, batch: List[Dict[str, str]]):
        try:
            existing = await redis_manager.get(self.archive_key)
            store = json.loads(existing) if existing else []
            store.extend(batch)
            await redis_manager.set(
                self.archive_key, json.dumps(store, ensure_ascii=False),
                ex=settings.CONTEXT_ARCHIVE_TTL,
            )
        except Exception as e:
            logger.error(f"[ContextCompressor] 归档写入失败: {e}")

    async def recall(self, query: str, top_k: int = 3) -> List[Dict[str, str]]:
        """按查询相关性从归档召回（字符二元组匹配，零 LLM 成本）。"""
        try:
            raw = await redis_manager.get(self.archive_key)
            store = json.loads(raw) if raw else []
        except Exception:
            return []
        if not store:
            return []

        def bigrams(text: str) -> set:
            text = re.sub(r"\s+", "", text)
            return {text[i : i + 2] for i in range(len(text) - 1)} if len(text) > 1 else {text}

        q_terms = bigrams(query)
        scored = []
        for m in store:
            terms = bigrams(m.get("content", ""))
            score = len(q_terms & terms)
            if score > 0:
                scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:top_k]]


def make_compressor_for_scope(scope, token_budget: Optional[int] = None) -> ContextCompressor:
    """工厂：为 ContextScope 构造压缩器，归档键按 scope 隔离。"""
    budget = token_budget or scope.token_budget or 4096
    return ContextCompressor(budget, archive_key=scope.scope_id)
