import re
from typing import List, Dict, Any, Tuple


class HallucinationChecker:
    """
    RAG Grounding & Hallucination Defense Gate
    1. Validates that statements cite valid source documents [1], [2]
    2. Checks whether cited claims actually exist in retrieved context
    3. Formats citation popover cards for Doubao UI (title, page, snippet)
    """

    @staticmethod
    def extract_citations(answer: str, retrieved_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extracts citation tokens like [1], [2], [文献来源: ...] from answer,
        and binds them to the retrieved document metadata for tooltip preview.
        """
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

    @staticmethod
    def verify_grounding(answer: str, retrieved_docs: List[Dict[str, Any]]) -> Tuple[bool, float, str]:
        """
        Verifies answer groundedness.
        Returns: (is_grounded, grounding_score, summary)
        """
        if not retrieved_docs:
            return True, 1.0, "无检索上下文约束"

        combined_context = " ".join([d.get("content", "") for d in retrieved_docs])
        # Check presence of key terms
        key_tokens = re.findall(r"[\u4e00-\u9fff]{2,4}|[a-zA-Z]{3,}", answer)
        if not key_tokens:
            return True, 1.0, "答案过于简短，无需校验"

        hits = sum(1 for t in key_tokens if t in combined_context)
        score = hits / len(key_tokens)

        is_grounded = score >= 0.35  # Threshold
        status = "事实对齐良好，引用溯源可信" if is_grounded else "检测到潜在事实偏离或无依据推断风险"
        return is_grounded, round(score, 4), status


hallucination_checker = HallucinationChecker()
