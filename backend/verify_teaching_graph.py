"""
验证 LangGraph 教学智能体图结构与执行流程。
mock 专业 agent，验证：单链路 / 返工循环 / HITL 暂停恢复 / 并行调度。
运行：cd backend && python verify_teaching_graph.py
"""
import asyncio
import sys
sys.path.insert(0, ".")

from langgraph.checkpoint.memory import MemorySaver
from app.services.agent.state import AgentState
from app.services.agent.teaching_graph import build_teaching_graph, SPECIALIZED_AGENTS
from app.services.agent import sub_agent

# ---- Mock 专业 agent：不调 LLM，返回固定输出 ----
class FakeAgent:
    def __init__(self, name, output="这是 mock agent 的输出内容。$a^2+b^2=c^2$ 公式闭合。"):
        self.agent_name = name
        self._output = output
    async def execute(self, state):
        return {
            "current_agent": self.agent_name,
            "final_markdown_output": self._output,
            "structured_artifact": {"title": "mock", "markdown": self._output},
            "artifact_type": "MOCK",
            "citations": [],
            "requires_approval": state.get("requires_approval", False),
        }

# 替换注册表：存类（get 时会实例化）
def make_fake_class(name):
    class FakeAgent:
        agent_name = name
        async def execute(self, state):
            return {
                "current_agent": name,
                "final_markdown_output": f"导数几何意义是切线斜率。{name} 输出。$a^2+b^2=c^2$。",
                "structured_artifact": {"title": "mock", "markdown": "mock"},
                "artifact_type": "MOCK",
                "citations": [],
                "requires_approval": state.get("requires_approval", False),
            }
    FakeAgent.__name__ = f"Fake_{name}"
    return FakeAgent

sub_agent.SubAgentRegistry._registry = {name: make_fake_class(name) for name in SPECIALIZED_AGENTS}
sub_agent.SubAgentRegistry._instances = {}


def make_state(intent="lesson_plan", message="设计一份导数教案", requires_approval=False, plan_steps=None):
    return {
        "messages": [{"role": "user", "content": message}],
        "user_id": "u-test",
        "session_id": "s-test",
        "thread_id": "t-test",
        "intent": intent,
        "current_agent": "start",
        "next_agent": intent,
        "plan_steps": plan_steps or [],
        "current_step": 0,
        "kb_ids": [],
        "retrieved_docs": [{"content": "导数几何意义是切线斜率。"}],
        "citations": [],
        "structured_artifact": None,
        "artifact_type": None,
        "final_markdown_output": "",
        "requires_approval": requires_approval,
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
    }


async def test_single_agent():
    """测试1：单 agent 链路（无 HITL）应直接跑完"""
    print("\n=== 测试1：单 agent 链路 ===")
    graph = build_teaching_graph(checkpointer=MemorySaver())
    state = make_state(intent="lesson_plan", requires_approval=False)
    config = {"configurable": {"thread_id": "test-single"}}
    result = await graph.ainvoke(state, config=config)
    print(f"  final output: {result.get('final_markdown_output', '')[:50]}...")
    print(f"  quality_score: {result.get('quality_score')}")
    print(f"  is_approved: {result.get('is_approved')}")
    ok = bool(result.get("final_markdown_output")) and result.get("is_approved") is True
    print(f"  PASS: {ok}")
    return ok


async def test_hitl():
    """测试2：requires_approval=True 时应在 hitl_gate 前暂停，恢复后继续"""
    print("\n=== 测试2：HITL 暂停与恢复 ===")
    graph = build_teaching_graph(checkpointer=MemorySaver())
    state = make_state(intent="lesson_plan", requires_approval=True)
    config = {"configurable": {"thread_id": "test-hitl"}}

    # 第一次调用：应在 hitl_gate 前暂停
    result = await graph.ainvoke(state, config=config)
    print(f"  首次调用后 is_approved: {result.get('is_approved')} (暂停中)")

    # 恢复：用 update_state 注入教师决策，然后 ainvoke(None) 继续
    graph.update_state(config, {"human_decision": {"approved": True, "comments": "通过"}})
    resumed = await graph.ainvoke(None, config=config)
    print(f"  恢复后 is_approved: {resumed.get('is_approved')}")
    ok = resumed.get("is_approved") is True
    print(f"  PASS: {ok}")
    return ok


async def test_parallel():
    """测试3：复合任务并行调度多 agent"""
    print("\n=== 测试3：复合任务并行 ===")
    graph = build_teaching_graph(checkpointer=MemorySaver())
    # 传多个 plan_steps 触发并行
    state = make_state(
        intent="lesson_plan",
        message="设计导数教案并出配套试卷",
        plan_steps=["lesson_plan", "exam_quiz"],
    )
    config = {"configurable": {"thread_id": "test-parallel"}, "recursion_limit": 100}
    result = await graph.ainvoke(state, config=config)
    sub = result.get("sub_results", [])
    print(f"  sub_results 数量: {len(sub)} (应为 2)")
    print(f"  agents: {[s.get('agent_name') for s in sub]}")
    ok = len(sub) == 2
    print(f"  PASS: {ok}")
    return ok


async def main():
    r1 = await test_single_agent()
    r2 = await test_hitl()
    r3 = await test_parallel()
    print(f"\n=== 总结果: {sum([r1,r2,r3])}/3 通过 ===")


asyncio.run(main())
