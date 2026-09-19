from typing import List, Optional
from pydantic import BaseModel, Field


class RubricDimension(BaseModel):
    dimension: str = Field(description="评价维度名称，如：审题立意、论据论证、结构层次、语言表达、思辨深度")
    score: float = Field(description="该维度得分")
    max_score: float = Field(description="该维度满分")
    comment: str = Field(description="该维度具体评语，指出亮点与问题")


class RubricReportStructured(BaseModel):
    overall_score: float = Field(description="总分（百分制）")
    overall_comment: str = Field(description="总评：对学生作答的整体诊断结论")
    grade_level_estimate: str = Field(default="", description="预估档次，如：一类文/二类文/优秀/良好/待提高")
    dimensions: List[RubricDimension] = Field(description="分项量规评分列表（3-5 个维度）")
    deductions: List[str] = Field(description="失分点清单：明确指出失分位置并说明扣分依据")
    improvement_suggestions: List[str] = Field(description="具体可执行的改写建议列表")
    upgraded_example: str = Field(description="升格示范：针对原文关键段落的改写示范或优秀范例段落")
