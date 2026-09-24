import operator
from typing import Annotated, Sequence, List, Dict, Any, Optional
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """
    Global LangGraph State for Multi-Agent Collaboration
    Supports:
    - Message history appending
    - Multi-step execution planning
    - Retrieved documents from Hybrid RAG
    - Generated structured artifacts (Lesson plans, exam papers)
    - Human-in-the-loop review status
    - Error & Retry counters
    """
    # Messages list with append reducer
    messages: Annotated[Sequence[Dict[str, Any]], operator.add]
    user_id: str
    session_id: str
    thread_id: str
    
    # Intent & Routing
    intent: str  # lesson_plan | academic_rag | exam_quiz | socratic | math_solver | curriculum | rubric | slide_outline
    current_agent: str
    next_agent: Optional[str]

    # Task decomposition plan
    plan_steps: List[str]
    current_step: int

    # RAG Retrieval Context
    kb_ids: List[str]
    retrieved_docs: List[Dict[str, Any]]
    citations: List[Dict[str, Any]]

    # Artifacts & Outputs
    structured_artifact: Optional[Dict[str, Any]]
    artifact_type: Optional[str]
    final_markdown_output: str

    # Human-In-The-Loop (HITL) Gate
    requires_approval: bool
    is_approved: bool
    reviewer_comments: Optional[str]

    # Error handling & Recovery
    retry_count: int
    error_message: Optional[str]

    # ---- LangGraph 运行时字段 ----
    quality_score: float                      # QualityReview 综合打分 [0,1]
    needs_revision: bool                      # 是否需要返工
    revision_history: Annotated[List[str], operator.add]  # 每次返工的问题描述（追加）
    sub_results: Annotated[List[Dict[str, Any]], operator.add]  # 并行 sub-agent 结果（Map-Reduce，追加合并）
    plan_dag: Optional[Dict[str, Any]]        # Orchestrator 拆解的 DAG
    human_decision: Optional[Dict[str, Any]]  # HITL 恢复时注入的教师决策 {approved, comments}
    sub_agent_mode: bool                      # 是否强制 Orchestrator 多 sub-agent 协作
    hitl_auto_approve: bool                   # True 时跳过 HITL 暂停（同步链路兼容旧行为）
    raw_user_message: str                     # 脱敏后的用户原文（未挂记忆 prompt），供意图拆解使用
