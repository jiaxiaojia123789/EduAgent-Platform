import time
from typing import Dict, Any, Optional


class SandboxViolationError(Exception):
    """Raised when an Agent exceeds safety or resource limits."""
    pass


class ExecutionSandbox:
    """
    Agent Harness: Resource & Execution Sandbox
    Monitors and enforces:
    1. Maximum reasoning steps (prevents infinite loop delegation)
    2. Token budget enforcement
    3. Wall-clock execution timeout circuit breaker
    """

    def __init__(
        self,
        max_steps: int = 15,
        token_budget: int = 16000,
        timeout_seconds: int = 120
    ):
        self.max_steps = max_steps
        self.token_budget = token_budget
        self.timeout_seconds = timeout_seconds

        self.current_steps = 0
        self.consumed_tokens = 0
        self.start_time: Optional[float] = None

    def start(self):
        self.start_time = time.time()
        self.current_steps = 0
        self.consumed_tokens = 0

    def record_step(self, step_name: str, tokens: int = 0):
        if self.start_time is None:
            self.start()

        self.current_steps += 1
        self.consumed_tokens += tokens

        # 1. Step check
        if self.current_steps > self.max_steps:
            raise SandboxViolationError(
                f"[AgentHarness Sandbox] 智能体推理步数超出安全上限 ({self.current_steps}/{self.max_steps})，触发熔断保护！"
            )

        # 2. Token budget check
        if self.consumed_tokens > self.token_budget:
            raise SandboxViolationError(
                f"[AgentHarness Sandbox] Token 开销超出预算上限 ({self.consumed_tokens}/{self.token_budget})，触发预算熔断！"
            )

        # 3. Timeout check
        elapsed = time.time() - self.start_time
        if elapsed > self.timeout_seconds:
            raise SandboxViolationError(
                f"[AgentHarness Sandbox] 任务执行超过最大耗时阈值 ({elapsed:.1f}s > {self.timeout_seconds}s)，触发超时终止！"
            )

    def get_metrics(self) -> Dict[str, Any]:
        elapsed = time.time() - self.start_time if self.start_time else 0
        return {
            "current_steps": self.current_steps,
            "max_steps": self.max_steps,
            "consumed_tokens": self.consumed_tokens,
            "token_budget": self.token_budget,
            "elapsed_seconds": round(elapsed, 2),
            "timeout_seconds": self.timeout_seconds,
        }
