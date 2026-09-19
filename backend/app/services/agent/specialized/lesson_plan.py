import logging
from typing import Dict, Any, Optional, Callable, Awaitable
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter
from app.services.llm.structured import structured_acomplete, aextract_structured
from app.services.llm.prompt_router import prompt_router
from app.schemas.lesson_plan import LessonPlanStructured

logger = logging.getLogger(__name__)


class LessonPlanMasterAgent(SubAgent):
    """
    教案大师智能体 (LessonPlanMaster)
    Specialized in pedagogical engineering:
    - Structured lesson design (Core competencies, learning analysis, teaching steps, blackboard design)
    - Output format: Full Markdown + Structured JSON artifact for right-side canvas

    Prompt Engineering 与推理优化：
    - Role Prompting（资深教研员角色，由 PromptRegistry 统一管理模板）
    - Structured Output Constraint：非流式路径走三层结构化约束
      (Prompt Schema 注入 -> API json_object 模式 -> Pydantic 校验+错误反馈重试)
    - 流式路径采用「生成-抽取」两段式：先保 SSE 打字机体验输出 Markdown，
      再由轻量模型(qwen-turbo)做二次结构化抽取填充 artifact
    - 结构化失败自动降级为自由文本路径，不阻断业务主流程
    """

    agent_name = "lesson_plan"

    @classmethod
    def _fallback_artifact(cls, markdown_text: str) -> Dict[str, Any]:
        """结构化输出降级时的兜底 artifact（保持与旧版字段兼容）"""
        return {
            "title": "教学设计方案",
            "subject": "",
            "grade_level": "",
            "textbook_version": "人教A版",
            "class_duration": 45,
            "markdown": markdown_text,
        }

    @classmethod
    def _render_markdown(cls, plan: LessonPlanStructured) -> str:
        """将结构化教案渲染为教师可读的 Markdown（保持旧版阅读体验）"""
        comps = "\n".join(f"- **{c.name}**：{c.description}" for c in plan.core_competencies)
        objectives = "\n".join(f"{i}. {o}" for i, o in enumerate(plan.teaching_objectives, 1))
        keys = "\n".join(f"- {k}" for k in plan.teaching_key_points)
        diffs = "\n".join(f"- {d}" for d in plan.teaching_difficult_points)

        steps_md = []
        for s in plan.teaching_steps:
            steps_md.append(
                f"### 环节{s.step_number}：{s.title}（约 {s.duration_minutes} 分钟）\n\n"
                f"| 项目 | 内容 |\n| :--- | :--- |\n"
                f"| 教师活动 | {s.teacher_activity} |\n"
                f"| 学生活动 | {s.student_activity} |\n"
                f"| 设计意图 | {s.design_intent} |"
            )
        steps = "\n\n".join(steps_md)

        board = plan.blackboard_design
        board_md = (
            f"**布局**：{board.layout_type}\n\n"
            f"**正板书**\n" + "\n".join(f"- {x}" for x in board.main_board) + "\n\n"
            f"**副板书**\n" + "\n".join(f"- {x}" for x in board.auxiliary_board)
        )
        homework = "\n".join(f"- {a}" for a in plan.assignment)

        return (
            f"# {plan.title}\n\n"
            f"> 学科：{plan.subject} ｜ 学段：{plan.grade_level} ｜ "
            f"教材：{plan.textbook_version} ｜ 课时：{plan.class_duration} 分钟\n\n"
            f"## 一、学情分析\n{plan.learning_analysis}\n\n"
            f"## 二、核心素养对标\n{comps}\n\n"
            f"## 三、教学目标\n{objectives}\n\n"
            f"## 四、教学重难点\n**重点**\n{keys}\n\n**难点与突破**\n{diffs}\n\n"
            f"## 五、教学过程\n{steps}\n\n"
            f"## 六、板书设计\n{board_md}\n\n"
            f"## 七、分层作业设计\n{homework}\n\n"
            f"## 八、教学反思指引\n{plan.teaching_reflection_prompt}"
        )

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        """非流式入口：三层结构化约束生成教案，失败降级为自由文本"""
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("lesson_plan", len(user_prompt))

        messages = [
            {"role": "system", "content": prompt_router.build_system(
                "lesson_plan.system", user_prompt, user_id=state.get("user_id"))},
            {"role": "user", "content": f"请为以下课题设计完整的教学方案：\n{user_prompt}"}
        ]

        plan, meta = await structured_acomplete(
            messages, LessonPlanStructured, model=model, temperature=0.3,
            prompt_id="lesson_plan.system", agent="lesson_plan",
            session_id=state.get("session_id"), user_id=state.get("user_id"),
        )
        logger.info(
            f"[LessonPlan] structured={'OK' if plan else 'FALLBACK'} "
            f"attempts={meta['attempts']} api_json_mode={meta['api_json_mode']} model={meta['model']}"
        )

        if plan is not None:
            markdown_text = cls._render_markdown(plan)
            artifact_json = plan.model_dump()
            artifact_json["markdown"] = markdown_text
        else:
            # 降级路径：与旧版一致，LLM 自由文本 + 代码兜底 artifact
            resp = await bailian_client.acomplete(messages, model=model, temperature=0.3)
            markdown_text = resp["content"]
            artifact_json = cls._fallback_artifact(markdown_text)

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
        """流式入口：SSE 打字机优先保体验，结束后二次抽取结构化 artifact"""
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("lesson_plan", len(user_prompt))

        messages = [
            {"role": "system", "content": prompt_router.build_system(
                "lesson_plan.stream", user_prompt, user_id=state.get("user_id"))},
            {"role": "user", "content": f"请为以下课题设计完整的教学方案：\n{user_prompt}"}
        ]

        markdown_text = await bailian_client.astream_with_callback(
            messages, on_token=on_token, model=model, temperature=0.3
        )

        # 生成-抽取两段式：轻量模型把流式 Markdown 抽取为结构化教案
        extract_model = ModelRouter.route_model("hyde_generator")  # LIGHT 档
        plan, meta = await aextract_structured(
            markdown_text, LessonPlanStructured, model=extract_model,
            prompt_id="lesson_plan.stream", agent="lesson_plan",
            session_id=state.get("session_id"), user_id=state.get("user_id"),
        )
        logger.info(
            f"[LessonPlan][stream] extract={'OK' if plan else 'FALLBACK'} attempts={meta['attempts']}"
        )

        if plan is not None:
            artifact_json = plan.model_dump()
            artifact_json["markdown"] = markdown_text
        else:
            artifact_json = cls._fallback_artifact(markdown_text)

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
