"""
统一错误响应 Schema
==================
给所有失败响应一个稳定的结构，便于前端按 error_code 做错误处理与国际化。

契约：
    {
        "error": {
            "code": "AUTH_INVALID_CREDENTIALS",
            "message": "用户名或密码错误",
            "details": {...}  # 可选，字段级错误
        },
        "request_id": "8c1f..."
    }

在端点里：
    raise HTTPException(
        status_code=401,
        detail={"error_code": "AUTH_INVALID_CREDENTIALS", "message": "..."}
    )

FastAPI 默认会把 detail 包成 {"detail": ...}，前端拿到的就是稳定结构。
"""
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(description="机器可读的错误码，如 AUTH_INVALID_CREDENTIALS")
    message: str = Field(description="人可读的错误描述（中文）")
    details: Optional[Dict[str, Any]] = Field(
        default=None,
        description="可选的字段级错误或上下文，如表单校验失败的字段列表",
    )


class ErrorResponse(BaseModel):
    """所有 4xx/5xx 响应的标准外层结构。"""
    detail: ErrorDetail = Field(description="错误详情")
    request_id: Optional[str] = Field(
        default=None,
        description="请求追踪 ID，前端可报给运维，便于日志串联",
    )


# 错误码常量（前端按 code 走国际化分支）
class ErrorCode:
    # 认证类
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
    AUTH_USER_NOT_FOUND = "AUTH_USER_NOT_FOUND"

    # 资源类
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"

    # 智能体类
    AGENT_WORKFLOW_FAILED = "AGENT_WORKFLOW_FAILED"
    AGENT_TASK_NOT_FOUND = "AGENT_TASK_NOT_FOUND"
    AGENT_HITL_RESUME_FAILED = "AGENT_HITL_RESUME_FAILED"
    AGENT_LLM_QUOTA_EXHAUSTED = "AGENT_LLM_QUOTA_EXHAUSTED"
    AGENT_LLM_CALL_FAILED = "AGENT_LLM_CALL_FAILED"

    # 校验类
    VALIDATION_FAILED = "VALIDATION_FAILED"

    # 系统类
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
