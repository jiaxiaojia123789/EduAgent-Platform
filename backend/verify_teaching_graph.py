"""
验证 LangGraph 教学智能体图 + Runner 的完整执行链路。
mock 专业 agent 与 grounding 校验，验证：
1. 单 agent：真 token 流事件 + 一次性返回 agent 的打字机补偿
2. HITL：requires_approval 暂停 → resume 注入决策 → 终态
3. 并行：Send fan-out → aggregate 合并 → final_markdown_output 非空
运行：cd backend && python verify_teaching_graph.py
"""
import asyncio
import sys
sys.path.insert(0, ".")

from app.services.agent import sub_agent
from app.services.agent.teaching_graph import (
    build_teaching_graph, teaching_graph_runner, SPECIALIZED_AGENTS,
)
from app.services.rag import hallucination as hallucination_module

# ---- mock grounding：返回必然通过，避免外部 LLM 干扰图流程验证 ----
async def _fake_verify_grounding(output, retrieved_docs):
    return True, 1.0, "mock 接地通过"

hallucination_module.hallucination_checker.verify_grounding = _fake_verify_grounding


# ---- mock 专业 agent ----
def make_streaming_fake(name):
    """模拟 lesson_plan/exam_quiz：execute_stream 产出真 token"""
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
    """模拟 socratic：只实现 execute，验证 TokenCounter 补偿"""
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
    """模拟要求审批的 agent：requires_approval 恒 True"""
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


def install_registry(mapping):
    sub_agent.SubAgentRegistry._registry = mapping
    sub_agent.SubAgentRegistry._instances = {}


class EventCollector:
    """异步事件收集器（节点回调必须是 async）"""
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)


def base_state(**overrides):
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
    # 未显式指定 raw_user_message 时，默认与最后一条消息一致（测试图直调场景）
    if not state["raw_user_message"]:
        state["raw_user_message"] = state["messages"][-1]["content"] if state["messages"] else ""
    return state


async def test_single_agent_tokens():
    """测试1：单 agent 真 token 流 + 非流式 agent 补偿，都应有 token 事件"""
    print("\n=== 测试1：单 agent token 流 ===")

    # 1a. 流式 agent
    install_registry({n: make_streaming_fake(n) for n in SPECIALIZED_AGENTS})
    collector = EventCollector()
    result = await teaching_graph_runner.run_workflow(
        user_message="设计一份导数教案",
        session_id="s-1a", thread_id="t-1a",
        explicit_agent="lesson_plan",
        event_callback=collector,
        task_id="task-1a",
    )
    real_tokens = [e for e in collector.events if e["event_type"] == "token"]
    print(f"  流式 agent: token 事件 {len(real_tokens)} 个, run_status={result['run_status']}")
    ok_a = len(real_tokens) >= 3 and result["run_status"] == "completed" and result["output"]

    # 1b. 非流式 agent（socratic）→ TokenCounter 补偿
    install_registry({n: make_nonstream_fake(n) for n in SPECIALIZED_AGENTS})
    collector_b = EventCollector()
    result_b = await teaching_graph_runner.run_workflow(
        user_message="为什么割线极限是切线",
        session_id="s-1b", thread_id="t-1b",
        explicit_agent="socratic",
        event_callback=collector_b,
        task_id="task-1b",
    )
    comp_tokens = [e for e in collector_b.events if e["event_type"] == "token"]
    print(f"  非流式 agent: 补偿 token 事件 {len(comp_tokens)} 个, output={result_b['output'][:20]}...")
    ok_b = len(comp_tokens) > 3 and result_b["run_status"] == "completed"

    print(f"  PASS: {ok_a and ok_b}")
    return ok_a and ok_b


async def test_hitl_pause_resume():
    """测试2：requires_approval → 暂停 → 审批通过 → completed"""
    print("\n=== 测试2：HITL 暂停与恢复 ===")
    install_registry({n: make_approval_fake(n) for n in SPECIALIZED_AGENTS})
    collector = EventCollector()
    result = await teaching_graph_runner.run_workflow(
        user_message="设计一份需要审批的教案",
        session_id="s-2", thread_id="t-2",
        explicit_agent="lesson_plan",
        event_callback=collector,
        task_id="task-2",
    )
    paused = teaching_graph_runner.is_waiting("task-2")
    print(f"  首次: run_status={result['run_status']}, 注册表暂停态={paused}")
    ok_a = result["run_status"] == "waiting_approval" and paused

    resumed = await teaching_graph_runner.resume(
        "task-2", approved=True, comments="同意发布",
        event_callback=collector,
    )
    still_paused = teaching_graph_runner.is_waiting("task-2")
    print(f"  恢复: run_status={resumed['run_status']}, is_approved={resumed['is_approved']}, 残留暂停态={still_paused}")
    ok_b = resumed["run_status"] == "completed" and resumed["is_approved"] is True and not still_paused

    print(f"  PASS: {ok_a and ok_b}")
    return ok_a and ok_b


async def test_parallel_aggregation():
    """测试3：并行 sub_results 经 aggregate 合并，final_markdown_output 非空"""
    print("\n=== 测试3：并行聚合 ===")
    install_registry({n: make_streaming_fake(n) for n in SPECIALIZED_AGENTS})
    graph = build_teaching_graph()  # 无需 checkpointer（不涉及 HITL）
    state = base_state(
        messages=[{"role": "user", "content": "设计导数教案并出配套试卷"}],
        plan_steps=["lesson_plan", "exam_quiz"],
        sub_agent_mode=True,
        thread_id="t-3", session_id="s-3",
    )
    final_state = await graph.ainvoke(state, config={"recursion_limit": 50})
    sub = final_state.get("sub_results", [])
    output = final_state.get("final_markdown_output", "")
    plan_dag = final_state.get("plan_dag") or {}
    print(f"  sub_results={len(sub)}, output 长度={len(output)}, plan_dag 节点={plan_dag.get('total_tasks')}")
    print(f"  output 含两个 agent 标题: {'lesson_plan' in output and 'exam_quiz' in output}")
    ok = len(sub) == 2 and len(output) > 0 and plan_dag.get("total_tasks") == 2
    print(f"  PASS: {ok}")
    return ok


async def test_compound_decomposition():
    """测试4：自然复合消息（不预置 plan_steps）经 supervisor 关键词拆解 → 并行聚合"""
    print("\n=== 测试4：复合请求关键词拆解 ===")
    install_registry({n: make_streaming_fake(n) for n in SPECIALIZED_AGENTS})
    collector = EventCollector()
    result = await teaching_graph_runner.run_workflow(
        user_message="请设计一份导数教案并出配套试题",
        session_id="s-4", thread_id="t-4",
        event_callback=collector,
        task_id="task-4",
    )
    sub = result.get("sub_results", [])
    names = {s.get("agent_name") for s in sub}
    output = result.get("output", "")
    print(f"  run_status={result['run_status']}, sub_results={sorted(names)}")
    ok = (
        result["run_status"] == "completed"
        and names == {"lesson_plan", "exam_quiz"}
        and len(output) > 0
    )
    print(f"  PASS: {ok}")
    return ok


async def main():
    r1 = await test_single_agent_tokens()
    r2 = await test_hitl_pause_resume()
    r3 = await test_parallel_aggregation()
    r4 = await test_compound_decomposition()
    results = [r1, r2, r3, r4]
    print(f"\n=== 总结果: {sum(results)}/4 通过 ===")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
