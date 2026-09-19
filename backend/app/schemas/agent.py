from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field
from datetime import datetime


class AgentRunRequest(BaseModel):
    message: str = Field(description="用户输入的内容或指令")
    session_id: Optional[str] = Field(default=None, description="会话ID，若为空则自动新建")
    conversation_id: Optional[str] = Field(default=None, description="历史对话ID，携带时在对应历史对话中继续，为空则新建历史对话")
    user_id: Optional[str] = Field(default="u-001", description="当前登录用户ID，用于检索专属教学画像与记忆约束")
    agent_type: Optional[str] = Field(default="supervisor", description="目标智能体类型: supervisor, lesson_plan, academic_rag, exam_quiz, socratic, math_solver, curriculum, rubric, slide_outline")
    kb_ids: Optional[List[str]] = Field(default=None, description="关联的知识库列表，用于 RAG 检索")
    hitl_auto_approve: bool = Field(default=False, description="是否自动批准人机交互门禁")
    sub_agent_mode: bool = Field(default=False, description="是否强制启用 Orchestrator 多 sub-agent 协作模式（复合请求会自动启用）")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="其他上下文元数据")


class AgentTraceStep(BaseModel):
    step_id: str
    node_name: str
    timestamp: str
    action_type: str  # THINKING, TOOL_CALL, TOOL_RESULT, GENERATION, HITL_GATE
    title: str
    detail: Optional[str] = None
    elapsed_ms: Optional[int] = None
    data: Optional[Dict[str, Any]] = None


class AgentTaskStatus(BaseModel):
    task_id: str
    session_id: str
    thread_id: str
    status: str  # PENDING, RUNNING, WAITING_APPROVAL, COMPLETED, FAILED, TIMED_OUT
    current_node: Optional[str] = None
    progress: int = 0  # 0 - 100
    steps: List[AgentTraceStep] = []
    output_message: Optional[str] = None
    artifact_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class HITLApprovalRequest(BaseModel):
    task_id: str
    approved: bool
    comments: Optional[str] = None
    modified_payload: Optional[Dict[str, Any]] = None


class AgentStreamEvent(BaseModel):
    event_type: str  # "trace", "token", "artifact", "error", "done", "plan_update"
    task_id: str
    payload: Dict[str, Any]


# ==================== Sub-Agent / Orchestrator Schemas ====================


class TaskNode(BaseModel):
    """DAG 中的单个任务节点"""
    task_id: str = Field(description="任务唯一 ID，如 T1")
    agent: str = Field(description="目标 sub-agent 名称，如 lesson_plan")
    input_summary: str = Field(default="", description="任务输入摘要，可引用前置 task_id")
    depends_on: List[str] = Field(default_factory=list, description="依赖的前置 task_id 列表")
    map_count: int = Field(default=1, description="Map-Reduce 并行实例数，>1 时启用投票")


class TaskPlan(BaseModel):
    """LLM 规划输出的完整任务 DAG"""
    plan: List[TaskNode] = Field(default_factory=list, description="任务节点列表")
    schedule: str = Field(default="serial", description="调度模式: serial | parallel | map_reduce")
    aggregation_strategy: str = Field(
        default="concat_with_citations",
        description="聚合策略: concat_with_citations | best_confidence | vote_dedup"
    )


class SubAgentResultSchema(BaseModel):
    """Sub-Agent 执行结果的 Pydantic schema"""
    task_id: str
    agent_name: str
    scope_id: str
    output_preview: str = Field(default="", description="输出 markdown 预览（前 300 字符）")
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1.0)
    token_used: int = Field(default=0)
    status: str = Field(default="DONE", description="DONE | FAILED")
    error_message: Optional[str] = None


class PlanDAGNode(BaseModel):
    """Plan DAG 中前端可渲染的节点"""
    task_id: str
    agent: str
    input_summary: str = ""
    depends_on: List[str] = Field(default_factory=list)
    map_count: int = 1
    status: str = Field(default="PENDING", description="PENDING | RUNNING | DONE | FAILED")


class PlanDAG(BaseModel):
    """前端 Plan DAG 渲染数据"""
    nodes: List[PlanDAGNode] = Field(default_factory=list)
    schedule: str = "serial"
    total_tasks: int = 0


class OrchestratorResult(BaseModel):
    """OrchestratorAgent.run() 的返回 schema"""
    output: str
    agent_type: str = "orchestrator"
    plan_dag: PlanDAG
    sub_results: List[SubAgentResultSchema] = Field(default_factory=list)
    reflection: Dict[str, Any] = Field(default_factory=dict)
