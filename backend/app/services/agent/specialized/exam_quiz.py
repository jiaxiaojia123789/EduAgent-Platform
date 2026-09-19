import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.agent.sub_agent import SubAgent, SubAgentRegistry
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter
from app.services.llm.structured import structured_acomplete
from app.services.llm.prompt_router import prompt_router
from app.schemas.exam_quiz import ExamPaperStructured

logger = logging.getLogger(__name__)


class ExamPaperGeneratorAgent(SubAgent):
    """
    命题组卷专家智能体 (ExamPaperGenerator)
    Generates standardized examination papers with:
    - Adaptive difficulty gradient (Foundation -> Advanced -> Deep Inquiry)
    - Full LaTeX formula expressions
    - Step-by-step scoring rubrics (采分点细则)
    - Common student misconceptions analysis

    Prompt Engineering 与推理优化：
    - Role Prompting（命题专家角色）由 PromptRegistry 统一管理
    - Structured Output Constraint：Prompt Schema 注入 -> API json_object 模式
      -> Pydantic 校验 + 错误反馈自动重试；失败降级为自由文本路径
    """

    agent_name = "exam_quiz"

    @classmethod
    def _render_markdown(cls, paper: ExamPaperStructured) -> str:
        """将结构化试卷渲染为可打印 Markdown 试卷"""
        q_md = []
        for q in paper.questions:
            lines = [
                f"### 第 {q.question_number} 题（{q.question_type}，{q.score:g} 分，难度：{q.difficulty}）",
                f"**考查知识点**：{'、'.join(q.knowledge_points)}",
                "",
                q.stem,
            ]
            if q.options:
                lines += ["", *[f"- **{o.label}.** {o.content}" for o in q.options]]
            lines += [
                "",
                f"**【参考答案】** {q.standard_answer}",
            ]
            if q.rubric_steps:
                lines += ["", "**【采分点细则】**", *[f"- {s}" for s in q.rubric_steps]]
            lines += ["", f"**【命题意图与解析】** {q.analysis}"]
            if q.common_mistakes:
                lines += ["", f"**【易错警示】** {q.common_mistakes}"]
            q_md.append("\n".join(lines))

        instructions = "\n".join(f"{i}. {x}" for i, x in enumerate(paper.instructions, 1))
        return (
            f"# {paper.paper_title}\n\n"
            f"> 学科：{paper.subject} ｜ 年级：{paper.grade_level} ｜ "
            f"满分：{paper.total_score:g} 分 ｜ 时长：{paper.duration_minutes} 分钟\n\n"
            f"## 考生须知\n{instructions}\n\n"
            + "\n\n---\n\n".join(q_md)
        )

    @classmethod
    async def execute(cls, state: AgentState) -> Dict[str, Any]:
        user_prompt = state["messages"][-1]["content"] if state["messages"] else ""
        model = ModelRouter.route_model("exam_quiz", len(user_prompt))

        messages = [
            {"role": "system", "content": prompt_router.build_system(
                "exam_quiz.system", user_prompt, user_id=state.get("user_id"))},
            {"role": "user", "content": f"请为以下需求命制标准化试题与解析：\n{user_prompt}"}
        ]

        paper, meta = await structured_acomplete(
            messages, ExamPaperStructured, model=model, temperature=0.3,
            prompt_id="exam_quiz.system", agent="exam_quiz",
            session_id=state.get("session_id"), user_id=state.get("user_id"),
        )
        logger.info(
            f"[ExamQuiz] structured={'OK' if paper else 'FALLBACK'} "
            f"attempts={meta['attempts']} api_json_mode={meta['api_json_mode']} model={meta['model']}"
        )

        if paper is not None:
            markdown_text = cls._render_markdown(paper)
            artifact_json = paper.model_dump()
            artifact_json["markdown"] = markdown_text
        else:
            # 降级路径：自由文本 + 代码兜底 artifact
            resp = await bailian_client.acomplete(messages, model=model, temperature=0.3)
            markdown_text = resp["content"]
            artifact_json = {
                "title": "标准化测试卷",
                "subject": "",
                "total_score": 100.0,
                "markdown": markdown_text,
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
