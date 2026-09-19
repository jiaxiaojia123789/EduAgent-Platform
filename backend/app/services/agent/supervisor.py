import logging
from typing import Dict, Any
from app.services.agent.state import AgentState
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter

logger = logging.getLogger(__name__)


class SupervisorAgent:
    """
    Supervisor Agent (主控路由与规划中枢)
    1. Analyzes user intent & pedagogy domain
    2. Decomposes compound requests into atomic agent plans
    3. Routes to 8 specialized domain agents
    """

    SYSTEM_PROMPT = """你是一个教育垂类AI中台的【Supervisor主控智能体】。
你的核心任务是分析教师或学生的用户需求，准确判断其意图，并将任务分派给最匹配的专业智能体：
1. lesson_plan: 教学方案、导学案、教学重难点、课堂环节设计
2. academic_rag: 学术期刊论文研读、文献综述、课改课题论证
3. exam_quiz: 命题出题、历年真题模拟、难度梯度组卷、试题详解
4. socratic: 启发式辅导答疑、循循善诱、不直接给答案、思维破冰
5. math_solver: 复杂数理公式推导、微积分/几何严格分步计算
6. curriculum: 新课标核心素养对标审查、教学评价达标检验
7. rubric: 作文与主观题批改、采分点打分与升格指导
8. slide_outline: 课件PPT结构大纲、演讲提纲制作
9. code_grader: 代码自动批改与运行沙箱、算法复杂度诊断、测试用例评测、代码Bug修复与重构指导

请严格分析并输出意图标签。"""

    @classmethod
    async def route(cls, state: AgentState) -> Dict[str, Any]:
        last_message = state["messages"][-1]["content"] if state["messages"] else ""
        explicit_agent = state.get("intent")

        # If user explicitly selected an agent from the Doubao UI sidebar
        valid_agents = [
            "lesson_plan", "academic_rag", "exam_quiz", "socratic",
            "math_solver", "curriculum", "rubric", "slide_outline", "code_grader"
        ]
        if explicit_agent in valid_agents:
            logger.info(f"[Supervisor] Explicit routing to: {explicit_agent}")
            return {
                "intent": explicit_agent,
                "current_agent": "supervisor",
                "next_agent": explicit_agent,
                "current_step": 1,
                "plan_steps": [f"由 {explicit_agent} 执行专业推理"]
            }

        # Dynamic intent classification
        classified_intent = "lesson_plan"  # Default
        if any(w in last_message for w in ["代码", "编程", "Python", "算法", "批改代码", "Debug", "LeetCode", "运行代码", "测试用例", "二分查找", "冒泡排序", "def ", "class "]):
            classified_intent = "code_grader"
        elif any(w in last_message for w in ["考题", "试卷", "题目", "选择题", "解答题", "试题"]):
            classified_intent = "exam_quiz"
        elif any(w in last_message for w in ["论文", "文献", "学术", "期刊", "课题", "知网"]):
            classified_intent = "academic_rag"
        elif any(w in last_message for w in ["引导", "怎么理解", "为什么", "启发", "不懂", "讲讲"]):
            classified_intent = "socratic"
        elif any(w in last_message for w in ["计算", "推导", "积分", "导数", "公式", "求证"]):
            classified_intent = "math_solver"
        elif any(w in last_message for w in ["课标", "核心素养", "达标", "合规", "课标对标"]):
            classified_intent = "curriculum"
        elif any(w in last_message for w in ["批改", "作文", "评分", "得分点", "打分"]):
            classified_intent = "rubric"
        elif any(w in last_message for w in ["课件", "PPT", "幻灯片", "演讲大纲"]):
            classified_intent = "slide_outline"

        logger.info(f"[Supervisor] Intelligently routed to: {classified_intent}")
        return {
            "intent": classified_intent,
            "current_agent": "supervisor",
            "next_agent": classified_intent,
            "current_step": 1,
            "plan_steps": [f"Supervisor 分派任务给 {classified_intent} 专家"]
        }


supervisor_agent = SupervisorAgent()
