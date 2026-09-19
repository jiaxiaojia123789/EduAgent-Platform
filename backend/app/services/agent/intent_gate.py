"""
IntentGate — 总控入口的意图门控（低成本分流）

用 qwen-turbo 小模型快速判定用户请求：
  - complexity=trivial：单一意图/单一学科 → 单 Agent 直达，绕过 Plan，
    省一次规划 LLM 调用、降低延迟
  - complexity=compound：≥2 意图或跨教学环节 → 进入 Plan 多 Agent 协作

失败降级：LLM 不可用/配额耗尽时，用关键词规则判定
（复用 SubAgentRegistry.is_compound_request + Supervisor 关键词路由），
保证门控永不阻塞主流程。
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter
from app.core.config import settings

logger = logging.getLogger(__name__)

VALID_AGENTS = [
    "lesson_plan", "academic_rag", "exam_quiz", "socratic",
    "math_solver", "curriculum", "rubric", "slide_outline", "code_grader",
]

GATE_SYSTEM_PROMPT = """你是教育AI中台的意图门控器。请用一次快速判定给用户请求分流。

可用智能体：
lesson_plan(教案), academic_rag(文献研读), exam_quiz(命题组卷), socratic(启发答疑),
math_solver(数理推导), curriculum(课标对标), rubric(主观题批改),
slide_outline(课件大纲), code_grader(代码批改)

判定规则：
- trivial：只需要一个智能体即可完成的单一意图请求（如"出5道选择题"、"解释这个公式"）
- compound：包含两个及以上可独立拆分的意图，或明确跨环节/要求协作
  （如"分析这份文档并出配套试卷再写教案"）

严格输出 JSON（不要 markdown 代码块）：
{
  "complexity": "trivial",
  "agent": "exam_quiz",
  "candidates": ["exam_quiz"],
  "reason": "单一命题意图"
}
agent：trivial 时填唯一目标智能体；compound 时填空字符串。
candidates：按匹配度排序的相关智能体列表。"""


@dataclass
class GateDecision:
    complexity: str                  # "trivial" / "compound"
    agent: Optional[str]             # trivial 时的目标 agent
    candidates: List[str] = field(default_factory=list)
    reason: str = ""
    source: str = "llm"              # "llm" / "fallback"


class IntentGate:
    """意图门控器。"""

    async def classify(self, message: str) -> GateDecision:
        try:
            model = ModelRouter.route_model(None, 0) if hasattr(ModelRouter, "route_model") else settings.ROUTER_LIGHT_MODEL
            messages = [
                {"role": "system", "content": GATE_SYSTEM_PROMPT},
                {"role": "user", "content": f"用户请求：\n{message[:1500]}"},
            ]
            resp = await bailian_client.acomplete(
                messages, model=settings.ROUTER_LIGHT_MODEL,
                temperature=0.0, max_tokens=400,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.get("content", "{}"))
            complexity = data.get("complexity", "trivial")
            if complexity not in ("trivial", "compound"):
                complexity = "trivial"
            agent = data.get("agent") or None
            if agent not in VALID_AGENTS:
                agent = None
            candidates = [a for a in (data.get("candidates") or []) if a in VALID_AGENTS]

            # LLM 把复合误判为 trivial 且未给 agent → 交回规则兜底
            if complexity == "trivial" and agent is None:
                return self._fallback(message)

            logger.info(
                f"[IntentGate] {complexity} agent={agent} candidates={candidates} "
                f"reason={data.get('reason', '')}"
            )
            return GateDecision(complexity, agent, candidates, data.get("reason", ""), "llm")

        except Exception as e:
            logger.warning(f"[IntentGate] LLM 门控失败，降级关键词规则: {e}")
            return self._fallback(message)

    # ------------------------------------------------------------------
    def _fallback(self, message: str) -> GateDecision:
        """规则降级：复合请求检测 + 关键词路由。"""
        from app.services.agent.sub_agent import SubAgentRegistry

        is_compound = SubAgentRegistry.is_compound_request(message)
        if is_compound:
            return GateDecision("compound", None, [], "规则检测到复合意图", "fallback")

        agent = self._keyword_route(message)
        return GateDecision("trivial", agent, [agent], "关键词单意图路由", "fallback")

    @staticmethod
    def _keyword_route(message: str) -> str:
        if any(w in message for w in ["代码", "编程", "Python", "算法", "def ", "class ", "Debug"]):
            return "code_grader"
        if any(w in message for w in ["考题", "试卷", "题目", "试题", "选择题", "解答题"]):
            return "exam_quiz"
        if any(w in message for w in ["论文", "文献", "学术", "期刊", "课题"]):
            return "academic_rag"
        if any(w in message for w in ["计算", "推导", "积分", "导数", "公式"]):
            return "math_solver"
        if any(w in message for w in ["课标", "核心素养"]):
            return "curriculum"
        if any(w in message for w in ["批改", "作文", "评分"]):
            return "rubric"
        if any(w in message for w in ["课件", "PPT", "幻灯片"]):
            return "slide_outline"
        if any(w in message for w in ["引导", "为什么", "启发", "不懂"]):
            return "socratic"
        return "lesson_plan"


intent_gate = IntentGate()
