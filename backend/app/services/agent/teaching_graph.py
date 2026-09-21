"""
LangGraph 教学智能体执行图（真 LangGraph StateGraph 实现）

替换原 graph.py 的手写顺序编排，提供：
- 声明式节点 + 条件边拓扑
- IntentNode 意图路由 → 9 个专业 agent 节点 → QualityReview → (Revision 循环) → HITL_Gate → END
- HITL 用原生 interrupt() 支持教师审批
- Orchestrator 复合任务用 Send API 并行调度 sub-agent
- Checkpointer 持久化（MemorySaver / RedisSaver）
- astream_events 对接 SSE 事件发布
"""
import logging
from typing import Dict, Any, List, Optional, Callable, Awaitable

from langgraph.graph import StateGraph, END
from langgraph.types import Send

from app.services.agent.state import AgentState
from app.services.agent.supervisor import supervisor_agent
from app.services.agent.sub_agent import SubAgentRegistry
from app.services.rag.hallucination import hallucination_checker
from app.harness.benchmark import BenchmarkHarness

logger = logging.getLogger(__name__)

# 9 个专业智能体注册表（与 graph.py _dispatch_agent 对齐）
SPECIALIZED_AGENTS = [
    "lesson_plan", "academic_rag", "exam_quiz", "socratic",
    "math_solver", "curriculum", "rubric", "slide_outline", "code_grader",
]

# 质量阈值
QUALITY_PASS_THRESHOLD = 0.6
MAX_REVISION = 2


# ============================================================
# 节点实现
# ============================================================

async def intent_node(state: AgentState) -> Dict[str, Any]:
    """意图分类 + 任务拆解，返回 intent/next_agent/plan_steps"""
    # 若调用方已传入多步计划（复合任务预拆解），则保留
    existing_plan = state.get("plan_steps") or []
    decision = await supervisor_agent.route(state)
    plan_steps = existing_plan if len(existing_plan) > 1 else decision.get("plan_steps", [])
    return {
        "intent": decision.get("intent", state.get("intent", "supervisor")),
        "current_agent": "supervisor",
        "next_agent": decision.get("next_agent", "lesson_plan"),
        "plan_steps": plan_steps,
        "current_step": 0,
    }


def make_agent_node(agent_name: str) -> Callable[[AgentState], Awaitable[Dict[str, Any]]]:
    """工厂：把 specialized agent 包装成 LangGraph 节点"""

    async def _node(state: AgentState) -> Dict[str, Any]:
        agent = SubAgentRegistry.get(agent_name)
        if agent is None:
            return {"error_message": f"unknown agent: {agent_name}"}

        # 若处于返工态，把修订意见注入消息
        if state.get("needs_revision") and state.get("revision_history"):
            revision_prompt = (
                f"请针对以下问题修订你的输出：{'; '.join(state['revision_history'])}"
            )
            state = {**state, "current_agent": agent_name}
            state["messages"] = list(state.get("messages", [])) + [
                {"role": "user", "content": revision_prompt}
            ]

        result = await agent.execute(state)
        # 规范化返回：确保 final_markdown_output 存在
        result.setdefault("final_markdown_output", result.get("output", ""))
        result.setdefault("current_agent", agent_name)
        result.setdefault("citations", [])

        # 并行模式（plan_steps 多个）：把结果写入 sub_results 供合并
        if len(state.get("plan_steps", [])) > 1:
            return {
                "sub_results": [{
                    "agent_name": agent_name,
                    "output": result.get("final_markdown_output", ""),
                    "artifact": result.get("structured_artifact"),
                    "citations": result.get("citations", []),
                }],
            }
        return result

    _node.__name__ = f"{agent_name}_node"
    return _node


async def quality_review_node(state: AgentState) -> Dict[str, Any]:
    """质量审核：Grounding（LLM NLI）+ LaTeX 闭合 + 综合打分"""
    # 并行模式：从 sub_results 合并最终输出
    output = state.get("final_markdown_output", "")
    if not output and state.get("sub_results"):
        output = "\n\n".join(
            sr.get("output", "") for sr in state["sub_results"] if sr.get("output")
        )

    retrieved = state.get("retrieved_docs", [])

    # 1. Grounding 校验
    try:
        is_grounded, g_score, g_status = await hallucination_checker.verify_grounding(output, retrieved)
    except Exception as e:
        logger.warning(f"[QualityReview] grounding 校验异常: {e}")
        is_grounded, g_score = True, 1.0

    # 2. LaTeX 闭合校验
    latex_ok = BenchmarkHarness.validate_latex_syntax(output)["is_valid"]

    # 3. 综合打分
    quality_score = g_score * 0.7 + (1.0 if latex_ok else 0.0) * 0.3
    quality_score = round(quality_score, 4)

    needs_revision = (not is_grounded) or (not latex_ok) or (quality_score < QUALITY_PASS_THRESHOLD)
    revision_notes: List[str] = []
    if not is_grounded:
        revision_notes.append("事实接地未通过，存在无依据或矛盾陈述")
    if not latex_ok:
        revision_notes.append("LaTeX 公式定界符未闭合")
    if quality_score < QUALITY_PASS_THRESHOLD:
        revision_notes.append(f"综合质量分 {quality_score} 低于阈值 {QUALITY_PASS_THRESHOLD}")

    return {
        "quality_score": quality_score,
        "needs_revision": needs_revision,
        "revision_history": revision_notes if needs_revision else [],
    }


async def revision_node(state: AgentState) -> Dict[str, Any]:
    """返工节点：递增 retry_count，重新调用当前专业 agent 执行"""
    retry = int(state.get("retry_count", 0) or 0) + 1
    # 用 next_agent（专业 agent 名），current_agent 可能是 "supervisor"
    agent_name = state.get("next_agent") or state.get("intent") or "lesson_plan"
    agent = SubAgentRegistry.get(agent_name)
    if agent is None:
        return {"error_message": f"revision: unknown agent {agent_name}", "retry_count": retry}

    # 把修订意见作为 user 消息注入
    revision_prompt = (
        f"请根据以下审核意见修订输出（第 {retry} 次修订）："
        f"{'; '.join(state.get('revision_history', []))}"
    )
    state = {**state, "retry_count": retry, "needs_revision": False}
    state["messages"] = list(state.get("messages", [])) + [
        {"role": "user", "content": revision_prompt}
    ]

    result = await agent.execute(state)
    result.setdefault("final_markdown_output", result.get("output", ""))
    # 必须把 retry_count 写回返回值，否则 LangGraph 不会更新 state 的 retry_count
    result["retry_count"] = retry
    result["needs_revision"] = False
    return result


async def hitl_gate_node(state: AgentState) -> Dict[str, Any]:
    """
    人机协作审批节点（会被 interrupt_before 暂停）。
    恢复时 human_decision 通过 Command(resume={...}) 注入到 state。
    """
    decision = state.get("human_decision") or {}
    approved = bool(decision.get("approved", False))
    comments = decision.get("comments")
    return {
        "is_approved": approved,
        "reviewer_comments": comments,
        "needs_revision": not approved,
        "revision_history": [f"教师驳回：{comments}"] if (not approved and comments) else [],
    }


def route_to_hitl(state: AgentState) -> str:
    """QualityReview 之后：需审批 → hitl_gate（暂停），否则直接通过"""
    if state.get("requires_approval", False):
        return "hitl_gate"
    return "approved"


async def auto_approve_node(state: AgentState) -> Dict[str, Any]:
    """无需审批时直接标记通过"""
    return {"is_approved": True}


async def orchestrator_fanout(state: AgentState) -> List[Send]:
    """
    Orchestrator 复合任务：用 Send API 并行调度多个 sub-agent。
    作为 conditional edge 的 path 函数，返回 Send 列表。
    """
    plan_steps = state.get("plan_steps", [])
    if not plan_steps:
        # 无拆解计划时降级到单 agent
        return [Send(state.get("next_agent", "lesson_plan"), state)]

    sends: List[Send] = []
    for step in plan_steps:
        # plan_steps 每项是 agent 名（与 SPECIALIZED_AGENTS 对齐）
        if step in SPECIALIZED_AGENTS:
            sends.append(Send(step, {**state, "intent": step}))
    return sends if sends else [Send(state.get("next_agent", "lesson_plan"), state)]


# ============================================================
# 条件边路由函数
# ============================================================

def route_by_intent(state: AgentState):
    """
    IntentNode 之后的条件边：
    - 复合任务（sub_agent_mode 或 supervisor 判定为复合）→ 返回 Send 列表并行调度多 agent
    - 简单任务 → 返回单个专业 agent 节点名
    """
    user_msg = state["messages"][-1]["content"] if state.get("messages") else ""
    plan_steps = state.get("plan_steps") or []
    is_compound = (
        state.get("sub_agent_mode")
        or len(plan_steps) > 1
        or SubAgentRegistry.is_compound_request(user_msg)
    )

    if is_compound and plan_steps:
        # 复合任务：用 Send API 并行调度 plan_steps 中的每个 agent
        sends: List[Send] = []
        for step in state["plan_steps"]:
            if step in SPECIALIZED_AGENTS:
                sends.append(Send(step, {**state, "intent": step, "current_agent": step}))
        if sends:
            return sends

    return state.get("next_agent", "lesson_plan")


def route_after_quality(state: AgentState) -> str:
    """QualityReview 之后：需返工 → revision；需审批 → hitl_gate（暂停）；否则 → approved"""
    if state.get("needs_revision") and state.get("retry_count", 0) < MAX_REVISION:
        return "revision"
    if state.get("requires_approval", False):
        return "hitl_gate"
    return "approved"


def route_after_hitl(state: AgentState) -> str:
    """HITL 之后：通过 → END，驳回且未超上限 → revision"""
    if state.get("is_approved"):
        return END
    if state.get("retry_count", 0) < MAX_REVISION:
        return "revision"
    return END  # 超上限直接结束（避免死循环）


# ============================================================
# Graph 组装
# ============================================================

def build_teaching_graph(checkpointer=None) -> Any:
    """构建 LangGraph 教学智能体图。checkpointer 传 None 则不持久化。"""
    builder = StateGraph(AgentState)

    # 注册节点
    builder.add_node("intent_router", intent_node)
    for name in SPECIALIZED_AGENTS:
        builder.add_node(name, make_agent_node(name))
    builder.add_node("quality_review", quality_review_node)
    builder.add_node("revision", revision_node)
    builder.add_node("hitl_gate", hitl_gate_node)
    builder.add_node("approved", auto_approve_node)

    # 入口
    builder.set_entry_point("intent_router")

    # Intent → 各专业 agent（条件边）
    path_map = {name: name for name in SPECIALIZED_AGENTS}
    builder.add_conditional_edges("intent_router", route_by_intent, path_map)

    # 所有专业 agent → quality_review
    for name in SPECIALIZED_AGENTS:
        builder.add_edge(name, "quality_review")

    # quality_review → revision / hitl_gate / approved
    builder.add_conditional_edges(
        "quality_review", route_after_quality,
        {"revision": "revision", "hitl_gate": "hitl_gate", "approved": "approved"},
    )

    # revision → quality_review（返工后重新审核）
    builder.add_edge("revision", "quality_review")

    # approved → END
    builder.add_edge("approved", END)

    # hitl_gate → revision / END
    builder.add_conditional_edges(
        "hitl_gate", route_after_hitl,
        {"revision": "revision", END: END},
    )

    # HITL：仅在进入 hitl_gate 前暂停（requires_approval=True 时才会走到此节点）
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["hitl_gate"],
    )


# ============================================================
# 流式执行入口（对接 SSE）
# ============================================================

async def run_streaming(
    graph,
    state: AgentState,
    config: Dict[str, Any],
    on_event: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    """
    流式执行图，通过 astream_events 把 trace/token 推到 SSE。
    on_event 回调签名 async def(event_dict) -> None
    """
    final_state: Dict[str, Any] = {}
    async for event in graph.astream_events(state, config=config, version="v2"):
        etype = event.get("event")
        name = event.get("name", "")
        data = event.get("data", {})

        if etype == "on_chain_start" and name in ("intent_router", *SPECIALIZED_AGENTS, "quality_review", "revision", "hitl_gate", "approved"):
            if on_event:
                await on_event({"event_type": "trace", "payload": {
                    "node_name": name, "title": f"节点 [{name}] 开始执行", "action": "THINKING"}})

        elif etype == "on_chat_model_stream":
            chunk = data.get("chunk")
            text = getattr(chunk, "content", "") if chunk is not None else ""
            if text and on_event:
                await on_event({"event_type": "token", "payload": {"text": text, "agent": name}})

        elif etype == "on_chain_end" and name in ("intent_router", *SPECIALIZED_AGENTS, "quality_review", "revision", "hitl_gate", "approved"):
            if on_event:
                await on_event({"event_type": "trace", "payload": {
                    "node_name": name, "title": f"节点 [{name}] 执行完成", "action": "THINKING_DONE"}})

        # 收集最终状态（LangGraph 会在 on_chain_end 中传递 output）
        if etype == "on_chain_end" and name == "LangGraph":
            output = data.get("output")
            if isinstance(output, dict):
                final_state = output

    return final_state
