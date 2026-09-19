import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter
from app.prompts.registry import prompt_registry
from app.services.llm.prompt_router import prompt_router

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

    SYSTEM_PROMPT = prompt_registry.render("socratic.system")

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("socratic", len(user_prompt))

        messages = [
            {"role": "system", "content": prompt_router.build_system(
                "socratic.system", user_prompt, user_id=state.get("user_id"))},
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
