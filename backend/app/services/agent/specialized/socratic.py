import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter

logger = logging.getLogger(__name__)


class SocraticTutorAgent(SubAgent):
    """
    苏格拉底式答疑专家 (SocraticTutor)
    Pedagogical design:
    - Never dumps the full direct answer immediately.
    - Asks targeted leading questions to ignite student metacognition.
    - Provides scaffolded hints based on the student's difficulty level.
    """

    agent_name = "socratic"

    SYSTEM_PROMPT = """你是一名秉持“苏格拉底教学法”的优秀学科导师。
面对学生的困惑或提问：
1. 严禁直接抛出最终答案或全套推导！
2. 首先肯定学生探索的积极性，精准找出其思维卡点；
3. 用一到两个具有启发性的反问或生活类比，引导学生自主思考下一步；
4. 每次只推进一个认知台阶，保持对话的探究性与亲和力。"""

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("socratic", len(user_prompt))

        messages = [
            {"role": "system", "content": cls.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]

        resp = await bailian_client.acomplete(messages, model=model, temperature=0.6)
        return {
            "current_agent": "socratic",
            "next_agent": "quality_gate",
            "final_markdown_output": resp["content"],
            "artifact_type": "SOCRATIC_GUIDE"
        }


socratic_tutor_agent = SocraticTutorAgent()
SubAgentRegistry.register("socratic", SocraticTutorAgent)
