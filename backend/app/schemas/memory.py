from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime


class UserProfileBase(BaseModel):
    subject: str = Field(default="高中数学", description="主教学科，如：高中数学、初中物理")
    grade: str = Field(default="高二理科", description="授课学段，如：高一、高二理科、高三冲刺")
    textbook_version: str = Field(default="人教A版", description="教材版本，如：人教A版、苏教版、统编版")
    student_analysis: str = Field(
        default="班级学生基础运算较扎实，但数形结合与转化化归思维偏弱，容易在含参分类讨论中遗漏边界条件",
        description="授课班级学情诊断与学生薄弱点"
    )
    teaching_style: str = Field(
        default="启发式探究教学法，注重几何直观引入，主副板书分区明确，梯度式作业设计",
        description="教师常驻授课风格与偏好"
    )
    auto_memory_enabled: bool = Field(default=True, description="是否在对话中开启自动记忆沉淀与反思")


class UserProfileUpdate(BaseModel):
    subject: Optional[str] = None
    grade: Optional[str] = None
    textbook_version: Optional[str] = None
    student_analysis: Optional[str] = None
    teaching_style: Optional[str] = None
    auto_memory_enabled: Optional[bool] = None


class UserProfileOut(UserProfileBase):
    user_id: str
    updated_at: str


class MemoryItemBase(BaseModel):
    category: str = Field(
        default="pedagogy",
        description="分类：pedagogy(教学法) | preference(板书/题型风格) | student_status(学情) | custom(自定义常识)"
    )
    title: str = Field(..., description="记忆简要标题/标签")
    content: str = Field(..., description="记忆详细描述")
    importance: int = Field(default=3, ge=1, le=5, description="重要程度：1~5星")
    is_active: bool = Field(default=True, description="是否激活参与大模型推理")


class MemoryItemCreate(MemoryItemBase):
    pass


class MemoryItemUpdate(BaseModel):
    category: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    importance: Optional[int] = Field(None, ge=1, le=5)
    is_active: Optional[bool] = None


class MemoryItemOut(MemoryItemBase):
    id: str
    user_id: str
    created_at: str
    updated_at: str


class MemoryReflectRequest(BaseModel):
    recent_dialogues: List[str] = Field(..., description="近期与智能体的对话文本列表")


class ExtractedMemoryItem(BaseModel):
    title: str
    category: str
    content: str
    importance: int = 3
    suggested_action: str = "add"  # add | update


class MemoryReflectResponse(BaseModel):
    reflected_items: List[ExtractedMemoryItem]
    summary: str
