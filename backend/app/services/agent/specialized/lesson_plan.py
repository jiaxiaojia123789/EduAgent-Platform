import logging
from typing import Dict, Any, Optional, Callable, Awaitable
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter
from app.schemas.lesson_plan import LessonPlanStructured

logger = logging.getLogger(__name__)


class LessonPlanMasterAgent(SubAgent):
    """
    教案大师智能体 (LessonPlanMaster)
    Specialized in pedagogical engineering:
    - Structured lesson design (Core competencies, learning analysis, teaching steps, blackboard design)
    - Output format: Full Markdown + Structured JSON artifact for right-side canvas

    重构说明：
    - 新增 execute_stream：边产出 token 边通过 on_token 回调推送，实现豆包风格流式打字机
    - 保留 execute 兼容原有同步调用
    - 继承 SubAgent：可被 OrchestratorAgent 动态调度，上下文隔离由 ContextManager 管理
    """

    agent_name = "lesson_plan"

    SYSTEM_PROMPT = """你是一名资深教研员与特级教师，精通教育部最新版学科《课程标准》。
请根据用户的教学主题与学段要求，设计一份结构严谨、教学法先进、符合核心素养导向的高质量教学设计。
要求：
1. 核心素养对标清晰（如数学抽象、逻辑推理、直观想象等）；
2. 教学过程包含明确的教师启发活动与学生活动；
3. 数学/物理/化学等理科公式必须使用严谨标准的 LaTeX 语法 ($...$, $$...$$)；
4. 包含规范的板书设计与分层作业布置。"""

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        """非流式入口：原逻辑保留"""
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("lesson_plan", len(user_prompt))

        messages = [
            {"role": "system", "content": cls.SYSTEM_PROMPT},
            {"role": "user", "content": f"请为以下课题设计完整的教学方案：\n{user_prompt}"}
        ]

        resp = await bailian_client.acomplete(messages, model=model, temperature=0.3)
        markdown_text = resp["content"]

        artifact_json = {
            "title": "高一数学《导数的几何意义》教学设计",
            "subject": "高中数学",
            "grade_level": "高二选择性必修",
            "textbook_version": "人教A版",
            "class_duration": 45,
            "markdown": markdown_text
        }

        return {
            "current_agent": "lesson_plan",
            "next_agent": "quality_gate",
            "final_markdown_output": markdown_text,
            "structured_artifact": artifact_json,
            "artifact_type": "LESSON_PLAN",
            "requires_approval": False
        }

    @classmethod
    async def execute_stream(
        cls,
        state: AgentState,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """流式入口：边产出边推送 SSE token"""
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("lesson_plan", len(user_prompt))

        messages = [
            {"role": "system", "content": cls.SYSTEM_PROMPT},
            {"role": "user", "content": f"请为以下课题设计完整的教学方案：\n{user_prompt}"}
        ]

        markdown_text = await bailian_client.astream_with_callback(
            messages, on_token=on_token, model=model, temperature=0.3
        )

        artifact_json = {
            "title": "高一数学《导数的几何意义》教学设计",
            "subject": "高中数学",
            "grade_level": "高二选择性必修",
            "textbook_version": "人教A版",
            "class_duration": 45,
            "markdown": markdown_text
        }

        return {
            "current_agent": "lesson_plan",
            "next_agent": "quality_gate",
            "final_markdown_output": markdown_text,
            "structured_artifact": artifact_json,
            "artifact_type": "LESSON_PLAN",
            "requires_approval": False
        }


lesson_plan_agent = LessonPlanMasterAgent()
SubAgentRegistry.register("lesson_plan", LessonPlanMasterAgent)
