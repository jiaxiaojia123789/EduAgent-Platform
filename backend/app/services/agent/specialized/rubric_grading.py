import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter
from app.services.llm.structured import structured_acomplete
from app.services.llm.prompt_router import prompt_router
from app.schemas.rubric_grading import RubricReportStructured

logger = logging.getLogger(__name__)


class RubricGradingAgent(SubAgent):
    """
    主观题与作文智能批改专家 (RubricGrading)
    Provides diagnostic rubric evaluation:
    - Multi-dimensional scoring (Content, Structure, Expression, Critical Thinking)
    - Specific line-by-line constructive feedback
    - Polishing & upgrading exemplar demonstration

    Prompt Engineering 与推理优化：
    - Role Prompting（阅卷组长角色）由 PromptRegistry 统一管理
    - Structured Output Constraint：Prompt Schema 注入 -> API json_object 模式
      -> Pydantic 校验 + 错误反馈自动重试；失败降级为自由文本路径
    - 批改任务使用低温(temperature=0.2)保障评分稳定性
    """

    agent_name = "rubric"

    @classmethod
    def _render_markdown(cls, report: RubricReportStructured) -> str:
        """将结构化批改报告渲染为教师/学生可读 Markdown"""
        dims = "\n".join(
            f"| {d.dimension} | {d.score:g} / {d.max_score:g} | {d.comment} |"
            for d in report.dimensions
        )
        deductions = "\n".join(f"- {x}" for x in report.deductions) or "- 无"
        suggestions = "\n".join(f"- {x}" for x in report.improvement_suggestions) or "- 无"
        return (
            f"# 📝 量规诊断批改报告\n\n"
            f"## 一、总评\n"
            f"**综合得分：{report.overall_score:g} / 100**"
            f"（预估档次：{report.grade_level_estimate or '未评级'}）\n\n"
            f"{report.overall_comment}\n\n"
            f"## 二、分项量规评分\n\n"
            f"| 评价维度 | 得分 | 评语 |\n| :--- | :--- | :--- |\n{dims}\n\n"
            f"## 三、失分点与扣分依据\n{deductions}\n\n"
            f"## 四、改写建议\n{suggestions}\n\n"
            f"## 五、升格示范\n{report.upgraded_example}"
        )

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("rubric", len(user_prompt))

        messages = [
            {"role": "system", "content": prompt_router.build_system(
                "rubric.system", user_prompt, user_id=state.get("user_id"))},
            {"role": "user", "content": f"请对以下学生作答进行量规诊断批阅：\n{user_prompt}"}
        ]

        report, meta = await structured_acomplete(
            messages, RubricReportStructured, model=model, temperature=0.2,
            prompt_id="rubric.system", agent="rubric_grading",
            session_id=state.get("session_id"), user_id=state.get("user_id"),
        )
        logger.info(
            f"[RubricGrading] structured={'OK' if report else 'FALLBACK'} "
            f"attempts={meta['attempts']} api_json_mode={meta['api_json_mode']} model={meta['model']}"
        )

        if report is not None:
            markdown_text = cls._render_markdown(report)
            artifact_json = report.model_dump()
            artifact_json["markdown"] = markdown_text
        else:
            # 降级路径：与旧版一致，自由文本输出
            resp = await bailian_client.acomplete(messages, model=model, temperature=0.2)
            markdown_text = resp["content"]
            artifact_json = {"markdown": markdown_text}

        return {
            "current_agent": "rubric",
            "next_agent": "quality_gate",
            "final_markdown_output": markdown_text,
            "structured_artifact": artifact_json,
            "artifact_type": "RUBRIC_REPORT"
        }


rubric_grading_agent = RubricGradingAgent()
SubAgentRegistry.register("rubric", RubricGradingAgent)
