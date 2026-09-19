from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Any, Optional

from app.services.mcp.client import mcp_client
from app.services.skills.manager import skill_manager

router = APIRouter(prefix="/ext", tags=["MCP Tools & Skills"])


@router.get("/mcp/tools")
async def list_mcp_tools():
    """Lists registered Model Context Protocol tools."""
    tools = await mcp_client.list_tools()
    return {"mcp_tools": tools}


class MCPCallRequest(BaseModel):
    server: str
    tool_name: str
    arguments: Dict[str, Any] = {}


@router.post("/mcp/tools/call")
async def call_mcp_tool(payload: MCPCallRequest):
    """调用指定 MCP 工具并返回结构化结果。"""
    result = await mcp_client.call_tool(
        server=payload.server,
        tool_name=payload.tool_name,
        arguments=payload.arguments,
    )
    return result


@router.get("/skills")
async def list_skills():
    """Lists dynamically mounted educational skills."""
    skills = skill_manager.list_skills()
    return {"skills": skills}


class SkillRunRequest(BaseModel):
    skill_id: str
    topic: str
    subject: str = "高中数学"
    grade: str = "高二"


@router.post("/skills/run")
async def run_skill(payload: SkillRunRequest):
    """执行教学技能：输入主题，调用 LLM 生成教学材料。"""
    result = await skill_manager.run_skill(
        skill_id=payload.skill_id,
        topic=payload.topic,
        subject=payload.subject,
        grade=payload.grade,
    )
    return result
