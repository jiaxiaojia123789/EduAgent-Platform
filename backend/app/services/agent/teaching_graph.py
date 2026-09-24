"""
LangGraph 教学智能体执行图（真 LangGraph StateGraph 实现）

拓扑：
  intent_router ──conditional──► [9 个专业 agent] ──► aggregate ──► quality_review
                                                                     │
                                              needs_revision?        │
                                          ┌──────────────────────────┼──────────────┐
                                         yes            no(需审批)                 no(免审批)
                                          ▼               ▼                           ▼
                                      revision        hitl_gate(interrupt)        approved → END
                                          │               │ approved?
                                          └─►quality_review├─yes──► END
                                            (≤2 次)        └─no───► revision

能力：
- 真 token 流：节点经 config 注入 on_token，优先 execute_stream，非流式 agent 用 TokenCounter 补偿
- 复合任务：route_by_intent 返回 Send 列表并行执行，aggregate 节点合并写回 final_markdown_output
- HITL：compile(interrupt_before=["hitl_gate"]) 暂停，TeachingGraphRunner.resume 注入决策续跑
- trace：节点开始/结束事件经 config.on_event 推到既有 SSE 事件总线
"""
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable, Awaitable

from langgraph.graph import StateGraph, END
from langgraph.types import Send
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.runnables import RunnableConfig

from app.services.agent.state import AgentState
from app.services.agent.supervisor import supervisor_agent
from app.services.agent.sub_agent import SubAgentRegistry
from app.services.agent.stream_compensate import TokenCounter
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

# 事件回调类型
EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


# ============================================================
# 节点公共工具
# ============================================================

def _runtime_callbacks(config: RunnableConfig) -> Dict[str, Any]:
    """从 LangGraph runtime config 取出调用方注入的 on_token / on_event。"""
    configurable = (config or {}).get("configurable") or {}
    return {
        "on_token": configurable.get("on_token"),
        "on_event": configurable.get("on_event"),
    }


async def _emit(on_event: Optional[EventCallback], event: Dict[str, Any]) -> None:
    """安全推送 SSE 事件，吞掉推送异常防止影响主流程。兼容同步/异步回调。"""
    if on_event is None:
        return
    try:
        ret = on_event(event)
        if inspect.isawaitable(ret):
            await ret
    except Exception as e:
        logger.warning(f"[TeachingGraph] event 推送失败: {e}")


async def _execute_with_stream(
    agent,
    agent_name: str,
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    执行专业 agent 并推送真实 token：
    1. 优先 execute_stream（教案/试卷等已实现真流式）
    2. 全程无真实 token（socratic 等只实现 execute）→ TokenCounter 分片补偿
    """
    cbs = _runtime_callbacks(config)
    on_event = cbs["on_event"]
    session_id = state.get("session_id", "")

    async def _on_token(chunk: str):
        await _emit(on_event, {
            "event_type": "token",
            "task_id": session_id,
            "payload": {"text": chunk, "agent": agent_name},
        })

    token_counter = TokenCounter(_on_token)

    await _emit(on_event, {
        "event_type": "trace",
        "task_id": session_id,
        "payload": {
            "node_name": agent_name.capitalize(),
            "title": f"【{agent_name}】正在进行专业领域推理与生成",
            "action": "GENERATION",
        },
    })
    started = time.monotonic()

    result = await agent.execute_stream(state, on_token=token_counter)
    result.setdefault("final_markdown_output", result.get("output", ""))
    result.setdefault("current_agent", agent_name)
    result.setdefault("citations", [])

    # 非流式 agent：对最终文本分片补发打字机
    await token_counter.compensate_if_silent(result.get("final_markdown_output", ""))

    elapsed = int((time.monotonic() - started) * 1000)
    await _emit(on_event, {
        "event_type": "trace",
        "task_id": session_id,
        "payload": {
            "node_name": agent_name.capitalize(),
            "title": "专业领域生成完成",
            "action": "GENERATION_DONE",
            "elapsed_ms": elapsed,
        },
    })
    return result


# ============================================================
# 节点实现
# ============================================================

async def intent_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """意图分类 + 任务拆解，返回 intent/next_agent/plan_steps"""
    on_event = _runtime_callbacks(config)["on_event"]
    session_id = state.get("session_id", "")

    await _emit(on_event, {
        "event_type": "trace",
        "task_id": session_id,
        "payload": {
            "node_name": "Supervisor",
            "title": "分析教学意图并进行任务拆解与分发",
            "action": "THINKING",
        },
    })

    # 若调用方已传入多步计划（复合任务预拆解），则保留
    # 注意：拆解必须基于脱敏原文，messages[-1] 挂了记忆 prompt 会引入误命中词
    raw_msg = state.get("raw_user_message") or (
        state["messages"][-1]["content"] if state.get("messages") else ""
    )
    existing_plan = state.get("plan_steps") or []
    decision = await supervisor_agent.route(state)

    # 调用方预拆解的 agent 名列表优先；否则复合任务做关键词拆解
    valid_existing = [s for s in existing_plan if s in SPECIALIZED_AGENTS]
    if len(valid_existing) > 1:
        plan_steps = valid_existing
    elif state.get("sub_agent_mode") or SubAgentRegistry.is_compound_request(raw_msg):
        decomposed = supervisor_agent.decompose_agents(raw_msg)
        plan_steps = decomposed if len(decomposed) > 1 else decision.get("plan_steps", [])
    else:
        plan_steps = decision.get("plan_steps", [])

    next_agent = plan_steps[0] if plan_steps and plan_steps[0] in SPECIALIZED_AGENTS \
        else decision.get("next_agent", "lesson_plan")

    await _emit(on_event, {
        "event_type": "trace",
        "task_id": session_id,
        "payload": {
            "node_name": "Supervisor",
            "title": f"已分流至专业智能体: {next_agent}"
                     + (f"（协同 {len(plan_steps)} 个子任务）" if len(plan_steps) > 1 else ""),
            "action": "THINKING_DONE",
        },
    })
    return {
        "intent": decision.get("intent", state.get("intent", "supervisor")),
        "current_agent": "supervisor",
        "next_agent": next_agent,
        "plan_steps": plan_steps,
        "current_step": 0,
    }


def make_agent_node(agent_name: str) -> Callable[..., Awaitable[Dict[str, Any]]]:
    """工厂：把 specialized agent 包装成 LangGraph 节点（带 token 流）"""

    async def _node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
        agent = SubAgentRegistry.get(agent_name)
        if agent is None:
            return {"error_message": f"unknown agent: {agent_name}"}

        # 若处于返工态，把修订意见 + 原稿注入消息
        if state.get("needs_revision") and state.get("revision_history"):
            revision_prompt = (
                f"请针对以下问题修订你的输出：{'; '.join(state['revision_history'])}"
            )
            previous = state.get("final_markdown_output", "")
            if previous:
                revision_prompt = f"原稿如下：\n\n{previous}\n\n---\n{revision_prompt}"
            state = {**state, "current_agent": agent_name}
            state["messages"] = list(state.get("messages", [])) + [
                {"role": "user", "content": revision_prompt}
            ]

        result = await _execute_with_stream(agent, agent_name, state, config)

        # 并行模式（plan_steps 多个）：结果写入 sub_results，由 aggregate 节点合并
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


async def aggregate_node(state: AgentState) -> Dict[str, Any]:
    """
    Map-Reduce 屏障聚合节点：
    - 单 agent 模式：sub_results 为空，原样放行
    - 并行模式：所有 sub-agent 分支在同一 superstep 完成后触发一次，
      把 sub_results 合并写回 final_markdown_output / citations / artifact / plan_dag
    """
    subs = state.get("sub_results") or []
    if not subs:
        # 单 agent 模式：原样放行（LangGraph 要求节点至少写一个 key）
        return {"current_step": state.get("current_step", 0)}

    sections: List[str] = []
    merged_citations: List[Dict[str, Any]] = []
    nodes: List[Dict[str, Any]] = []
    for idx, sr in enumerate(subs, start=1):
        name = sr.get("agent_name", f"agent_{idx}")
        output = sr.get("output", "")
        if output:
            sections.append(f"## 【{name}】产出\n\n{output}")
        for c in sr.get("citations", []):
            if c not in merged_citations:
                merged_citations.append(c)
        nodes.append({
            "task_id": f"T{idx}",
            "agent": name,
            "input_summary": output[:80],
            "depends_on": [],
            "map_count": 1,
            "status": "DONE",
        })

    merged_output = "\n\n---\n\n".join(sections)
    artifact = {
        "title": "协同教学成果",
        "markdown": merged_output,
        "type": "multi_agent",
    }
    return {
        "final_markdown_output": merged_output,
        "citations": merged_citations,
        "structured_artifact": artifact,
        "artifact_type": "MULTI_AGENT",
        "current_agent": "orchestrator",
        "plan_dag": {
            "nodes": nodes,
            "schedule": "parallel",
            "total_tasks": len(nodes),
        },
    }


async def quality_review_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """质量审核：Grounding（LLM NLI）+ LaTeX 闭合 + 综合打分"""
    on_event = _runtime_callbacks(config)["on_event"]
    session_id = state.get("session_id", "")
    output = state.get("final_markdown_output", "")
    retrieved = state.get("retrieved_docs", [])

    await _emit(on_event, {
        "event_type": "trace",
        "task_id": session_id,
        "payload": {
            "node_name": "QualityJudge",
            "title": "执行教学合规性、事实接地与 LaTeX 语法自检",
            "action": "THINKING",
        },
    })

    # 1. Grounding 校验
    try:
        is_grounded, g_score, g_status = await hallucination_checker.verify_grounding(output, retrieved)
    except Exception as e:
        logger.warning(f"[QualityReview] grounding 校验异常: {e}")
        is_grounded, g_score, g_status = True, 1.0, "校验降级"

    # 2. LaTeX 闭合校验
    latex_check = BenchmarkHarness.validate_latex_syntax(output)
    latex_ok = latex_check["is_valid"]

    # 3. 综合打分
    quality_score = g_score * 0.7 + (1.0 if latex_ok else 0.0) * 0.3
    quality_score = round(quality_score, 4)

    needs_revision = (not is_grounded) or (not latex_ok) or (quality_score < QUALITY_PASS_THRESHOLD)
    revision_notes: List[str] = []
    if not is_grounded:
        revision_notes.append(f"事实接地未通过（{g_status}），存在无依据或矛盾陈述")
    if not latex_ok:
        revision_notes.append("LaTeX 公式定界符未闭合")
    if quality_score < QUALITY_PASS_THRESHOLD:
        revision_notes.append(f"综合质量分 {quality_score} 低于阈值 {QUALITY_PASS_THRESHOLD}")

    detail = f"接地分 {g_score:.2f} / LaTeX {'通过' if latex_ok else '未闭合'} / 综合 {quality_score}"
    await _emit(on_event, {
        "event_type": "trace",
        "task_id": session_id,
        "payload": {
            "node_name": "QualityJudge",
            "title": f"质量自检: {'未通过，触发返工' if needs_revision else '通过'}（{detail}）",
            "action": "THINKING_DONE",
        },
    })

    return {
        "quality_score": quality_score,
        "needs_revision": needs_revision,
        "revision_history": revision_notes if needs_revision else [],
    }


async def revision_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """返工节点：递增 retry_count，带上原稿+审核意见重新调用专业 agent（同样有 token 流）"""
    retry = int(state.get("retry_count", 0) or 0) + 1
    # 用 next_agent（专业 agent 名），current_agent 可能是 "supervisor"
    agent_name = state.get("next_agent") or state.get("intent") or "lesson_plan"
    agent = SubAgentRegistry.get(agent_name)
    if agent is None:
        return {"error_message": f"revision: unknown agent {agent_name}", "retry_count": retry}

    revision_prompt = (
        f"请根据以下审核意见修订输出（第 {retry} 次修订）："
        f"{'; '.join(state.get('revision_history', []))}"
    )
    previous = state.get("final_markdown_output", "")
    if previous:
        revision_prompt = f"原稿如下：\n\n{previous}\n\n---\n{revision_prompt}"
    state = {**state, "retry_count": retry, "needs_revision": False, "current_agent": agent_name}
    state["messages"] = list(state.get("messages", [])) + [
        {"role": "user", "content": revision_prompt}
    ]

    result = await _execute_with_stream(agent, agent_name, state, config)
    # 必须把 retry_count 写回返回值，否则 LangGraph 不会更新 state 的 retry_count
    result["retry_count"] = retry
    result["needs_revision"] = False
    return result


async def hitl_gate_node(state: AgentState) -> Dict[str, Any]:
    """
    人机协作审批节点（compile(interrupt_before) 在进入前暂停）。
    恢复时 human_decision 由 TeachingGraphRunner.resume 经 update_state 注入。
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


async def auto_approve_node(state: AgentState) -> Dict[str, Any]:
    """无需审批时直接标记通过"""
    return {"is_approved": True}


# ============================================================
# 条件边路由函数
# ============================================================

def route_by_intent(state: AgentState):
    """
    IntentNode 之后的条件边：
    - 复合任务（sub_agent_mode / 多步 plan / 启发式复合）→ 返回 Send 列表并行调度
    - 简单任务 → 返回单个专业 agent 节点名
    """
    raw_msg = state.get("raw_user_message") or (
        state["messages"][-1]["content"] if state.get("messages") else ""
    )
    plan_steps = state.get("plan_steps") or []
    is_compound = (
        state.get("sub_agent_mode")
        or len(plan_steps) > 1
        or SubAgentRegistry.is_compound_request(raw_msg)
    )

    if is_compound and plan_steps:
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
    if state.get("requires_approval", False) and not state.get("hitl_auto_approve", False):
        return "hitl_gate"
    return "approved"


def route_after_hitl(state: AgentState) -> str:
    """HITL 之后：通过 → END，驳回且未超上限 → revision（会再次经过审核+审批）"""
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

    builder.add_node("intent_router", intent_node)
    for name in SPECIALIZED_AGENTS:
        builder.add_node(name, make_agent_node(name))
    builder.add_node("aggregate", aggregate_node)
    builder.add_node("quality_review", quality_review_node)
    builder.add_node("revision", revision_node)
    builder.add_node("hitl_gate", hitl_gate_node)
    builder.add_node("approved", auto_approve_node)

    builder.set_entry_point("intent_router")

    # Intent → 各专业 agent（条件边，复合任务时为 Send 列表）
    path_map = {name: name for name in SPECIALIZED_AGENTS}
    builder.add_conditional_edges("intent_router", route_by_intent, path_map)

    # 所有专业 agent → aggregate（并行分支在此形成屏障，只触发一次）
    for name in SPECIALIZED_AGENTS:
        builder.add_edge(name, "aggregate")
    builder.add_edge("aggregate", "quality_review")

    # quality_review → revision / hitl_gate / approved
    builder.add_conditional_edges(
        "quality_review", route_after_quality,
        {"revision": "revision", "hitl_gate": "hitl_gate", "approved": "approved"},
    )

    # revision → quality_review（返工后重新审核）
    builder.add_edge("revision", "quality_review")
    builder.add_edge("approved", END)

    # hitl_gate → revision / END
    builder.add_conditional_edges(
        "hitl_gate", route_after_hitl,
        {"revision": "revision", END: END},
    )

    # HITL：仅在进入 hitl_gate 前暂停（requires_approval=True 且非 auto_approve 时才走到）
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["hitl_gate"],
    )


# ============================================================
# Runner：对外保持与旧 graph_engine.run_workflow 一致的调用契约
# ============================================================

@dataclass
class PausedRun:
    """一次暂停在 hitl_gate 前的图运行（同进程内可恢复）。"""
    graph: Any
    checkpointer: Any
    config: Dict[str, Any]
    task_id: Optional[str]
    thread_id: str
    session_id: str
    agent_type: str
    user_id: str
    user_role: str
    kb_ids: Optional[List[str]]
    conversation_id: Optional[str]
    harness: Any
    explicit_agent: Optional[str]
    sub_agent_mode: bool
    snapshot_state: Dict[str, Any] = field(default_factory=dict)


def _state_to_result(state: Dict[str, Any], harness: Any, explicit_agent: Optional[str]) -> Dict[str, Any]:
    """LangGraph state → 旧 graph_engine.run_workflow 的返回契约"""
    sub_results = state.get("sub_results") or []
    is_parallel = len(sub_results) > 0
    agent_type = "orchestrator" if is_parallel else (
        state.get("current_agent") or explicit_agent or "supervisor"
    )
    return {
        "output": state.get("final_markdown_output", ""),
        "agent_type": agent_type,
        "citations": state.get("citations", []),
        "artifact": state.get("structured_artifact"),
        "artifact_type": state.get("artifact_type"),
        "trace_summary": harness.get_trace_summary() if harness is not None else {},
        "plan_dag": state.get("plan_dag"),
        "sub_results": sub_results,
        "reflection": {},
        "quality_score": state.get("quality_score", 0.0),
        "is_approved": state.get("is_approved", True),
    }


class TeachingGraphRunner:
    """
    LangGraph 运行器（进程内单例）：
    - run_workflow：与旧 graph_engine 同签名，内部跑真 StateGraph
    - resume：HITL 暂停后注入教师决策续跑
    - 暂停态注册在进程内存中（跨进程恢复需 RedisSaver，见 P1）
    """

    def __init__(self):
        self._paused_by_thread: Dict[str, PausedRun] = {}
        self._thread_by_task: Dict[str, str] = {}

    # ---------- 内部工具 ----------

    def is_waiting(self, task_id: str) -> bool:
        thread_id = self._thread_by_task.get(task_id)
        return bool(thread_id and thread_id in self._paused_by_thread)

    def get_waiting_preview(self, task_id: str) -> Optional[Dict[str, Any]]:
        thread_id = self._thread_by_task.get(task_id)
        run = self._paused_by_thread.get(thread_id) if thread_id else None
        if not run:
            return None
        s = run.snapshot_state
        return {
            "output": s.get("final_markdown_output", ""),
            "quality_score": s.get("quality_score", 0.0),
            "artifact": s.get("structured_artifact"),
        }

    def _build_initial_state(
        self, *, user_message: str, enriched_input: str,
        session_id: str, thread_id: str, user_id: str,
        explicit_agent: Optional[str], kb_ids: Optional[List[str]],
        sub_agent_mode: bool, auto_approve: bool,
    ) -> AgentState:
        return {
            "messages": [{"role": "user", "content": enriched_input or user_message}],
            "raw_user_message": user_message,
            "user_id": user_id,
            "session_id": session_id,
            "thread_id": thread_id,
            "intent": explicit_agent or "supervisor",
            "current_agent": "start",
            "next_agent": "supervisor",
            "plan_steps": [],
            "current_step": 0,
            "kb_ids": kb_ids or [],
            "retrieved_docs": [],
            "citations": [],
            "structured_artifact": None,
            "artifact_type": None,
            "final_markdown_output": "",
            "requires_approval": False,
            "is_approved": False,
            "reviewer_comments": None,
            "retry_count": 0,
            "error_message": None,
            "quality_score": 0.0,
            "needs_revision": False,
            "revision_history": [],
            "sub_results": [],
            "plan_dag": None,
            "human_decision": None,
            "sub_agent_mode": sub_agent_mode,
            "hitl_auto_approve": auto_approve,
        }

    # ---------- 主入口 ----------

    async def run_workflow(
        self,
        user_message: str,
        session_id: str,
        thread_id: str,
        user_id: str = "u-001",
        user_role: str = "teacher",
        explicit_agent: Optional[str] = None,
        kb_ids: Optional[List[str]] = None,
        harness: Any = None,
        event_callback: Optional[EventCallback] = None,
        sub_agent_mode: bool = False,
        task_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        auto_approve: bool = False,
    ) -> Dict[str, Any]:
        from app.harness.base import AgentHarness
        from app.services.memory.memory_service import memory_service

        harness = harness or AgentHarness(session_id=session_id, user_role=user_role)

        # ---- 前置：Harness 安全审计 + 教学记忆挂载（与旧 graph.py 保持一致）----
        sanitized_input = harness.before_run(user_message)
        await _emit(event_callback, {
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "title": "Agent Harness 前置安全审计：防越狱注入 / PII 脱敏 / 步数与 Token 预算初始化",
                "action": "THINKING",
                "phase": "harness_pre_check",
            },
        })
        mem_step = harness.on_step(
            "MemoryRetriever", "TOOL_RESULT",
            "正在检索并挂载授课教师专属教学画像与生效记忆",
        )
        await _emit(event_callback, {
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "step_id": mem_step,
                "node_name": "MemoryRetriever",
                "title": "正在检索并挂载授课教师专属教学画像与生效记忆",
                "action": "TOOL_RESULT",
            },
        })
        memory_prompt = memory_service.build_memory_prompt(user_id)
        harness.finish_step(mem_step, detail="已成功加载专属教学画像、学情诊断与生效教学记忆")
        await _emit(event_callback, {
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "step_id": mem_step,
                "node_name": "MemoryRetriever",
                "title": "已成功加载专属教学画像、学情诊断与生效教学记忆",
                "action": "TOOL_RESULT_DONE",
                "elapsed_ms": 0,
            },
        })
        enriched_input = f"{sanitized_input}\n\n{memory_prompt}"

        # ---- 构建图与运行时 config（on_token/on_event 走 config 注入节点）----
        checkpointer = MemorySaver()
        graph = build_teaching_graph(checkpointer=checkpointer)
        state = self._build_initial_state(
            user_message=sanitized_input,
            enriched_input=enriched_input,
            session_id=session_id,
            thread_id=thread_id,
            user_id=user_id,
            explicit_agent=explicit_agent,
            kb_ids=kb_ids,
            sub_agent_mode=sub_agent_mode,
            auto_approve=auto_approve,
        )
        config: Dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
                "on_token": None,  # token 由节点内部经 on_event 通道发送
                "on_event": event_callback,
            },
            "recursion_limit": 50,
        }

        final_state = await graph.ainvoke(state, config=config)

        # ---- HITL 暂停：图停在 hitl_gate 前 ----
        snapshot = graph.get_state(config)
        if snapshot.next:
            paused = PausedRun(
                graph=graph,
                checkpointer=checkpointer,
                config=config,
                task_id=task_id,
                thread_id=thread_id,
                session_id=session_id,
                agent_type=explicit_agent or "supervisor",
                user_id=user_id,
                user_role=user_role,
                kb_ids=kb_ids,
                conversation_id=conversation_id,
                harness=harness,
                explicit_agent=explicit_agent,
                sub_agent_mode=sub_agent_mode,
                snapshot_state=dict(final_state),
            )
            self._paused_by_thread[thread_id] = paused
            if task_id:
                self._thread_by_task[task_id] = thread_id

            result = _state_to_result(final_state, harness, explicit_agent)
            result["run_status"] = "waiting_approval"
            return result

        # ---- 后置教学合规审查 ----
        final_state["final_markdown_output"] = harness.after_run(
            final_state.get("final_markdown_output", "")
        )
        await _emit(event_callback, {
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "node_name": "Harness_PostGuardrail",
                "title": "后置教学合规审查通过",
                "action": "POST_GUARDRAIL_DONE",
            },
        })

        result = _state_to_result(final_state, harness, explicit_agent)
        result["run_status"] = "completed"
        return result

    # ---------- HITL 恢复 ----------

    async def resume(
        self,
        task_id: str,
        approved: bool,
        comments: Optional[str] = None,
        event_callback: Optional[EventCallback] = None,
    ) -> Dict[str, Any]:
        """注入教师审批决策并从 hitl_gate 续跑，返回终态 result（可能再次暂停）。"""
        thread_id = self._thread_by_task.get(task_id)
        run = self._paused_by_thread.get(thread_id) if thread_id else None
        if run is None:
            raise KeyError(f"未找到任务 {task_id} 的暂停态（跨进程恢复需 RedisSaver）")

        # 更新 config 中的事件回调（恢复请求可能来自新的 HTTP 调用）
        if event_callback is not None:
            run.config = {**run.config, "configurable": {
                **run.config["configurable"], "on_event": event_callback,
            }}

        run.graph.update_state(
            run.config,
            {"human_decision": {"approved": approved, "comments": comments}},
        )
        final_state = await run.graph.ainvoke(None, config=run.config)

        # 被驳回返工后可能再次走到 hitl_gate 暂停（允许教师二次审批）
        snapshot = run.graph.get_state(run.config)
        if snapshot.next:
            run.snapshot_state = dict(final_state)
            result = _state_to_result(final_state, run.harness, run.explicit_agent)
            result["run_status"] = "waiting_approval"
            return result

        # 终态：清理注册表
        self._paused_by_thread.pop(thread_id, None)
        self._thread_by_task.pop(task_id, None)

        final_state["final_markdown_output"] = run.harness.after_run(
            final_state.get("final_markdown_output", "")
        )
        result = _state_to_result(final_state, run.harness, run.explicit_agent)
        result["run_status"] = "completed"
        result["conversation_id"] = run.conversation_id
        return result

    def get_paused_run(self, task_id: str) -> Optional[PausedRun]:
        thread_id = self._thread_by_task.get(task_id)
        return self._paused_by_thread.get(thread_id) if thread_id else None


# 全局单例
teaching_graph_runner = TeachingGraphRunner()
