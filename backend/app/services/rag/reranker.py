import logging
import math
from typing import List, Dict, Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class BGERerankerAdapter:
    """
    Cross-Encoder Reranker (BGE-Reranker)

    真 BGE 重排：把 Query 与 Document 拼成 [CLS] Q [SEP] D [SEP] 送入 CrossEncoder，
    取 <[BOS_never_used_51bce0c785ca2f68081bfa7d91973934]> logit 经 sigmoid 归一化为 [0,1] 相关性分数，消除 bi-encoder 语义压缩损失。

    设计要点：
    - 懒加载：首次 rerank 才下载/加载模型，避免服务启动被阻塞
    - 进程内单例缓存：模型实例挂在类属性，多次调用不重复加载
    - 失败自动降级：依赖缺失 / 模型下载失败 / 推理异常时回退启发式评分，保证 RAG 链路可用
    - 批量推理：所有 (query, doc) pairs 一次 predict，比逐条快一个数量级
    - 小候选集短路：候选数 <= top_n 时不浪费模型算力，直接按已有分数排序
    """

    # 进程内模型缓存（类属性，所有实例共享）
    _model: Optional[Any] = None
    _model_loading: bool = False
    _model_load_failed: bool = False

    def __init__(self):
        self.model_name = settings.RERANKER_MODEL_NAME
        self.batch_size = settings.RERANKER_BATCH_SIZE
        self.cache_dir = settings.RERANKER_CACHE_DIR

    def _ensure_model(self) -> Optional[Any]:
        """
        懒加载 CrossEncoder 模型。返回模型实例或 None（不可用）。
        首次失败后标记 _model_load_failed，后续调用直接降级，不重复尝试。
        """
        if BGERerankerAdapter._model is not None:
            return BGERerankerAdapter._model
        if BGERerankerAdapter._model_load_failed:
            return None
        if not settings.RERANKER_ENABLE_BGE:
            BGERerankerAdapter._model_load_failed = True
            logger.info("[Reranker] RERANKER_ENABLE_BGE=False，使用启发式降级重排")
            return None

        # 防止并发重复加载
        if BGERerankerAdapter._model_loading:
            return None
        BGERerankerAdapter._model_loading = True

        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            BGERerankerAdapter._model_load_failed = True
            BGERerankerAdapter._model_loading = False
            logger.warning(
                "[Reranker] 未安装 sentence-transformers，回退启发式重排。"
                "请执行 pip install sentence-transformers 后重启以启用真 BGE 重排。"
            )
            return None

        try:
            logger.info(f"[Reranker] 正在加载 CrossEncoder 模型: {self.model_name} (首次调用，可能需要下载)...")
            model = CrossEncoder(
                self.model_name,
                device=None,                  # None=自动选 GPU/CPU
                cache_folder=self.cache_dir,
            )
            BGERerankerAdapter._model = model
            BGERerankerAdapter._model_loading = False
            logger.info(f"[Reranker] CrossEncoder 模型加载成功: {self.model_name}")
            return model
        except Exception as e:
            BGERerankerAdapter._model_load_failed = True
            BGERerankerAdapter._model_loading = False
            logger.warning(
                f"[Reranker] 模型加载失败 ({type(e).__name__}: {e})，回退启发式重排。"
                f"可检查网络（HF 下载）或设置 RERANKER_ENABLE_BGE=False。"
            )
            return None

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_n: int = 5
    ) -> List[Dict[str, Any]]:
        if not candidates:
            return []

        # 候选数不足 top_n：不浪费模型算力，直接按已有分数排序
        if len(candidates) <= top_n:
            return sorted(candidates, key=lambda x: x.get("final_score", 0), reverse=True)

        model = self._ensure_model()
        contents = [doc.get("content", "") for doc in candidates]

        if model is not None:
            scores = self._cross_encode_scores(model, query, contents)
        else:
            scores = [self._heuristic_cross_score(query, c) for c in contents]

        scored_candidates = []
        for doc, score in zip(candidates, scores):
            doc_copy = dict(doc)
            doc_copy["rerank_score"] = round(float(score), 4)
            scored_candidates.append(doc_copy)

        scored_candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored_candidates[:top_n]

    def _cross_encode_scores(self, model, query: str, contents: List[str]) -> List[float]:
        """
        批量 CrossEncoder 推理 + sigmoid 归一化。
        返回与 contents 等长的 [0,1] 分数列表。
        推理异常时回退启发式。
        """
        pairs = [(query, c) for c in contents]
        try:
            logits = model.predict(
                pairs,
                batch_size=self.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            # sigmoid 归一化到 [0,1]
            scores = [1.0 / (1.0 + math.exp(-float(x))) for x in logits]
            return scores
        except Exception as e:
            logger.warning(f"[Reranker] CrossEncoder 推理异常 ({type(e).__name__}: {e})，回退启发式")
            return [self._heuristic_cross_score(query, c) for c in contents]

    def _heuristic_cross_score(self, query: str, content: str) -> float:
        """
        降级用的启发式交叉相关性评分（原实现保留）：
        - 词项覆盖率
        - 公式符号匹配奖励
        - 教学关键章节奖励
        """
        q_clean = set(query.lower().split())
        c_clean = content.lower()

        overlap = sum(1 for term in q_clean if term in c_clean)
        coverage = overlap / (len(q_clean) or 1)

        if "$" in query and "$" in content:
            coverage += 0.15
        if any(h in content for h in ["教学目标", "重点", "难点", "公式", "例题"]):
            coverage += 0.1

        return min(0.99, max(0.01, coverage * 0.8 + 0.15))


bge_reranker = BGERerankerAdapter()
