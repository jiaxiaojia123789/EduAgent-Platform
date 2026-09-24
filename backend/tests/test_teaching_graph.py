"""
TeachingGraph（LangGraph 教学图）单元测试
=========================================
- 全部专业 agent / grounding 校验使用 mock：无 GPU、无 API Key 依赖，可直接进 CI
- 覆盖：
    1. 单 agent 真 token 流 + 非流式 agent 打字机补偿
    2. HITL：interrupt 暂停 → Command(resume=) 审批通过/驳回返工 → 二次审批
    3. 并行：Send fan-out → aggregate 屏障合并 → final_markdown_output 非空
    4. 复合自然消息的关键词拆解
    5. 条件边路由逻辑 + quality_review 打分节点

与 verify_teaching_graph.py 的区别：本文件由 pytest 收集，使用 fixture 自动隔离，
不修改全局真实注册表；verify 脚本保留为人工冒烟入口。
"""
import asyncio
import uuid

import pytest
from langgraph.graph import END

from app.services.agent import sub_agent
from app.services.agent.teaching_graph import (
    build_teaching_graph,
    teaching_graph_runner,
    SPECIALIZED_AGENTS,
    MAX_REVISION,
    route_after_quality,
    route_after_hitl,
    quality_review_node,
)
from app.services.rag import hallucination as hallucination_module


# ============================================================
# Mock agent 工厂（与 verify_teaching_graph.py 对齐）
# ============================================================

def make_streaming_fake(name):
    """execute_stream 产出真实 token 的 mock agent"""

    class FakeAgent(sub_agent.SubAgent):
        agent_name = name

        async def execute_stream(self, state, on_token=None):
            if on_token is not None:
                for piece in (f"{name}片段一、", f"{name}片段二、", f"{name}片段三。$a^2+b^2=c^2$"):
                    await on_token(piece)
            return {
                "current_agent": name,
                "final_markdown_output": f"{name}片段一、{name}片段二、{name}片段三。$a^2+b^2=c^2$",
                "structured_artifact": {"title": name, "markdown": "mock"},
                "artifact_type": "MOCK",
                "citations": [],
                "requires_approval": state.get("requires_approval", False),
            }

        async def execute(self, state):
            return await self.execute_stream(state, on_token=None)

    FakeAgent.__name__ = f"FakeStream_{name}"
    return FakeAgent


def make_nonstream_fake(name):
    """只实现 execute 的 mock agent，用于验证 TokenCounter 补偿"""

    class FakeAgent(sub_agent.SubAgent):
        agent_name = name

        async def execute(self, state):
            return {
                "current_agent": name,
                "final_markdown_output": f"{name}一次性返回的完整内容，应当被分片补偿。",
                "structured_artifact": None,
                "artifact_type": None,
                "citations": [],
                "requires_approval": state.get("requires_approval", False),
            }

    FakeAgent.__name__ = f"FakeNonStream_{name}"
    return FakeAgent


def make_approval_fake(name):
    """requires_approval 恒 True 的 mock agent"""

    class FakeAgent(sub_agent.SubAgent):
        agent_name = name

        async def execute_stream(self, state, on_token=None):
            if on_token is not None:
                await on_token("待审批内容")
            return {
                "current_agent": name,
                "final_markdown_output": "待审批内容：导数几何意义是切线斜率。",
                "structured_artifact": {"title": "待审批", "markdown": "x"},
                "artifact_type": "MOCK",
                "citations": [],
                "requires_approval": True,
            }

        async def execute(self, state):
            return await self.execute_stream(state, on_token=None)

    FakeAgent.__name__ = f"FakeApproval_{name}"
    return FakeAgent


class EventCollector:
    """异步事件收集器（与节点回调签名一致）"""

    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)


def _run(coro):
    """同步测试内执行协程（与 test_code_grader.py 惯例一致，避免 pytest-asyncio 模式依赖）"""
    return asyncio.run(coro)


def _events_of(collector, event_type):
    return [e for e in collector.events if e["event_type"] == event_type]


# ============================================================
# Fixtures：全局隔离
# ============================================================

@pytest.fixture
def fake_grounding():
    """mock 接地校验恒通过，避免外部 LLM 依赖"""
    original = hallucination_module.hallucination_checker.verify_grounding

    async def _ok(output, retrieved_docs):
        return True, 1.0, "mock 接地通过"

    hallucination_module.hallucination_checker.verify_grounding = _ok
    yield
    hallucination_module.hallucination_checker.verify_grounding = original


@pytest.fixture
def registry_factory():
    """
    提供 install(mapping) 入口，测试结束恢复真实注册表（9 个真实 agent 在 import 时自注册）。
    同时重置 teaching_graph 的图/checkpointer 单例，防止跨用例污染。
    """
    from app.services.agent import teaching_graph as tg

    saved_registry = dict(sub_agent.SubAgentRegistry._registry)
    saved_instances = dict(sub_agent.SubAgentRegistry._instances)
    saved_graph, saved_cp = tg._graph, tg._checkpointer
    saved_backend = tg._checkpointer_backend

    def install(mapping):
        sub_agent.SubAgentRegistry._registry = mapping
        sub_agent.SubAgentRegistry._instances = {}

    yield install

    sub_agent.SubAgentRegistry._registry = saved_registry
    sub_agent.SubAgentRegistry._instances = saved_instances
    tg._graph, tg._checkpointer = saved_graph, saved_cp
    tg._checkpointer_backend = saved_backend
    # Runner 暂停注册表不允许跨用例泄漏
    teaching_graph_runner._paused_by_thread.clear()
    teaching_graph_runner._thread_by_task.clear()


def _base_state(**overrides):
    state = {
        "messages": [{"role": "user", "content": "设计一份导数教案"}],
        "user_id": "u-test", "session_id": "s-test", "thread_id": "t-test",
        "intent": "lesson_plan", "current_agent": "start", "next_agent": "lesson_plan",
        "plan_steps": [], "current_step": 0, "kb_ids": [], "retrieved_docs": [],
        "citations": [], "structured_artifact": None, "artifact_type": None,
        "final_markdown_output": "", "requires_approval": False, "is_approved": False,
        "reviewer_comments": None, "retry_count": 0, "error_message": None,
        "quality_score": 0.0, "needs_revision": False, "revision_history": [],
        "sub_results": [], "plan_dag": None, "human_decision": None,
        "sub_agent_mode": False, "hitl_auto_approve": False,
        "raw_user_message": "",
    }
    state.update(overrides)
    if not state["raw_user_message"]:
        state["raw_user_message"] = state["messages"][-1]["content"] if state["messages"] else ""
    return state


def _unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ============================================================
# 1. 单 agent：token 流 + 非流式补偿 + 节点可观测性
# ============================================================

def test_single_agent_streaming_tokens(fake_grounding, registry_factory):
    registry_factory({n: make_streaming_fake(n) for n in SPECIALIZED_AGENTS})
    collector = EventCollector()

    result = _run(teaching_graph_runner.run_workflow(
        user_message="设计一份导数教案",
        session_id=_unique("s"), thread_id=_unique("t"),
        explicit_agent="lesson_plan",
        event_callback=collector,
        task_id=_unique("task"),
    ))

    assert result["run_status"] == "completed"
    assert result["output"]
    assert len(_events_of(collector, "token")) >= 3

    # 节点可观测性：node_start/node_end 成对出现
    starts = _events_of(collector, "node_start")
    ends = _events_of(collector, "node_end")
    assert len(starts) == len(ends) and len(starts) >= 5

    end_nodes = {e["payload"]["node"] for e in ends}
    assert {"intent_router", "lesson_plan", "aggregate", "quality_review", "approved"} <= end_nodes
    for e in ends:
        assert isinstance(e["payload"]["elapsed_ms"], int)
        assert e["payload"]["elapsed_ms"] >= 0

    # trace_summary 含节点时序，quality_review 已写入质量分
    quality_ends = [e for e in ends if e["payload"]["node"] == "quality_review"]
    assert quality_ends[0]["payload"]["quality_score"] == 1.0
    node_timings = result["trace_summary"]["node_timings"]
    assert {t["node"] for t in node_timings} == end_nodes


def test_nonstream_agent_token_compensation(fake_grounding, registry_factory):
    registry_factory({n: make_nonstream_fake(n) for n in SPECIALIZED_AGENTS})
    collector = EventCollector()

    result = _run(teaching_graph_runner.run_workflow(
        user_message="为什么割线极限是切线",
        session_id=_unique("s"), thread_id=_unique("t"),
        explicit_agent="socratic",
        event_callback=collector,
        task_id=_unique("task"),
    ))

    assert result["run_status"] == "completed"
    # 非流式 agent：最终文本被 TokenCounter 分片补发
    assert len(_events_of(collector, "token")) > 3


# ============================================================
# 2. HITL：interrupt 暂停 → Command(resume=) 审批
# ============================================================

def test_hitl_pause_and_resume_approve(fake_grounding, registry_factory):
    registry_factory({n: make_approval_fake(n) for n in SPECIALIZED_AGENTS})
    collector = EventCollector()
    task_id = _unique("task")

    result = _run(teaching_graph_runner.run_workflow(
        user_message="设计一份需要审批的教案",
        session_id=_unique("s"), thread_id=_unique("t"),
        explicit_agent="lesson_plan",
        event_callback=collector,
        task_id=task_id,
    ))

    assert result["run_status"] == "waiting_approval"
    assert teaching_graph_runner.is_waiting(task_id)
    preview = teaching_graph_runner.get_waiting_preview(task_id)
    assert preview and preview["output"]

    resumed = _run(teaching_graph_runner.resume(
        task_id, approved=True, comments="同意发布",
        event_callback=collector,
    ))

    assert resumed["run_status"] == "completed"
    assert resumed["is_approved"] is True
    assert not teaching_graph_runner.is_waiting(task_id)


def test_hitl_reject_triggers_revision_then_approve(fake_grounding, registry_factory):
    registry_factory({n: make_approval_fake(n) for n in SPECIALIZED_AGENTS})
    collector = EventCollector()
    task_id = _unique("task")

    first = _run(teaching_graph_runner.run_workflow(
        user_message="设计一份需要审批的教案",
        session_id=_unique("s"), thread_id=_unique("t"),
        explicit_agent="lesson_plan",
        event_callback=collector,
        task_id=task_id,
    ))
    assert first["run_status"] == "waiting_approval"

    # 驳回：revision 返工后重新走到 hitl_gate 二次暂停
    rejected = _run(teaching_graph_runner.resume(
        task_id, approved=False, comments="请补充学情分析",
        event_callback=collector,
    ))
    assert rejected["run_status"] == "waiting_approval"
    assert teaching_graph_runner.is_waiting(task_id)

    ends = _events_of(collector, "node_end")
    revision_ends = [e for e in ends if e["payload"]["node"] == "revision"]
    assert revision_ends and revision_ends[0]["payload"]["retry_count"] == 1

    # 二次审批通过 → 终态
    approved = _run(teaching_graph_runner.resume(
        task_id, approved=True, comments="可以了",
        event_callback=collector,
    ))
    assert approved["run_status"] == "completed"
    assert approved["is_approved"] is True


# ============================================================
# 3. 并行：Send fan-out → aggregate 合并
# ============================================================

def test_parallel_aggregation(fake_grounding, registry_factory):
    registry_factory({n: make_streaming_fake(n) for n in SPECIALIZED_AGENTS})
    graph = build_teaching_graph()  # 无 checkpointer，不涉及 HITL
    state = _base_state(
        messages=[{"role": "user", "content": "设计导数教案并出配套试卷"}],
        plan_steps=["lesson_plan", "exam_quiz"],
        sub_agent_mode=True,
        thread_id=_unique("t"), session_id=_unique("s"),
    )

    final_state = asyncio.run(graph.ainvoke(state, config={"recursion_limit": 50}))

    assert len(final_state.get("sub_results", [])) == 2
    output = final_state.get("final_markdown_output", "")
    assert output and "lesson_plan" in output and "exam_quiz" in output
    plan_dag = final_state.get("plan_dag") or {}
    assert plan_dag.get("total_tasks") == 2
    assert plan_dag.get("schedule") == "parallel"


# ============================================================
# 4. 复合自然消息关键词拆解
# ============================================================

def test_compound_decomposition(fake_grounding, registry_factory):
    registry_factory({n: make_streaming_fake(n) for n in SPECIALIZED_AGENTS})
    collector = EventCollector()

    result = _run(teaching_graph_runner.run_workflow(
        user_message="请设计一份导数教案并出配套试题",
        session_id=_unique("s"), thread_id=_unique("t"),
        event_callback=collector,
        task_id=_unique("task"),
    ))

    assert result["run_status"] == "completed"
    names = {s.get("agent_name") for s in result.get("sub_results", [])}
    assert names == {"lesson_plan", "exam_quiz"}
    assert result["output"]


# ============================================================
# 5. 条件边路由 & 质量审核节点
# ============================================================

def test_route_after_quality():
    assert route_after_quality({
        "needs_revision": True, "retry_count": 0,
    }) == "revision"
    # 需返工但已超上限：不再 revision，落到审批判断
    assert route_after_quality({
        "needs_revision": True, "retry_count": MAX_REVISION,
        "requires_approval": True,
    }) == "hitl_gate"
    assert route_after_quality({
        "needs_revision": False, "requires_approval": True, "hitl_auto_approve": False,
    }) == "hitl_gate"
    assert route_after_quality({
        "needs_revision": False, "requires_approval": False,
    }) == "approved"


def test_route_after_hitl():
    assert route_after_hitl({"is_approved": True}) == END
    assert route_after_hitl({
        "is_approved": False, "retry_count": 0,
    }) == "revision"
    assert route_after_hitl({
        "is_approved": False, "retry_count": MAX_REVISION,
    }) == END  # 超上限直接结束，避免死循环


def test_quality_review_node_scoring(fake_grounding):
    config = {"configurable": {"on_event": None}}

    # 合法 LaTeX + 接地通过 → 满分，无需返工
    ok_state = _base_state(
        final_markdown_output="结论：$a^2+b^2=c^2$，公式闭合。",
        retrieved_docs=[{"content": "勾股定理"}],
    )
    ok_result = asyncio.run(quality_review_node(ok_state, config))
    assert ok_result["quality_score"] == 1.0
    assert ok_result["needs_revision"] is False

    # LaTeX 未闭合 → 需要返工
    broken_state = _base_state(
        final_markdown_output="公式 $a^2 没有闭合",
        retrieved_docs=[{"content": "a squared"}],
    )
    broken_result = asyncio.run(quality_review_node(broken_state, config))
    assert broken_result["needs_revision"] is True
    # 接地满分(1.0*0.7) + LaTeX 未通过(0*0.3) = 0.7：返工由 LaTeX 触发而非阈值
    assert broken_result["quality_score"] == 0.7
