from typing import List, Optional
from pydantic import BaseModel, Field


class QuestionOption(BaseModel):
    label: str = Field(description="选项标识，如 A, B, C, D")
    content: str = Field(description="选项内容，支持 LaTeX 公式")


class ExamQuestion(BaseModel):
    question_number: int = Field(description="题号")
    question_type: str = Field(description="题型：单项选择题, 多项选择题, 填空题, 解答题, 探究大题")
    score: float = Field(description="分值")
    difficulty: str = Field(description="难度等级：基础 (0.8-1.0), 中等 (0.5-0.8), 拔高 (0.2-0.5)")
    knowledge_points: List[str] = Field(description="考查的核心知识点与二级考点")
    stem: str = Field(description="题干内容，严禁公式断裂，完整包含 LaTeX 表达式")
    options: Optional[List[QuestionOption]] = Field(default=None, description="选择题选项列表")
    standard_answer: str = Field(description="参考标准答案")
    rubric_steps: List[str] = Field(default=[], description="分步采分点与评分细则 (针对解答题)")
    analysis: str = Field(description="命题意图分析与详细解题推导过程")
    common_mistakes: Optional[str] = Field(default=None, description="学生常见易错点与思维陷阱警示")


class ExamPaperStructured(BaseModel):
    paper_title: str = Field(description="试卷名称，例如：《2024届高三高考模拟测试数学卷》")
    subject: str = Field(description="学科")
    grade_level: str = Field(description="年级")
    total_score: float = Field(default=100.0, description="满分")
    duration_minutes: int = Field(default=90, description="考试时长 (分钟)")
    instructions: List[str] = Field(description="考生须知与注意事项")
    questions: List[ExamQuestion] = Field(description="试题列表")
