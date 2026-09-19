import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter

logger = logging.getLogger(__name__)


class MathFormulaSolverAgent(SubAgent):
    """
    数理推导专家 (MathFormulaSolver)
    Dedicated to rigorous mathematical & physics calculus, proofs, and algebraic derivations:
    - Verifies formula step-by-step
    - 100% syntactically correct LaTeX ($...$ and $$...$$)
    - Validates boundary conditions and domains
    """

    agent_name = "math_solver"

    SYSTEM_PROMPT = """你是一名严谨的高等数学与理论物理推导专家。
你的职责是进行绝对精确的数学推导与计算：
1. 每一个定理应用（如中值定理、洛必达法则、泰勒展开）必须声明使用前提条件；
2. 每一行推导严格换行并使用行间公式 $$...$$；
3. 检查定义域与极值点分类讨论的完整性。"""

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("math_solver", len(user_prompt))

        messages = [
            {"role": "system", "content": cls.SYSTEM_PROMPT},
            {"role": "user", "content": f"请给出严密的推导过程：\n{user_prompt}"}
        ]

        resp = await bailian_client.acomplete(messages, model=model, temperature=0.1)
        markdown_text = resp["content"]

        return {
            "current_agent": "math_solver",
            "next_agent": "quality_gate",
            "final_markdown_output": markdown_text,
            "structured_artifact": {
                "title": "数理推导成果",
                "markdown": markdown_text
            },
            "artifact_type": "MATH_PROOF"
        }


math_solver_agent = MathFormulaSolverAgent()
SubAgentRegistry.register("math_solver", MathFormulaSolverAgent)
