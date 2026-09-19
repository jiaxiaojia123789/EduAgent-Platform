import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    from langfuse import Langfuse
    _HAS_LANGFUSE = True
except ImportError:
    Langfuse = None
    _HAS_LANGFUSE = False


def _get_langfuse_client() -> Optional["Langfuse"]:
    """单例获取 Langfuse 客户端，未配置时返回 None"""
    if not _HAS_LANGFUSE:
        return None
    if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
        return None
    if "your-" in (settings.LANGFUSE_PUBLIC_KEY or ""):
        return None
    try:
        return Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
    except Exception as e:
        logger.warning(f"[Telemetry] Langfuse 客户端初始化失败: {e}")
        return None


# 全局单例
_langfuse_client: Optional["Langfuse"] = None


def get_langfuse() -> Optional["Langfuse"]:
    global _langfuse_client
    if _langfuse_client is None:
        _langfuse_client = _get_langfuse_client()
    return _langfuse_client


class TelemetryRecorder:
    """
    Agent Harness: Observability & Telemetry Recorder
    Emits standardized trace events for front-end rendering (Doubao-style thinking process)
    and pushes to Langfuse if configured.

    重构说明：
    - 新增 Langfuse 集成：每个 step 同步推送到 Langfuse trace
    - 推送策略：trace_id 作为 Langfuse trace 主键，每个 step 对应一个 span
    - 失败容忍：Langfuse 推送失败不影响业务流程
    """

    def __init__(self, trace_id: Optional[str] = None, session_id: Optional[str] = None):
        self.trace_id = trace_id or str(uuid.uuid4())
        self.session_id = session_id or str(uuid.uuid4())
        self.steps: List[Dict[str, Any]] = []
        self._current_step_start: Optional[float] = None
        # Langfuse 上下文
        self._langfuse = get_langfuse()
        self._lf_trace = None
        self._lf_spans: Dict[str, Any] = {}
        if self._langfuse:
            try:
                self._lf_trace = self._langfuse.trace(
                    id=self.trace_id,
                    name=f"agent_run:{self.session_id}",
                    session_id=self.session_id,
                )
            except Exception as e:
                logger.warning(f"[Telemetry] Langfuse trace 创建失败: {e}")
                self._lf_trace = None

    def start_step(self, node_name: str, action_type: str, title: str) -> str:
        step_id = str(uuid.uuid4())[:8]
        self._current_step_start = time.time()
        step_data = {
            "step_id": step_id,
            "node_name": node_name,
            "action_type": action_type,  # THINKING, TOOL_CALL, TOOL_RESULT, GENERATION, HITL_GATE
            "title": title,
            "detail": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": 0,
            "data": {}
        }
        self.steps.append(step_data)

        # 推送 Langfuse span
        if self._lf_trace:
            try:
                span = self._lf_trace.span(
                    name=f"{node_name}:{action_type}",
                    metadata={"title": title, "action_type": action_type},
                )
                self._lf_spans[step_id] = span
            except Exception as e:
                logger.debug(f"[Telemetry] Langfuse span 创建失败: {e}")
        return step_id

    def finish_step(self, step_id: str, detail: Optional[str] = None, data: Optional[Dict[str, Any]] = None):
        for step in self.steps:
            if step["step_id"] == step_id:
                if self._current_step_start:
                    step["elapsed_ms"] = int((time.time() - self._current_step_start) * 1000)
                if detail:
                    step["detail"] = detail
                if data:
                    step["data"] = data
                break

        # 推送 Langfuse span end + event
        span = self._lf_spans.pop(step_id, None)
        if span:
            try:
                if detail:
                    span.event(name="step_detail", payload={"detail": detail})
                if data:
                    span.event(name="step_data", payload=data)
                span.end()
            except Exception as e:
                logger.debug(f"[Telemetry] Langfuse span 结束失败: {e}")

    def get_steps(self) -> List[Dict[str, Any]]:
        return self.steps

    def export_summary(self) -> Dict[str, Any]:
        total_time_ms = sum(s.get("elapsed_ms", 0) for s in self.steps)
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "total_steps": len(self.steps),
            "total_latency_ms": total_time_ms,
            "steps": self.steps
        }

    def flush(self):
        """显式 flush 到 Langfuse（建议在 task 完成时调用）"""
        if self._langfuse:
            try:
                self._langfuse.flush()
            except Exception as e:
                logger.debug(f"[Telemetry] Langfuse flush 失败: {e}")
