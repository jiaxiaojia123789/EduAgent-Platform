"""
LangGraph 教学智能体执行图（真 LangGraph StateGraph 实现，LangGraph 1.x）

拓扑：
  intent_router ──conditional──► [9 个专业 agent] ──► aggregate ──► quality_review
                                                                     │
                                              needs_revision?        │
                                          ┌──────────────────────────┼──────────────┐
                                         yes            no(需审批)                 no(免审批)
                                          ▼               ▼                           ▼
                                      revision      hitl_gate(interrupt)         approved → END
                                          │               │ approved?
                                          └─►quality_review├─yes──► END
                                            (≤2 次)        └─no───► revision

能力：
- 真 token 流：节点经 config 注入 on_token，优先 execute_stream，非流式 agent 用 TokenCounter 补偿
- 复合任务：route_by_intent 返回 Send 列表并行执行，aggregate 节点合并写回 final_markdown_output
- HITL：原生 interrupt() 暂停，TeachingGraphRunner.resume 以 Command(resume=) 注入决策续跑
- 持久化：AsyncRedisSaver（Redis 8+ / Redis Stack），不可用时降级 MemorySaver；支持跨进程恢复
- 可观测性：全节点 node_start/node_end 事件（耗时/retry_count/quality_score），汇总入 trace_summary
"""
import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable, Awaitable

from langgraph.graph import StateGraph, END
from langgraph.types import Send, Command, interrupt
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.runnables import RunnableConfig

from app.core.config import settings
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
# Checkpointer 工厂：AsyncRedisSaver（持久化）→ MemorySaver（降级）
# ============================================================

_checkpointer: Optional[Any] = None
_checkpointer_backend: str = "memory"
_checkpointer_init_lock = asyncio.Lock()

_graph: Optional[Any] = None
_graph_init_lock = asyncio.Lock()


def _redis_url() -> str:
    pwd = f":{settings.REDIS_PASSWORD}@" if settings.REDIS_PASSWORD else ""
    return (
        f"redis://{pwd}{settings.REDIS_HOST}:{settings.REDIS_PORT}"
        f"/{settings.REDIS_DB}"
    )


async def _build_redis_saver():
    """
    构建 AsyncRedisSaver：
    1. 探测 Redis 模块（RedisJSON / search）——Redis 8+ 内置，低版本需 Redis Stack
    2. 独立字节协议连接（decode_responses=False），避免与 redis_manager 的文本连接冲突
    失败返回 None，由调用方降级 MemorySaver。
    """
    from app.core.redis_client import redis_manager

    if not (redis_manager.is_connected and redis_manager.client):
        return None
    try:
        modules = await redis_manager.client.module_list()
        names = set()
        for m in modules or []:
            # decode_responses=True → 字符串键
            names.add(m.get("name") if isinstance(m, dict) else None)
        missing = {"ReJSON", "search"} - names
        if missing:
            logger.warning(
                f"[TeachingGraph] Redis 缺少模块 {missing}（需 Redis 8+ 或 Redis Stack），"
                "checkpointer 降级 MemorySaver"
            )
            return None

        import redis.asyncio as aioredis
        client = aioredis.Redis.from_url(_redis_url(), decode_responses=False)
        await client.ping()

        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
        saver = AsyncRedisSaver(redis_client=client)
        await saver.asetup()  # 幂等创建索引，已存在则跳过
        return saver
    except Exception as e:
        logger.warning(f"[TeachingGraph] AsyncRedisSaver 初始化失败，降级 MemorySaver: {e}")
        return None


async def get_checkpointer() -> Any:
    """幂等单例：返回 LangGraph checkpointer（RedisSaver / MemorySaver）。"""
    global _checkpointer, _checkpointer_backend
    if _checkpointer is not None:
        return _checkpointer
    async with _checkpointer_init_lock:
        if _checkpointer is not None:
            return _checkpointer
        saver = await _build_redis_saver()
        if saver is not None:
            _checkpointer = saver
            _checkpointer_backend = "redis"
            logger.info("[TeachingGraph] checkpointer 后端: AsyncRedisSaver（持久化）")
        else:
            _checkpointer = MemorySaver()
            _checkpointer_backend = "memory"
            logger.info("[TeachingGraph] checkpointer 后端: MemorySaver（进程内，重启即丢）")
    return _checkpointer


def checkpointer_backend() -> str:
    """当前 checkpointer 后端标识：'redis'（可跨进程恢复）/ 'memory'。"""
    return _checkpointer_backend


async def get_teaching_graph() -> Any:
    """幂等单例：共享编译后的教学图（多 thread_id 复用同一实例）。"""
    global _graph
    if _graph is not None:
        return _graph
    async with _graph_init_lock:
        if _graph is None:
            cp = await get_checkpointer()
            _graph = build_teaching_graph(checkpointer=cp)
    return _graph


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
    人机协作审批节点（原生 interrupt）。
    首次执行时 interrupt() 抛出暂停信息；教师经 Command(resume=decision) 恢复后，
    interrupt() 的返回值即审批决策 {approved, comments}。
    """
    decision = interrupt({
        "type": "teaching_approval",
        "output": state.get("final_markdown_output", ""),
        "quality_score": state.get("quality_score", 0.0),
        "artifact": state.get("structured_artifact"),
    })
    approved = bool((decision or {}).get("approved", False))
    comments = (decision or {}).get("comments")
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

def _with_node_metrics(node_name: str, func: Callable):
    """
    节点可观测性包装：统一推送 node_start / node_end 事件。
    node_end 携带 elapsed_ms、retry_count、quality_score，供 SSE 与 trace_summary 使用。
    兼容 (state, config) 与 (state) 两种节点签名。
    """
    takes_config = len(inspect.signature(func).parameters) >= 2

    async def _wrapped(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
        on_event = _runtime_callbacks(config)["on_event"]
        session_id = state.get("session_id", "")
        started = time.monotonic()
        await _emit(on_event, {
            "event_type": "node_start",
            "task_id": session_id,
            "payload": {"node": node_name},
        })
        try:
            result = await func(state, config) if takes_config else await func(state)
        except Exception:
            elapsed = int((time.monotonic() - started) * 1000)
            await _emit(on_event, {
                "event_type": "node_end",
                "task_id": session_id,
                "payload": {"node": node_name, "elapsed_ms": elapsed, "status": "error"},
            })
            raise
        elapsed = int((time.monotonic() - started) * 1000)
        await _emit(on_event, {
            "event_type": "node_end",
            "task_id": session_id,
            "payload": {
                "node": node_name,
                "elapsed_ms": elapsed,
                "status": "ok",
                "retry_count": (result or {}).get("retry_count", 0),
                "quality_score": (result or {}).get("quality_score"),
            },
        })
        return result

    _wrapped.__name__ = f"{node_name}_instrumented"
    return _wrapped


def build_teaching_graph(checkpointer=None) -> Any:
    """构建 LangGraph 教学智能体图。checkpointer 传 None 则不持久化。"""
    builder = StateGraph(AgentState)

    builder.add_node("intent_router", _with_node_metrics("intent_router", intent_node))
    for name in SPECIALIZED_AGENTS:
        builder.add_node(name, _with_node_metrics(name, make_agent_node(name)))
    builder.add_node("aggregate", _with_node_metrics("aggregate", aggregate_node))
    builder.add_node("quality_review", _with_node_metrics("quality_review", quality_review_node))
    builder.add_node("revision", _with_node_metrics("revision", revision_node))
    builder.add_node("hitl_gate", _with_node_metrics("hitl_gate", hitl_gate_node))
    builder.add_node("approved", _with_node_metrics("approved", auto_approve_node))

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

    # HITL 暂停由 hitl_gate 节点内部的 interrupt() 触发（无需 interrupt_before）
    return builder.compile(checkpointer=checkpointer)


# ============================================================
# Runner：对外保持与旧 graph_engine.run_workflow 一致的调用契约
# ============================================================

@dataclass
class PausedRun:
    """一次暂停在 hitl_gate（interrupt）处的图运行；checkpoint 已持久化，本地注册便于快速恢复。"""
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


def _state_to_result(
    state: Dict[str, Any],
    harness: Any,
    explicit_agent: Optional[str],
    node_timings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """LangGraph state → 旧 graph_engine.run_workflow 的返回契约"""
    sub_results = state.get("sub_results") or []
    is_parallel = len(sub_results) > 0
    agent_type = "orchestrator" if is_parallel else (
        state.get("current_agent") or explicit_agent or "supervisor"
    )
    trace_summary = harness.get_trace_summary() if harness is not None else {}
    if node_timings:
        # 图执行可观测性：节点耗时 / 重试次数 / quality_score 汇总
        trace_summary = {**trace_summary, "node_timings": node_timings}
    return {
        "output": state.get("final_markdown_output", ""),
        "agent_type": agent_type,
        "citations": state.get("citations", []),
        "artifact": state.get("structured_artifact"),
        "artifact_type": state.get("artifact_type"),
        "trace_summary": trace_summary,
        "plan_dag": state.get("plan_dag"),
        "sub_results": sub_results,
        "reflection": {},
        "quality_score": state.get("quality_score", 0.0),
        "is_approved": state.get("is_approved", True),
    }


class TeachingGraphRunner:
    """
    LangGraph 运行器（进程内单例）：
    - run_workflow：与旧 graph_engine 同签名，内部跑真 StateGraph（共享图实例）
    - resume：以 Command(resume=) 注入教师决策续跑
    - 本地注册暂停元数据；checkpointer 为 RedisSaver 时支持跨进程恢复
    """

    def __init__(self):
        self._paused_by_thread: Dict[str, PausedRun] = {}
        self._thread_by_task: Dict[str, str] = {}

    # ---------- 内部工具 ----------

    @staticmethod
    def _make_collector(
        event_callback: Optional[EventCallback],
        node_timings: List[Dict[str, Any]],
    ) -> EventCallback:
        """
        包装调用方事件回调：旁路收集 node_end 事件（节点耗时/重试/质量分），
        同时把事件原样转发给调用方（SSE 总线）。
        """
        async def _collector(event: Dict[str, Any]):
            if event.get("event_type") == "node_end":
                p = event.get("payload") or {}
                node_timings.append({
                    k: p.get(k)
                    for k in ("node", "elapsed_ms", "status", "retry_count", "quality_score")
                })
            await _emit(event_callback, event)

        return _collector

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

        # ---- 共享图实例与运行时 config（on_token/on_event 走 config 注入节点）----
        graph = await get_teaching_graph()
        checkpointer = await get_checkpointer()
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

        node_timings: List[Dict[str, Any]] = []
        collecting_cb = self._make_collector(event_callback, node_timings)
        config: Dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
                "on_token": None,  # token 由节点内部经 on_event 通道发送
                "on_event": collecting_cb,
            },
            "recursion_limit": 50,
        }

        final_state = await graph.ainvoke(state, config=config)

        # ---- HITL 暂停：图停在 hitl_gate 的 interrupt 处 ----
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

            result = _state_to_result(final_state, harness, explicit_agent, node_timings)
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

        result = _state_to_result(final_state, harness, explicit_agent, node_timings)
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
        """
        以 Command(resume=decision) 从 hitl_gate 的 interrupt 处续跑，
        返回终态 result（被驳回返工时可能再次暂停）。
        - 本地有暂停注册：直接复用其 harness / metadata
        - 无本地注册（跨进程）：checkpointer 须为 RedisSaver，从任务存储重建上下文
        """
        from app.harness.base import AgentHarness

        thread_id = self._thread_by_task.get(task_id)
        run = self._paused_by_thread.get(thread_id) if thread_id else None

        if run is not None:
            graph = run.graph
            harness = run.harness
            explicit_agent = run.explicit_agent
            conversation_id = run.conversation_id
            session_id = run.session_id
        else:
            # ---- 跨进程恢复：从 Redis 任务存储找回元数据 ----
            if checkpointer_backend() != "redis":
                raise KeyError(
                    f"未找到任务 {task_id} 的本地暂停态，且 checkpointer 非 RedisSaver，无法跨进程恢复"
                )
            from app.services.task_queue.redis_task_store import redis_task_store

            task = await redis_task_store.get_task(task_id)
            if not task:
                raise KeyError(f"未找到任务 {task_id}")
            graph = await get_teaching_graph()
            thread_id = task["thread_id"]
            session_id = task["session_id"]
            harness = AgentHarness(session_id=session_id, user_role="teacher")
            task_agent = task.get("agent_type") or "supervisor"
            explicit_agent = None if task_agent == "supervisor" else task_agent
            conversation_id = await redis_task_store.get_conversation_link(task_id)
            logger.info(f"[TeachingGraph] 跨进程恢复 task={task_id} thread={thread_id}")

        node_timings: List[Dict[str, Any]] = []
        collecting_cb = self._make_collector(event_callback, node_timings)
        config: Dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
                "on_token": None,
                "on_event": collecting_cb,
            },
            "recursion_limit": 50,
        }

        final_state = await graph.ainvoke(
            Command(resume={"approved": approved, "comments": comments}),
            config=config,
        )

        # 被驳回返工后可能再次走到 hitl_gate 暂停（允许教师二次审批）
        snapshot = graph.get_state(config)
        if snapshot.next:
            if run is not None:
                run.snapshot_state = dict(final_state)
            else:
                # 跨进程恢复后再次暂停：在本进程注册，后续审批可快速恢复
                paused = PausedRun(
                    graph=graph,
                    checkpointer=await get_checkpointer(),
                    config=config,
                    task_id=task_id,
                    thread_id=thread_id,
                    session_id=session_id,
                    agent_type=explicit_agent or "supervisor",
                    user_id="u-001",
                    user_role="teacher",
                    kb_ids=None,
                    conversation_id=conversation_id,
                    harness=harness,
                    explicit_agent=explicit_agent,
                    sub_agent_mode=False,
                    snapshot_state=dict(final_state),
                )
                self._paused_by_thread[thread_id] = paused
                self._thread_by_task[task_id] = thread_id

            result = _state_to_result(final_state, harness, explicit_agent, node_timings)
            result["run_status"] = "waiting_approval"
            return result

        # 终态：清理本地注册表
        self._paused_by_thread.pop(thread_id, None)
        self._thread_by_task.pop(task_id, None)

        final_state["final_markdown_output"] = harness.after_run(
            final_state.get("final_markdown_output", "")
        )
        result = _state_to_result(final_state, harness, explicit_agent, node_timings)
        result["run_status"] = "completed"
        result["conversation_id"] = conversation_id
        return result

    def get_paused_run(self, task_id: str) -> Optional[PausedRun]:
        thread_id = self._thread_by_task.get(task_id)
        return self._paused_by_thread.get(thread_id) if thread_id else None


# 全局单例
teaching_graph_runner = TeachingGraphRunner()
