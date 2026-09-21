import asyncio
import json
import logging
import re
from typing import List, Dict, Any, Tuple, Optional

from app.core.config import settings
from app.services.llm.bailian_client import bailian_client

logger = logging.getLogger(__name__)


class HallucinationChecker:
    """
    RAG Grounding & Hallucination Defense Gate

    升级：从「关键词命中率」→「LLM NLI 裁判」
    - extract_citations: 引用标记 [1][2] 绑定到检索文档元数据（保留原逻辑）
    - verify_grounding: 把答案拆成若干 claim → LLM 逐条判断 entailment/contradiction/neutral
      → 以 entailment 占比作为 grounding_score；LLM 调用失败时降级到关键词命中率
    """

    # 上下文截断上限（避免 NLI 裁判 prompt 超 token）
    _CONTEXT_CHAR_LIMIT = 6000
    # grounded 判定阈值：entailment 占比 ≥ 该值视为有依据
    _GROUNDED_THRESHOLD = 0.6

    # ---------------- 引用提取（保留原逻辑） ----------------

    @staticmethod
    def extract_citations(answer: str, retrieved_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        citation_marks = re.findall(r"\[(\d+)\]", answer)
        unique_indices = sorted(list(set(int(m) for m in citation_marks if m.isdigit())))

        citations = []
        for idx in unique_indices:
            doc_idx = idx - 1
            if 0 <= doc_idx < len(retrieved_docs):
                doc = retrieved_docs[doc_idx]
                meta = doc.get("metadata", {})
                snippet = doc.get("content", "")[:180] + "..."
                citations.append({
                    "citation_id": idx,
                    "chunk_id": doc.get("chunk_id", ""),
                    "title": meta.get("section_path", f"文献切片 #{idx}"),
                    "page_number": doc.get("page_number", 1),
                    "confidence_score": doc.get("rerank_score") or doc.get("final_score", 0.9),
                    "snippet": snippet
                })
        return citations

    # ---------------- 核心：LLM NLI 裁判 ----------------

    @staticmethod
    def _split_claims(answer: str) -> List[str]:
        """把答案拆成可独立校验的陈述（按中英文句末标点 + 换行），过滤过短句。"""
        sentences = re.split(r"[。！？!?\n]+", answer)
        claims = [s.strip() for s in sentences if len(s.strip()) >= 4]
        return claims

    @staticmethod
    def _truncate_context(retrieved_docs: List[Dict[str, Any]]) -> str:
        """拼接检索上下文并截断到字符上限，优先保留靠前文档。"""
        parts = []
        total = 0
        for d in retrieved_docs:
            content = d.get("content", "")
            if not content:
                continue
            if total + len(content) > HallucinationChecker._CONTEXT_CHAR_LIMIT:
                remain = HallucinationChecker._CONTEXT_CHAR_LIMIT - total
                if remain > 50:
                    parts.append(content[:remain])
                break
            parts.append(content)
            total += len(content)
        return "\n\n".join(parts)

    @staticmethod
    async def verify_grounding(
        answer: str,
        retrieved_docs: List[Dict[str, Any]]
    ) -> Tuple[bool, float, str]:
        """
        验证答案是否被检索上下文支撑。
        返回 (is_grounded, grounding_score, status)
        """
        if not retrieved_docs:
            return True, 1.0, "无检索上下文约束"

        claims = HallucinationChecker._split_claims(answer)
        if not claims:
            return True, 1.0, "答案无可校验陈述"

        # 优先走 LLM NLI 裁判
        try:
            result = await HallucinationChecker._llm_nli_judge(claims, retrieved_docs)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(f"[Grounding] LLM NLI 裁判异常，降级关键词命中率: {type(e).__name__}: {e}")

        # 降级：关键词命中率
        return HallucinationChecker._keyword_grounding(answer, retrieved_docs)

    @staticmethod
    async def _llm_nli_judge(
        claims: List[str],
        retrieved_docs: List[Dict[str, Any]]
    ) -> Optional[Tuple[bool, float, str]]:
        """
        LLM NLI 裁判：一次性打包所有 claim，要求输出 JSON 数组。
        解析失败返回 None（由调用方降级）。
        """
        context = HallucinationChecker._truncate_context(retrieved_docs)
        if not context.strip():
            return None

        claims_text = "\n".join(f"{i}. {c}" for i, c in enumerate(claims))

        system = (
            "你是一个严谨的事实验证裁判。给定【参考上下文】和若干【待验证陈述】，"
            "请逐条判断每条陈述相对于参考上下文的蕴含关系。\n"
            "标签定义：\n"
            "- entailment：陈述内容完全或主要可由参考上下文推出\n"
            "- contradiction：陈述与参考上下文明显矛盾\n"
            "- neutral：参考上下文既不支持也不矛盾，陈述属于无依据推断\n"
            "只输出 JSON 数组，不要任何解释文字。数组元素格式："
            '{"claim_index": 0, "label": "entailment|contradiction|neutral", "confidence": 0.0-1.0}'
        )
        user = (
            f"【参考上下文】\n{context}\n\n"
            f"【待验证陈述】\n{claims_text}\n\n"
            "请输出 JSON 数组。"
        )

        resp = await bailian_client.acomplete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=settings.ROUTER_LIGHT_MODEL,
            temperature=0.0,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        raw = resp.get("content", "").strip()
        if not raw:
            return None

        judgments = HallucinationChecker._parse_judgments(raw, len(claims))
        if not judgments:
            return None

        entail = sum(1 for j in judgments if j["label"] == "entailment")
        contra = sum(1 for j in judgments if j["label"] == "contradiction")
        neutral = sum(1 for j in judgments if j["label"] == "neutral")
        total = len(judgments)
        score = entail / total if total else 0.0

        is_grounded = score >= HallucinationChecker._GROUNDED_THRESHOLD
        if contra > 0:
            status = f"检测到 {contra} 条与文献矛盾的陈述"
        elif is_grounded:
            status = f"事实对齐良好，{entail}/{total} 条陈述有文献支撑"
        else:
            status = f"{neutral}/{total} 条陈述缺乏文献支撑，存在无依据推断风险"

        return is_grounded, round(score, 4), status

    @staticmethod
    def _parse_judgments(raw: str, expected_count: int) -> List[Dict[str, Any]]:
        """
        解析 LLM 输出的 JSON 数组。
        兼容：```json ... ``` 包裹、前后多余文字、对象而非数组。
        """
        text = raw.strip()
        # 去掉 markdown 代码块包裹
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        # 提取第一个 [ ... ] 或 { ... }
        array_match = re.search(r"\[.*\]", text, re.DOTALL)
        obj_match = re.search(r"\{.*\}", text, re.DOTALL)
        if array_match:
            candidate = array_match.group(0)
        elif obj_match:
            candidate = "[" + obj_match.group(0) + "]"
        else:
            return []

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            return []

        if not isinstance(data, list):
            return []

        judgments = []
        for item in data:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).lower()
            if label not in ("entailment", "contradiction", "neutral"):
                continue
            judgments.append({"label": label})

        # 若解析出的条数与预期不符，只要有合理结果就用（不强制等长）
        return judgments

    # ---------------- 降级：关键词命中率 ----------------

    @staticmethod
    def _keyword_grounding(
        answer: str,
        retrieved_docs: List[Dict[str, Any]]
    ) -> Tuple[bool, float, str]:
        """原关键词命中率逻辑，LLM 不可用时的降级路径。"""
        combined_context = " ".join([d.get("content", "") for d in retrieved_docs])
        key_tokens = re.findall(r"[\u4e00-\u9fff]{2,4}|[a-zA-Z]{3,}", answer)
        if not key_tokens:
            return True, 1.0, "答案过于简短，无需校验"

        hits = sum(1 for t in key_tokens if t in combined_context)
        score = hits / len(key_tokens)
        is_grounded = score >= 0.35
        status = "事实对齐良好，引用溯源可信" if is_grounded else "检测到潜在事实偏离或无依据推断风险"
        return is_grounded, round(score, 4), status


hallucination_checker = HallucinationChecker()
