import logging
from typing import Dict, Any, Optional, Callable
from app.harness.sandbox import ExecutionSandbox, SandboxViolationError
from app.harness.guardrails import SafetyGuardrails, GuardrailViolationError
from app.harness.authorizer import ToolAuthorizer, ToolAuthorizationError
from app.harness.telemetry import TelemetryRecorder
from app.harness.fingerprint import reset_fingerprint_guard, dispose_fingerprint_guard
from app.core.config import settings

logger = logging.getLogger(__name__)


class AgentHarness:
    """
    Agent Harness: 智能体运行驾驭底座
    Unifies:
    1. ExecutionSandbox (Token budget, step limit, timeout breaker)
    2. SafetyGuardrails (Input anti-injection, PII sanitization, output pedagogical check)
    3. ToolAuthorizer & HITL (RBAC tool gates, high-risk approval interception)
    4. TelemetryRecorder (Doubao UI thinking accordion step traces & Langfuse export)
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        user_role: str = "teacher",
        max_steps: Optional[int] = None,
        token_budget: Optional[int] = None,
        timeout_seconds: Optional[int] = None
    ):
        self.session_id = session_id
        self.user_role = user_role

        # Initialize Harness components
        self.sandbox = ExecutionSandbox(
            max_steps=max_steps or settings.AGENT_MAX_STEPS,
            token_budget=token_budget or settings.AGENT_MAX_TOKEN_BUDGET,
            timeout_seconds=timeout_seconds or settings.AGENT_TIMEOUT_SECONDS
        )
        self.guardrails = SafetyGuardrails()
        self.authorizer = ToolAuthorizer()
        self.telemetry = TelemetryRecorder(session_id=session_id)

    def before_run(self, prompt: str) -> str:
        """Lifecycle Hook 1: Executed before Agent invocation."""
        self.sandbox.start()
        # 新任务：重置工具调用指纹守卫（同一 session 多轮对话互不污染）
        if self.session_id:
            reset_fingerprint_guard(self.session_id)
        step_id = self.telemetry.start_step(
            node_name="Harness_Guardrails",
            action_type="THINKING",
            title="执行前置安全审计与防注入检测"
        )

        # 1. Check prompt injection and educational compliance
        is_safe, reason = self.guardrails.check_input_safety(prompt)
        if not is_safe:
            self.telemetry.finish_step(step_id, detail=f"安全护栏拦截: {reason}")
            raise GuardrailViolationError(reason)

        # 2. Sanitize PII
        sanitized_prompt = self.guardrails.sanitize_pii(prompt)
        self.telemetry.finish_step(step_id, detail="前置安全检查通过，敏感信息已脱敏")
        return sanitized_prompt

    def on_step(self, node_name: str, action_type: str, title: str, tokens: int = 0) -> str:
        """Lifecycle Hook 2: Executed on each reasoning step in the Agent Graph."""
        self.sandbox.record_step(step_name=node_name, tokens=tokens)
        step_id = self.telemetry.start_step(
            node_name=node_name,
            action_type=action_type,
            title=title
        )
        return step_id

    def finish_step(self, step_id: str, detail: Optional[str] = None, data: Optional[Dict[str, Any]] = None):
        """Lifecycle Hook 2b: Finish current step and record latency."""
        self.telemetry.finish_step(step_id, detail=detail, data=data)

    def before_tool_call(self, tool_name: str, tool_args: Dict[str, Any]) -> bool:
        """
        Lifecycle Hook 3: Tool Authorization & HITL Interception.
        Returns:
            True if authorized and ready to execute.
            Raises ToolAuthorizationError or triggers HITL if unauthorized.
        """
        is_allowed, requires_hitl, msg = self.authorizer.authorize(tool_name, self.user_role)
        if not is_allowed:
            raise ToolAuthorizationError(msg)

        if requires_hitl and not settings.HITL_AUTO_APPROVE:
            # Requires human-in-the-loop approval
            step_id = self.telemetry.start_step(
                node_name="Harness_HITL",
                action_type="HITL_GATE",
                title=f"高风险工具 '{tool_name}' 挂起等待教师审批确认"
            )
            self.telemetry.finish_step(step_id, detail=f"参数: {tool_args}")
            return False  # Signals that flow is suspended for HITL

        return True

    def after_run(self, output: str) -> str:
        """Lifecycle Hook 4: Post-execution output validation."""
        step_id = self.telemetry.start_step(
            node_name="Harness_PostGuardrail",
            action_type="THINKING",
            title="执行最终产物合规审查与格式校验"
        )
        is_valid, msg = self.guardrails.validate_output_compliance(output)
        if not is_valid:
            self.telemetry.finish_step(step_id, detail=f"后置合规拦截: {msg}")
            raise GuardrailViolationError(msg)

        self.telemetry.finish_step(step_id, detail="后置合规审查通过")
        # 任务结束：销毁指纹守卫，释放内存
        if self.session_id:
            dispose_fingerprint_guard(self.session_id)
        return output

    def get_trace_summary(self) -> Dict[str, Any]:
        """Returns collected execution traces and sandbox metrics."""
        summary = self.telemetry.export_summary()
        summary["sandbox_metrics"] = self.sandbox.get_metrics()
        return summary
