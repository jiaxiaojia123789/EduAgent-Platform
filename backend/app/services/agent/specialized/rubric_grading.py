import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter

logger = logging.getLogger(__name__)


class RubricGradingAgent(SubAgent):
    """
    主观题与作文智能批改专家 (RubricGrading)
    Provides diagnostic rubric evaluation:
    - Multi-dimensional scoring (Content, Structure, Expression, Critical Thinking)
    - Specific line-by-line constructive feedback
    - Polishing & upgrading exemplar demonstration
    """

    agent_name = "rubric"

    SYSTEM_PROMPT = """你是一名资深中高考阅卷组长。
请对学生提交的习题作答或作文进行诊断式量规（Rubric）批阅：
1. 给出分项得分（如审题立意、论据论证、语言表达）及总分；
2. 明确指出失分点并说明扣分依据；
3. 提供具体的改写建议与升格示范。"""

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("rubric", len(user_prompt))

        messages = [
            {"role": "system", "content": cls.SYSTEM_PROMPT},
            {"role": "user", "content": f"请对以下学生作答进行量规诊断批阅：\n{user_prompt}"}
        ]

        resp = await bailian_client.acomplete(messages, model=model, temperature=0.2)
        return {
            "current_agent": "rubric",
            "next_agent": "quality_gate",
            "final_markdown_output": resp["content"],
            "artifact_type": "RUBRIC_REPORT"
        }


rubric_grading_agent = RubricGradingAgent()
SubAgentRegistry.register("rubric", RubricGradingAgent)
