from enum import Enum
from typing import Dict, Any, Tuple, Optional


class ToolRiskLevel(str, Enum):
    READ_ONLY = "READ_ONLY"          # Search, WolframAlpha, LaTeX validation
    SAFE_WRITE = "SAFE_WRITE"        # Draft save, personal notes
    HIGH_RISK = "HIGH_RISK"          # Publish exam to class, bulk modification, delete doc


class ToolAuthorizationError(Exception):
    pass


class ToolAuthorizer:
    """
    Agent Harness: Tool Call Authorizer & Human-in-the-loop (HITL) Interceptor
    """

    # Tool registry with risk levels and allowed roles
    TOOL_POLICY: Dict[str, Dict[str, Any]] = {
        "milvus_hybrid_search": {
            "risk": ToolRiskLevel.READ_ONLY,
            "roles": ["admin", "teacher", "student", "researcher"]
        },
        "wolfram_alpha_compute": {
            "risk": ToolRiskLevel.READ_ONLY,
            "roles": ["admin", "teacher", "student", "researcher"]
        },
        "arxiv_paper_search": {
            "risk": ToolRiskLevel.READ_ONLY,
            "roles": ["admin", "teacher", "student", "researcher"]
        },
        "latex_syntax_validator": {
            "risk": ToolRiskLevel.READ_ONLY,
            "roles": ["admin", "teacher", "student", "researcher"]
        },
        "save_lesson_plan_draft": {
            "risk": ToolRiskLevel.SAFE_WRITE,
            "roles": ["admin", "teacher", "researcher"]
        },
        "publish_exam_paper_to_class": {
            "risk": ToolRiskLevel.HIGH_RISK,
            "roles": ["admin", "teacher"]
        },
        "delete_knowledge_document": {
            "risk": ToolRiskLevel.HIGH_RISK,
            "roles": ["admin"]
        },
        "execute_code_in_sandbox": {
            "risk": ToolRiskLevel.SAFE_WRITE,
            "roles": ["admin", "teacher", "student", "researcher"]
        }
    }

    @classmethod
    def authorize(cls, tool_name: str, user_role: str) -> Tuple[bool, bool, str]:
        """
        Returns: (is_allowed, requires_hitl_approval, message)
        """
        policy = cls.TOOL_POLICY.get(tool_name)
        if not policy:
            # Default policy for unknown/custom tools: Read-only, allow all authenticated users
            return True, False, "Custom tool authorized"

        # Check role permission
        if user_role not in policy["roles"]:
            return False, False, f"当前角色 ({user_role}) 无权调用工具 '{tool_name}'"

        # Check HITL requirement
        requires_hitl = (policy["risk"] == ToolRiskLevel.HIGH_RISK)
        return True, requires_hitl, "Authorized"
