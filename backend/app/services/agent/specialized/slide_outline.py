import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter

logger = logging.getLogger(__name__)


class SlideOutlineMasterAgent(SubAgent):
    """
    课件与教学大纲专家 (SlideOutlineMaster)
    Transforms long instructional designs into presentation structures:
    - Slide-by-slide visual layout suggestions
    - Speaker notes
    - Interactive classroom quiz prompts
    """

    agent_name = "slide_outline"

    SYSTEM_PROMPT = """你是一名教学课件设计与PPT架构专家。
将教学方案提炼为结构清晰、重点突出的演示文稿大纲（PPT Outline）：
1. 规划幻灯片页数（通常 10-15 页/节课）；
2. 每一页注明【页面标题】、【核心要点】、【视觉图表/公式建议】与【教师讲授口令】；
3. 输出标准化 Markdown 格式，便于直通导出 PPTX。"""

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("slide_outline", len(user_prompt))

        messages = [
            {"role": "system", "content": cls.SYSTEM_PROMPT},
            {"role": "user", "content": f"请为以下内容生成PPT课件大纲：\n{user_prompt}"}
        ]

        resp = await bailian_client.acomplete(messages, model=model, temperature=0.3)
        markdown_text = resp["content"]

        return {
            "current_agent": "slide_outline",
            "next_agent": "quality_gate",
            "final_markdown_output": markdown_text,
            "structured_artifact": {
                "title": "教学课件与演讲大纲",
                "markdown": markdown_text
            },
            "artifact_type": "SLIDE_OUTLINE"
        }


slide_outline_agent = SlideOutlineMasterAgent()
SubAgentRegistry.register("slide_outline", SlideOutlineMasterAgent)
