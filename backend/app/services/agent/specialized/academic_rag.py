import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter
from app.prompts.registry import prompt_registry
from app.services.llm.prompt_router import prompt_router
from app.services.rag.hybrid_search import hybrid_search_engine
from app.services.rag.reranker import bge_reranker
from app.services.rag.hallucination import hallucination_checker
from app.harness.fingerprint import get_fingerprint_guard

logger = logging.getLogger(__name__)


class AcademicRAGAgent(SubAgent):
    """
    学术研读检索智能体 (AcademicRAG)
    Executes advanced educational research retrieval:
    1. Hybrid Search (Milvus 2.4 dense + BM25 sparse)
    2. Cross-Encoder reranking (BGE-Reranker-Large)
    3. Grounding check & Citation attribution binding
    """

    agent_name = "academic_rag"

    SYSTEM_PROMPT = prompt_registry.render("academic_rag.system")

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        query = state["messages"][-1]["content"] if state["messages"] else ""
        kb_ids = state.get("kb_ids") or []

        # 1. Hybrid Search (Top-20 candidates)
        #    指纹去重：同任务内重复检索（等价 query）直接短路
        guard = get_fingerprint_guard(state["session_id"])
        candidates = await guard.execute(
            "hybrid_search",
            {"query": query, "kb_ids": kb_ids, "top_k": 20},
            lambda: hybrid_search_engine.search(query, kb_ids=kb_ids, top_k=20),
        )

        # 2. BGE Reranker (Top-5 precision chunks)
        reranked_docs = bge_reranker.rerank(query, candidates, top_n=5)

        # 3. Context assembly
        context_str = ""
        for idx, doc in enumerate(reranked_docs):
            context_str += f"[{idx + 1}] 文献切片 (页码 P{doc.get('page_number', 1)}):\n{doc.get('content', '')}\n\n"

        prompt = (
            f"【用户学术咨询】: {query}\n\n"
            f"【参考权威文献资料】:\n{context_str}\n\n"
            "请给出严谨的学术综述回答，并在关键论点末尾准确标注引用标号 [1], [2] 等。"
        )

        model = ModelRouter.route_model("academic_rag", len(prompt))
        messages = [
            {"role": "system", "content": prompt_router.build_system(
                "academic_rag.system", query, user_id=state.get("user_id"))},
            {"role": "user", "content": prompt}
        ]

        resp = await bailian_client.acomplete(messages, model=model, temperature=0.2)
        answer = resp["content"]

        # 4. Extract citations and check grounding
        citations = hallucination_checker.extract_citations(answer, reranked_docs)
        is_grounded, g_score, g_status = await hallucination_checker.verify_grounding(answer, reranked_docs)

        logger.info(f"[AcademicRAG] Completed RAG synthesis. Citations: {len(citations)}, Grounding: {g_score}")

        return {
            "current_agent": "academic_rag",
            "next_agent": "quality_gate",
            "retrieved_docs": reranked_docs,
            "citations": citations,
            "final_markdown_output": answer,
            "structured_artifact": {
                "title": "学术文献研读成果",
                "citations": citations,
                "grounding_status": g_status,
                "grounding_score": g_score,
                "markdown": answer
            },
            "artifact_type": "ACADEMIC_RAG"
        }


academic_rag_agent = AcademicRAGAgent()
SubAgentRegistry.register("academic_rag", AcademicRAGAgent)
