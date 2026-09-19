from typing import List, Optional
from pydantic import BaseModel, Field


class CoreCompetency(BaseModel):
    name: str = Field(description="核心素养维度，如：数学抽象、逻辑推理、数学建模、直观想象、数学运算、数据分析")
    description: str = Field(description="在本节课中的具体落实要求与培养目标")


class TeachingStep(BaseModel):
    step_number: int = Field(description="教学环节序号，例如 1")
    title: str = Field(description="环节名称，如：情境创设、概念形成、例题精讲、随堂演练、课堂小结")
    duration_minutes: int = Field(description="预估耗时 (分钟)")
    teacher_activity: str = Field(description="教师引导与讲授活动，包含引导性问题")
    student_activity: str = Field(description="学生活动与自主探究、合作讨论内容")
    design_intent: str = Field(description="设计意图与新课标支撑点")


class BlackboardDesign(BaseModel):
    layout_type: str = Field(default="三栏式", description="板书布局结构，如：主副板书设计、脉络思维导图式")
    main_board: List[str] = Field(description="正板书：核心公式、定理推导、关键结论 (支持 LaTeX)")
    auxiliary_board: List[str] = Field(description="副板书：草稿演练、学生板演、例题辅助示意")


class LessonPlanStructured(BaseModel):
    title: str = Field(description="课题名称，例如：《导数的几何意义》")
    subject: str = Field(description="学科，例如：高中数学")
    grade_level: str = Field(description="适用年级/学段，例如：高二第一学期")
    textbook_version: str = Field(default="人教A版", description="教材版本")
    class_duration: int = Field(default=45, description="授课课时时长 (分钟)")
    
    # 教学指导思想与学情
    learning_analysis: str = Field(description="学情分析：学生已有知识基础、可能遇到的思维障碍与认知特点")
    core_competencies: List[CoreCompetency] = Field(description="新课标学科核心素养对标目标")
    
    # 教学目标与重难点
    teaching_objectives: List[str] = Field(description="三维/综合教学目标")
    teaching_key_points: List[str] = Field(description="教学重点")
    teaching_difficult_points: List[str] = Field(description="教学难点及突破策略")
    
    # 教学流程
    teaching_steps: List[TeachingStep] = Field(description="详实的课堂教学过程分步设计")
    
    # 板书与作业
    blackboard_design: BlackboardDesign = Field(description="结构化板书设计方案")
    assignment: List[str] = Field(description="分层作业设计：基础巩固、能力提升、拓展探究")
    teaching_reflection_prompt: str = Field(description="课后教学反思指引要点")
