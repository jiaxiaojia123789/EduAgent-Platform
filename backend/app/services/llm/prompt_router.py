"""
动态 Prompt 路由器 (Dynamic Prompt Routing)
============================================
与 ModelRouter（services/llm/router.py）正交协同：
- ModelRouter 决定「由哪个模型执行」（qwen-max / plus / turbo，按任务档位与上下文长度）
- PromptRouter 决定「系统提示里注入什么适配信息」（按学段 / 学科 / 学情画像）

三个路由维度（信息按优先级合并，请求内显式声明 > 用户画像默认值）：
1. 学段路由：从请求文本检测学段关键词（小学/初中/高中/高三/考研...）
2. 学科路由：从请求文本检测学科关键词（数学/物理/语文...）
3. 学情路由：memory_service 的用户画像（subject / grade / textbook_version /
   student_analysis / teaching_style），实现「因材施教」的动态 Prompt 注入

失败降级：画像读取异常时静默跳过，路由结果退化为纯模板，不阻塞主流程。
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.prompts.registry import prompt_registry

logger = logging.getLogger(__name__)


class PromptRouter:
    """教育场景动态 Prompt 路由器（单例，见模块级 prompt_router）"""

    # ------------------------------------------------------------------
    # 关键词路由表：按命中优先级排列（先长词后短词，避免"高中数学"被"数学"截胡）
    # ------------------------------------------------------------------
    _SUBJECT_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
        ("高中数学", ("高中数学",)),
        ("初中数学", ("初中数学",)),
        ("小学数学", ("小学数学",)),
        ("数学", ("数学", "导数", "函数", "几何", "概率", "微积分", "向量", "数列", "方程")),
        ("物理", ("物理", "力学", "电磁", "光学", "热学", "动量")),
        ("化学", ("化学", "有机化学", "无机化学", "摩尔", "化学方程式")),
        ("生物", ("生物", "细胞", "遗传", "生态系统")),
        ("语文", ("语文", "作文", "阅读理解", "古诗文", "文言文", "议论文")),
        ("英语", ("英语", "语法", "阅读填空", "书面表达")),
        ("历史", ("历史", "朝代", "近代史", "世界史")),
        ("地理", ("地理", "气候", "地形", "人文地理")),
        ("政治", ("政治", "思政", "哲学与人生", "经济与社会")),
        ("信息技术", ("信息科技", "信息技术", "编程", "算法", "Python", "代码")),
    )
    _GRADE_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
        ("小学", ("小学", "一年级", "二年级", "三年级", "四年级", "五年级", "六年级")),
        ("初中", ("初中", "七年级", "八年级", "九年级", "中考")),
        ("高中", ("高中", "高一", "高二", "高三", "高考", "选择性必修", "必修")),
        ("大学/考研", ("大学", "考研", "高数", "高等数学", "线性代数", "概率论")),
    )

    # ------------------------------------------------------------------
    @classmethod
    def detect_subject(cls, text: str) -> Optional[str]:
        for subject, keywords in cls._SUBJECT_RULES:
            if any(kw in text for kw in keywords):
                return subject
        return None

    @classmethod
    def detect_grade(cls, text: str) -> Optional[str]:
        for grade, keywords in cls._GRADE_KEYWORDS:
            if any(kw in text for kw in keywords):
                return grade
        return None

    # ------------------------------------------------------------------
    @classmethod
    def resolve_adaptation(cls, user_prompt: str = "", user_id: Optional[str] = None) -> str:
        """
        计算本次请求的适配提示文本（用于追加进系统提示）。
        请求内显式声明的学段/学科优先于用户画像默认值；无任何信号时返回空串。
        """
        lines: List[str] = []
        p_subject = cls.detect_subject(user_prompt or "")
        p_grade = cls.detect_grade(user_prompt or "")

        profile: Dict[str, Any] = {}
        if user_id:
            try:
                from app.services.memory.memory_service import memory_service
                profile = memory_service.get_user_profile(user_id) or {}
            except Exception as e:
                logger.warning(f"[PromptRouter] 读取用户画像失败，跳过学情适配: {e}")

        subject = p_subject or profile.get("subject") or ""
        grade = p_grade or profile.get("grade") or ""
        if subject and grade:
            lines.append(f"- 目标对象：{grade} · {subject}")
        elif subject or grade:
            lines.append(f"- 目标对象：{grade}{subject}")

        textbook = profile.get("textbook_version") or ""
        if textbook:
            lines.append(f"- 教材版本：{textbook}")

        analysis = profile.get("student_analysis") or ""
        if analysis:
            lines.append(f"- 学情要点：{analysis}")

        style = profile.get("teaching_style") or ""
        if style:
            lines.append(f"- 教学风格要求：{style}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    @classmethod
    def build_system(
        cls,
        prompt_id: str,
        user_prompt: str = "",
        user_id: Optional[str] = None,
        extra: str = "",
    ) -> str:
        """组装动态系统提示：角色 + 任务模板 + 学段/学科/学情适配"""
        base = prompt_registry.compose_system(prompt_id, extra=extra)
        adaptation = cls.resolve_adaptation(user_prompt, user_id)
        if adaptation:
            base = f"{base}\n\n【学段/学科/学情适配提示】\n{adaptation}"
        return base


prompt_router = PromptRouter()
