"""
Sub-Agent 抽象基类 + 动态注册表
核心职责：
1. 定义 SubAgent 统一接口：async run(task, scope, on_token) -> SubAgentResult
2. 提供 run 默认实现：复用子类的 execute_stream，包装为 SubAgentResult（兼容现有 9 个 agent）
3. SubAgentRegistry 动态注册：未来新任务通过 register("new_task", NewAgent) 挂载
4. Map-Reduce 模式支持：同一 SubAgent 实例可并行跑 N 次，由 Orchestrator 投票聚合

设计原则：
- SubAgent 完成后只返回压缩后的 SubAgentResult（不含内部 messages），保持上下文隔离
- specialized agent 继承 SubAgent 后只需设置 agent_name + 保留 execute_stream，零改动业务逻辑
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable, Awaitable, Type

from app.services.agent.context_manager import ContextScope, context_manager
from app.services.agent.state import AgentState

logger = logging.getLogger(__name__)


@dataclass
class SubAgentResult:
    """
    Sub-Agent 完成后的压缩输出
    不含内部 messages，仅保留主线程需要的产出
    """
    task_id: str
    agent_name: str
    scope_id: str
    output_markdown: str = ""
    citations: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0           # 0-1，用于 Map-Reduce 投票加权
    token_used: int = 0
    status: str = "DONE"              # DONE / FAILED
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# on_token 回调类型：async def(chunk: str) -> None
TokenCallback = Callable[[str], Awaitable[None]]


class SubAgent:
    """
    Sub-Agent 抽象基类（非强制 ABC，便于现有 agent 零改动继承）
    子类需设置 agent_name 类属性，并实现 execute 或 execute_stream 方法
    """

    agent_name: str = "base"
    default_token_budget: int = 4096
    system_prompt: str = ""

    async def run(
        self,
        task: Dict[str, Any],
        scope: ContextScope,
        on_token: Optional[TokenCallback] = None,
    ) -> SubAgentResult:
        """
        统一执行入口（默认实现）
        1. 从 scope.messages + task.input_summary 构造 AgentState
        2. 调用子类的 execute_stream
        3. 把返回 dict 包装为 SubAgentResult
        4. 更新 scope 状态与 artifacts
        """
        task_id = task.get("task_id", scope.scope_id)
        input_summary = task.get("input_summary", "")

        # 标记 scope 为 RUNNING
        await context_manager.update_status(
            scope.scope_id, scope.thread_id, status="RUNNING"
        )

        try:
            # 构造 AgentState（复用子类 execute_stream 的接口）
            # 把 input_summary 追加到 scope.messages 末尾作为 user 消息
            messages_for_state: List[Dict[str, str]] = list(scope.messages)
            if input_summary and (
                not messages_for_state
                or messages_for_state[-1].get("content") != input_summary
            ):
                messages_for_state.append({"role": "user", "content": input_summary})

            state: AgentState = {
                "messages": messages_for_state,  # type: ignore[typeddict-item]
                "user_id": scope.metadata.get("user_id", "u-001"),
                "session_id": scope.metadata.get("session_id", scope.thread_id),
                "thread_id": scope.thread_id,
                "intent": self.agent_name,
                "current_agent": self.agent_name,
                "next_agent": "quality_gate",
                "plan_steps": [],
                "current_step": 0,
                "kb_ids": scope.metadata.get("kb_ids", []),
                "retrieved_docs": [],
                "citations": [],
                "structured_artifact": None,
                "artifact_type": None,
                "final_markdown_output": "",
                "requires_approval": False,
                "is_approved": False,
                "reviewer_comments": None,
                "retry_count": 0,
                "error_message": None,
            }

            # 优先流式入口：检查子类是否自己覆盖了 execute_stream（而非用基类默认实现）
            execute_stream_method = type(self).execute_stream
            is_stream_overridden = execute_stream_method is not SubAgent.execute_stream

            agent_result: Dict[str, Any]
            if is_stream_overridden and on_token is not None:
                agent_result = await self.execute_stream(state, on_token=on_token)  # type: ignore[attr-defined]
            elif hasattr(self, "execute"):
                agent_result = await self.execute(state)  # type: ignore[attr-defined]
            else:
                raise NotImplementedError(
                    f"SubAgent {self.agent_name} 必须实现 execute_stream 或 execute"
                )

            # 把 structured_artifact 统一转为 dict（兼容 Pydantic 模型）
            raw_artifact = agent_result.get("structured_artifact")
            if raw_artifact is not None:
                if hasattr(raw_artifact, "model_dump"):
                    raw_artifact = raw_artifact.model_dump()
                elif hasattr(raw_artifact, "dict"):
                    raw_artifact = raw_artifact.dict()

            # 包装为 SubAgentResult
            result = SubAgentResult(
                task_id=task_id,
                agent_name=self.agent_name,
                scope_id=scope.scope_id,
                output_markdown=agent_result.get("final_markdown_output", ""),
                citations=agent_result.get("citations", []),
                artifacts=[raw_artifact] if raw_artifact else [],
                confidence=1.0,
                token_used=scope.token_used,
                status="DONE",
                metadata={
                    "artifact_type": agent_result.get("artifact_type"),
                    "current_agent": agent_result.get("current_agent"),
                },
            )

            # 更新 scope
            scope.artifacts = result.artifacts
            scope.status = "DONE"
            await context_manager.save_scope(scope)
            await context_manager.update_status(
                scope.scope_id, scope.thread_id, status="DONE"
            )
            return result

        except Exception as e:
            logger.error(
                f"[SubAgent] {self.agent_name} 执行失败 task={task_id}: {e}",
                exc_info=True,
            )
            await context_manager.update_status(
                scope.scope_id, scope.thread_id, status="FAILED"
            )
            return SubAgentResult(
                task_id=task_id,
                agent_name=self.agent_name,
                scope_id=scope.scope_id,
                status="FAILED",
                error_message=str(e),
            )

    async def execute_stream(
        self,
        state: AgentState,
        on_token: Optional[TokenCallback] = None,
    ) -> Dict[str, Any]:
        """
        默认实现：调用 execute（非流式）
        子类如有 execute_stream 则覆盖此方法以支持流式 token 推送
        """
        return await self.execute(state)  # type: ignore[attr-defined]


class SubAgentRegistry:
    """
    Sub-Agent 动态注册表
    未来新任务通过 SubAgentRegistry.register("new_task", NewAgent) 挂载，不硬编码
    """

    _registry: Dict[str, Type[SubAgent]] = {}
    _instances: Dict[str, SubAgent] = {}

    @classmethod
    def register(cls, name: str, agent_class: Type[SubAgent]) -> None:
        """注册一个 SubAgent 类"""
        if not issubclass(agent_class, SubAgent):
            raise TypeError(f"{agent_class.__name__} 必须继承 SubAgent")
        cls._registry[name] = agent_class
        logger.info(f"[SubAgentRegistry] 已注册 sub-agent: {name} -> {agent_class.__name__}")

    @classmethod
    def get(cls, name: str) -> Optional[SubAgent]:
        """获取 sub-agent 单例（懒加载）"""
        if name not in cls._registry:
            return None
        if name not in cls._instances:
            cls._instances[name] = cls._registry[name]()
        return cls._instances[name]

    @classmethod
    def list_agents(cls) -> List[str]:
        """列出所有已注册的 agent 名"""
        return list(cls._registry.keys())

    @classmethod
    def is_compound_request(cls, message: str) -> bool:
        """
        启发式判断是否为复合请求（需要 Orchestrator 多 sub-agent 协作）
        触发条件：包含多个动词/任务关键词，或显式包含"并"、"同时"、"以及"等连接词
        """
        compound_markers = [
            "并", "同时", "以及", "接着", "然后", "之后",
            "并且", "还需", "还要", "再", "配套",
        ]
        # 统计命中数
        hits = sum(1 for m in compound_markers if m in message)
        # 显式复合关键词：教案+试题、课件+教案等多任务模式
        task_keywords = ["教案", "试题", "课件", "批改", "推导", "课标", "论文", "PPT", "代码"]
        task_hits = sum(1 for k in task_keywords if k in message)
        # 命中 ≥2 个连接词 或 ≥2 个不同任务关键词 → 复合请求
        return hits >= 2 or task_hits >= 2


# 全局单例
sub_agent_registry = SubAgentRegistry()
