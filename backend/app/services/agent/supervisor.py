import logging
from typing import Dict, Any, List
from app.services.agent.state import AgentState
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter
from app.prompts.registry import prompt_registry

logger = logging.getLogger(__name__)

# 关键词 → 专业 agent（与 route 单路分流规则保持一致）
# 注意：math_solver 做特殊处理，避免"导数教案/积分试题"等选题语境误触发
_KEYWORD_AGENT_RULES = [
    ("code_grader", ["代码", "编程", "Python", "算法", "批改代码", "Debug", "LeetCode", "运行代码", "测试用例", "二分查找", "冒泡排序", "def ", "class "]),
    ("exam_quiz", ["考题", "试卷", "题目", "选择题", "解答题", "试题"]),
    ("academic_rag", ["论文", "文献", "学术", "期刊", "课题", "知网"]),
    ("socratic", ["引导", "怎么理解", "为什么", "启发", "不懂", "讲讲"]),
    ("curriculum", ["课标", "核心素养", "达标", "合规", "课标对标"]),
    ("rubric", ["批改", "作文", "评分", "得分点", "打分"]),
    ("slide_outline", ["课件", "PPT", "幻灯片", "演讲大纲"]),
    ("lesson_plan", ["教案", "教学设计", "备课", "学案"]),
]
# math_solver：动作词直接命中；话题词（导数/积分）仅在非"做教学制品"语境下命中
_MATH_ACTION_KEYWORDS = ["计算", "推导", "求证", "求解", "解方程", "化简", "极限", "算一下"]
_MATH_TOPIC_KEYWORDS = ["积分", "导数", "公式"]
_ARTIFACT_CONTEXT_KEYWORDS = [
    "教案", "教学设计", "备课", "学案", "试题", "试卷", "考题", "题目",
    "课件", "PPT", "幻灯片", "课标", "作文", "批改", "评分",
]
# 复合拆解时的固定展示顺序
_AGENT_ORDER = [
    "lesson_plan", "academic_rag", "exam_quiz", "socratic",
    "math_solver", "curriculum", "rubric", "slide_outline", "code_grader",
]


class SupervisorAgent:
    """
    Supervisor Agent (主控路由与规划中枢)
    1. Analyzes user intent & pedagogy domain
    2. Decomposes compound requests into atomic agent plans
    3. Routes to 8 specialized domain agents
    """

    SYSTEM_PROMPT = prompt_registry.render("supervisor.system")

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

    @classmethod
    def decompose_agents(cls, message: str) -> List[str]:
        """
        复合请求拆解：基于关键词命中，返回去重后的专业 agent 名列表（按固定顺序）。
        仅用于并行 sub-agent 调度；无命中时返回空列表由调用方回退单路。
        """
        if not message:
            return []
        hit_agents = {
            agent
            for agent, keywords in _KEYWORD_AGENT_RULES
            if any(kw in message for kw in keywords)
        }
        # math_solver 语境判别：动作词必中；纯话题词需排除"做教学制品"语境
        in_artifact_context = any(kw in message for kw in _ARTIFACT_CONTEXT_KEYWORDS)
        math_hit = any(kw in message for kw in _MATH_ACTION_KEYWORDS) or (
            not in_artifact_context and any(kw in message for kw in _MATH_TOPIC_KEYWORDS)
        )
        if math_hit:
            hit_agents.add("math_solver")
        return [name for name in _AGENT_ORDER if name in hit_agents]


supervisor_agent = SupervisorAgent()
