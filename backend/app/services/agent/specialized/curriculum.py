import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter

logger = logging.getLogger(__name__)


class CurriculumAlignerAgent(SubAgent):
    """
    新课标素养对标专家 (CurriculumAligner)
    Validates learning objectives, teaching activities, and exam questions against
    National Curriculum Standards (新课程方案与核心素养对标审查).
    """

    agent_name = "curriculum"

    SYSTEM_PROMPT = """你是一名教育部基础教育课程教材发展中心教研督导专家。
你的任务是对提交的教案或试卷进行新课标核心素养达成度审查：
1. 评估学科核心素养的渗透维度与深度（达标、部分达标、未达标）；
2. 检查活动设计是否体现“学生为主体、探究为本”的课改精神；
3. 输出具体的修改建议清单与素养雷达评分。"""

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("curriculum", len(user_prompt))

        messages = [
            {"role": "system", "content": cls.SYSTEM_PROMPT},
            {"role": "user", "content": f"请针对以下内容进行新课标素养合规与达标度审查：\n{user_prompt}"}
        ]

        resp = await bailian_client.acomplete(messages, model=model, temperature=0.2)
        return {
            "current_agent": "curriculum",
            "next_agent": "quality_gate",
            "final_markdown_output": resp["content"],
            "artifact_type": "CURRICULUM_REPORT"
        }


curriculum_aligner_agent = CurriculumAlignerAgent()
SubAgentRegistry.register("curriculum", CurriculumAlignerAgent)
