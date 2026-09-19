import math
import re
from typing import List, Dict, Any, Optional
from rank_bm25 import BM25Okapi
from app.services.rag.milvus_manager import milvus_manager
from app.services.llm.bailian_client import bailian_client


class HybridSearchEngine:
    """
    Two-Stage Hybrid Search Pipeline:
    1. Sparse Retrieval (BM25 for exact keyword & LaTeX terminology matching)
    2. Dense Vector Retrieval (Milvus 2.4 + Text-Embedding-v3 for semantic intent)
    3. Reciprocal Rank Fusion (RRF) & Weighted Score Fusion
    """

    def __init__(self):
        self._bm25_index: Optional[BM25Okapi] = None
        self._corpus_chunks: List[Dict[str, Any]] = []

    def index_for_bm25(self, chunks: List[Dict[str, Any]]):
        """Builds in-memory BM25 index for uploaded or stored chunks."""
        self._corpus_chunks = chunks
        tokenized_corpus = [self._tokenize(c["content"]) for c in chunks]
        if tokenized_corpus:
            self._bm25_index = BM25Okapi(tokenized_corpus)

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize Chinese characters and English/LaTeX terms."""
        # Clean text
        text = re.sub(r"\s+", " ", text)
        # Split Chinese chars individually, keep English words/math tokens intact
        tokens = []
        for word in re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9_\\]+|\$[^\$]+\$", text):
            tokens.append(word.lower())
        return tokens or [""]

    async def search(
        self,
        query: str,
        kb_ids: Optional[List[str]] = None,
        top_k: int = 10,
        rrf_k: int = 60,
        sparse_weight: float = 0.4,
        dense_weight: float = 0.6
    ) -> List[Dict[str, Any]]:
        """
        Executes parallel hybrid search (BM25 + Dense) and merges using RRF.
        """
        # 1. Dense retrieval via Milvus
        q_vec = await bailian_client.get_embedding(query)
        dense_hits = milvus_manager.search_vector(
            query_vector=q_vec,
            top_k=top_k * 2,
            kb_ids=kb_ids
        )

        # 2. Sparse retrieval via BM25
        sparse_hits = []
        if self._bm25_index and self._corpus_chunks:
            q_tokens = self._tokenize(query)
            scores = self._bm25_index.get_scores(q_tokens)
            ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            for idx in ranked_indices[:top_k * 2]:
                if scores[idx] > 0.01:
                    chunk = self._corpus_chunks[idx]
                    sparse_hits.append({
                        "chunk_id": chunk.get("chunk_id", str(idx)),
                        "kb_id": chunk.get("kb_id", ""),
                        "doc_id": chunk.get("doc_id", ""),
                        "content": chunk["content"],
                        "page_number": chunk.get("page_number", 1),
                        "score": round(float(scores[idx]), 4)
                    })

        # 3. Reciprocal Rank Fusion (RRF)
        fused_scores: Dict[str, Dict[str, Any]] = {}

        # Merge dense ranks
        for rank, hit in enumerate(dense_hits):
            cid = hit["chunk_id"]
            rrf_score = dense_weight * (1.0 / (rrf_k + rank + 1))
            if cid not in fused_scores:
                fused_scores[cid] = {
                    "chunk": hit,
                    "dense_score": hit["score"],
                    "sparse_score": 0.0,
                    "rrf_score": rrf_score
                }
            else:
                fused_scores[cid]["dense_score"] = hit["score"]
                fused_scores[cid]["rrf_score"] += rrf_score

        # Merge sparse ranks
        for rank, hit in enumerate(sparse_hits):
            cid = hit["chunk_id"]
            rrf_score = sparse_weight * (1.0 / (rrf_k + rank + 1))
            if cid not in fused_scores:
                fused_scores[cid] = {
                    "chunk": hit,
                    "dense_score": 0.0,
                    "sparse_score": hit["score"],
                    "rrf_score": rrf_score
                }
            else:
                fused_scores[cid]["sparse_score"] = hit["score"]
                fused_scores[cid]["rrf_score"] += rrf_score

        # Sort by fused score
        sorted_fused = sorted(fused_scores.values(), key=lambda x: x["rrf_score"], reverse=True)

        results = []
        for item in sorted_fused[:top_k]:
            c = item["chunk"]
            results.append({
                "chunk_id": c["chunk_id"],
                "kb_id": c.get("kb_id", ""),
                "doc_id": c.get("doc_id", ""),
                "content": c["content"],
                "page_number": c.get("page_number", 1),
                "dense_score": item["dense_score"],
                "sparse_score": item["sparse_score"],
                "final_score": round(item["rrf_score"] * 100, 4)
            })

        # If both indices were empty, provide context from fallback
        if not results and dense_hits:
            return dense_hits[:top_k]

        return results


hybrid_search_engine = HybridSearchEngine()
