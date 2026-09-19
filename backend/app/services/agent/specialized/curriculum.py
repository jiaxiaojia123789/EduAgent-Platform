import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter
from app.prompts.registry import prompt_registry
from app.services.llm.prompt_router import prompt_router

logger = logging.getLogger(__name__)


class CurriculumAlignerAgent(SubAgent):
    """
    新课标素养对标专家 (CurriculumAligner)
    Validates learning objectives, teaching activities, and exam questions against
    National Curriculum Standards (新课程方案与核心素养对标审查).
    """

    agent_name = "curriculum"

    SYSTEM_PROMPT = prompt_registry.render("curriculum.system")

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("curriculum", len(user_prompt))

        messages = [
            {"role": "system", "content": prompt_router.build_system(
                "curriculum.system", user_prompt, user_id=state.get("user_id"))},
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
