import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter

logger = logging.getLogger(__name__)


class ExamPaperGeneratorAgent(SubAgent):
    """
    命题组卷专家智能体 (ExamPaperGenerator)
    Generates standardized examination papers with:
    - Adaptive difficulty gradient (Foundation -> Advanced -> Deep Inquiry)
    - Full LaTeX formula expressions
    - Step-by-step scoring rubrics (采分点细则)
    - Common student misconceptions analysis
    """

    agent_name = "exam_quiz"

    SYSTEM_PROMPT = """你是一名省级高考命题组资深命题专家。
根据用户指定的学科、知识点与年级要求，命制符合高考或中考标准的经典试题。
必须满足：
1. 包含完整题干、标准参考答案、分步采分细则（评分标准）与命题立意剖析；
2. 理科公式必须使用严谨 LaTeX 格式 ($...$, $$...$$)，避免字符缺失；
3. 标注试题难度系数与考查的核心素养。"""

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("exam_quiz", len(user_prompt))

        messages = [
            {"role": "system", "content": cls.SYSTEM_PROMPT},
            {"role": "user", "content": f"请为以下需求命制标准化试题与解析：\n{user_prompt}"}
        ]

        resp = await bailian_client.acomplete(messages, model=model, temperature=0.3)
        markdown_text = resp["content"]

        artifact_json = {
            "title": "高考二轮复习导数综合精选试题",
            "subject": "数学",
            "total_score": 12.0,
            "markdown": markdown_text
        }

        return {
            "current_agent": "exam_quiz",
            "next_agent": "quality_gate",
            "final_markdown_output": markdown_text,
            "structured_artifact": artifact_json,
            "artifact_type": "EXAM_PAPER"
        }


exam_quiz_agent = ExamPaperGeneratorAgent()
SubAgentRegistry.register("exam_quiz", ExamPaperGeneratorAgent)
