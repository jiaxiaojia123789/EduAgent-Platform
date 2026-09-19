import logging
from typing import List, Dict, Any

from app.services.llm.bailian_client import bailian_client

logger = logging.getLogger(__name__)


class SkillManager:
    """
    Skills Engine: 教学工作流技能库
    把高频教学工作流封装为"输入主题 → 一键产出"的技能，
    执行时通过系统提示词模板调用 Qwen 大模型生成内容。

    分类（教学环节）：备课设计 / 命题测评 / 批改分析 / 教研沟通 / 效率工具
    """

    def __init__(self):
        self._skills: List[Dict[str, Any]] = list(_SKILL_REGISTRY.values())

    def list_skills(self) -> List[Dict[str, Any]]:
        return self._skills

    def get_skill(self, skill_id: str) -> Dict[str, Any]:
        for s in self._skills:
            if s["id"] == skill_id:
                return s
        return {}

    async def run_skill(
        self,
        skill_id: str,
        topic: str,
        subject: str = "高中数学",
        grade: str = "高二",
    ) -> Dict[str, Any]:
        """执行技能：按提示词模板调用 LLM 生成教学材料。"""
        skill = self.get_skill(skill_id)
        if not skill:
            return {"status": "error", "message": f"未知技能: {skill_id}"}

        system_prompt = skill["system_prompt"]
        user_prompt = (
            f"学科：{subject}\n学段：{grade}\n主题/素材：{topic}\n\n"
            f"请严格按要求产出内容，使用规范的 Markdown 排版，数学公式用 LaTeX。"
        )

        try:
            resp = await bailian_client.acomplete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
                max_tokens=4096,
            )
            return {
                "status": "success",
                "skill_id": skill_id,
                "skill_name": skill["name"],
                "topic": topic,
                "content": resp["content"],
                "model": resp.get("model", ""),
            }
        except Exception as e:
            logger.error(f"[SkillManager] run '{skill_id}' failed: {e}")
            return {"status": "error", "message": str(e)}


def _s(
    skill_id: str,
    name: str,
    category: str,
    icon: str,
    description: str,
    input_label: str,
    system_prompt: str,
) -> Dict[str, Any]:
    return {
        "id": skill_id,
        "name": name,
        "category": category,
        "icon": icon,
        "description": description,
        "input_label": input_label,
        "system_prompt": system_prompt,
        "enabled": True,
    }


_SKILL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ---------------- 备课设计 ----------------
    "unit_design": _s(
        "unit_design", "单元整体教学设计", "备课设计", "Blocks",
        "基于大概念生成单元目标、任务群、课时安排与评价设计",
        "单元主题，如：函数的导数",
        "你是资深教研员。请生成一份核心素养导向的单元整体教学设计，包含：1) 单元大概念与素养目标；2) 单元学习任务群；3) 课时划分与每课时要点；4) 单元评价方案。",
    ),
    "lesson_plan": _s(
        "lesson_plan", "课时教案生成", "备课设计", "BookOpenText",
        "生成含教学目标、重难点、流程、板书与作业的完整教案",
        "课时主题，如：导数的几何意义",
        "你是特级教师。请生成一份完整课时教案，包含：教学目标（三维/素养）、教学重难点、教学准备、教学过程（导入-新授-例题-练习-小结，标注师生活动与设计意图）、板书设计、分层作业。",
    ),
    "board_design": _s(
        "board_design", "板书设计", "备课设计", "Presentation",
        "生成结构化、图文配合的课堂板书布局方案",
        "板书主题，如：切线方程",
        "你是板书设计专家。请用文字排版示意的方式生成一份课堂板书设计，区分主板书与副板书区域，体现知识结构与逻辑关系，可配合简笔示意。",
    ),
    "homework_design": _s(
        "homework_design", "分层作业设计", "备课设计", "ListChecks",
        "按基础/提升/拓展三层设计作业并附设计说明",
        "作业知识点，如：导数计算",
        "你是作业设计专家。请设计分层作业：基础巩固层（3-4题）、能力提升层（2题）、拓展探究层（1题），每题附考查目标与参考答案要点，并说明分层依据。",
    ),
    "slide_outline": _s(
        "slide_outline", "课件大纲生成", "备课设计", "MonitorPlay",
        "将教学内容提炼为逐页 PPT 大纲与讲稿要点",
        "课件主题，如：导数的几何意义",
        "你是课件设计专家。请生成逐页课件大纲，每页包含：页码、标题、页面要点、建议配图/动画、讲稿提示，共 8-12 页。",
    ),
    # ---------------- 命题测评 ----------------
    "exam_make": _s(
        "exam_make", "试题命制", "命题测评", "FileQuestion",
        "按题型与难度命制试题，附答案与详细解析",
        "命题要求，如：2道导数中档解答题",
        "你是命题专家。请按要求命制试题，确保科学性与规范性，每题附标准答案、详细解题步骤、采分点与难度预估。",
    ),
    "spec_table": _s(
        "spec_table", "双向细目表", "命题测评", "Table",
        "生成知识内容×能力层级的试卷双向细目表",
        "考试范围，如：导数及其应用单元测验",
        "你是测量评价专家。请生成一份试卷双向细目表：行为知识内容维度 × 了解/理解/掌握/应用能力层级，标注题号、题型、分值与预估难度，并用 Markdown 表格呈现。",
    ),
    "rubric_eval": _s(
        "rubric_eval", "Rubric 量规诊断", "命题测评", "Gauge",
        "分步采分点映射与扣分项溯源分析",
        "题目与学生作答内容",
        "你是高考阅卷专家。请针对题目建立分步采分量规（等级/维度/描述/分值），并对学生作答逐项诊断，标注得分、扣分点与改进建议。",
    ),
    # ---------------- 批改分析 ----------------
    "homework_grade": _s(
        "homework_grade", "作业智能批改", "批改分析", "PenLine",
        "逐题批改并标注典型错误与订正指导",
        "学生作业内容",
        "你是批改教师。请逐题批改作业，标注对错、给出正确解答、归纳典型错误类型与原因，并提供针对性订正练习。",
    ),
    "class_analysis": _s(
        "class_analysis", "学情分析报告", "批改分析", "BarChart3",
        "基于成绩数据生成班级学情与教学改进建议",
        "成绩数据或测验情况描述",
        "你是学情分析专家。请基于数据生成分析报告：整体水平（均分/及格率/优秀率）、得分分布、薄弱知识点归因、典型错因、分层教学改进建议。",
    ),
    # ---------------- 教研沟通 ----------------
    "lecture_notes": _s(
        "lecture_notes", "听课记录分析", "教研沟通", "ClipboardList",
        "整理课堂实录并给出教学环节与亮点建议分析",
        "听课记录/课堂实录文本",
        "你是教研组长。请将听课记录整理为结构化听评课报告：教学流程时间线、环节评价、课堂亮点、存在问题与改进建议。",
    ),
    "reflection": _s(
        "reflection", "教学反思", "教研沟通", "Lightbulb",
        "围绕目标达成与改进生成课后教学反思",
        "授课主题与课堂情况描述",
        "你是反思型教师。请撰写一篇课后教学反思：教学目标达成度、成功之处、不足与原因分析、具体改进措施，真实深刻、避免空话。",
    ),
    "parent_speech": _s(
        "parent_speech", "家长会发言稿", "教研沟通", "Users",
        "生成含学情、建议与家校配合的家长会发言",
        "班级与学科情况描述",
        "你是班主任兼任课教师。请撰写家长会发言稿：欢迎致辞、班级学情通报、学科学习要求、给家长的具体建议、家校配合事项，语气真诚亲切。",
    ),
    # ---------------- 效率工具 ----------------
    "latex_guard": _s(
        "latex_guard", "LaTeX 公式保护校验", "效率工具", "Sigma",
        "校验并修复理科公式定界符与特殊符号",
        "含公式的文本内容",
        "你是公式排版专家。请检查并修复文本中的 LaTeX 数学公式：定界符配对、上下标、分式、根号、希腊字母等，输出修复后的完整内容并说明修改点。",
    ),
    "doc_export": _s(
        "doc_export", "Word/PDF 排版导出", "效率工具", "FileDown",
        "按教育部公文规范排版，可导出 Word 与 PDF",
        "需要排版导出的内容主题",
        "你是公文排版专家。请按教育部教学文档规范生成结构完整、层级清晰、表格公式规范的内容（标题/正文/表格），以便导出 Word 与 PDF。",
    ),
}


skill_manager = SkillManager()
