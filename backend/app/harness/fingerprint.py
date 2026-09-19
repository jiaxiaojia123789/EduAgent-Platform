"""
Agent Harness: 工具调用指纹去重

在一次任务执行（workflow run）生命周期内，对所有工具调用建立
"工具名 + 规范化参数" 的 SHA-256 指纹；命中指纹时直接返回
缓存结果并短路，不再真实执行，用于拦截 Agent 反复调用同一
工具（等价参数）导致的死循环与成本浪费。

作用域：
- 以 session_id 为 key 的任务级 Guard（同一任务内多个 sub-agent 共享）
- 每次 harness.before_run（新任务/新一轮对话）自动重置
- harness.after_run 后销毁

注意：Map-Reduce 中"有意重复"的调用（如检索确定性结果）被去重是
安全的——检索/沙箱结果是确定性的，投票多样性来自 LLM 采样而非工具。
"""
import copy
import json
import hashlib
import logging
from typing import Any, Callable, Awaitable, Dict, Optional

logger = logging.getLogger(__name__)


def _canonicalize(value: Any) -> Any:
    """递归规范化参数：字符串去首尾空白，dict 按 key 排序，列表逐元素处理。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return {k: _canonicalize(value[k]) for k in sorted(value.keys())}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    return value


def make_fingerprint(tool_name: str, arguments: Dict[str, Any]) -> str:
    """生成工具调用指纹：sha256(tool_name + 规范化参数 JSON)。"""
    canonical = _canonicalize(arguments or {})
    raw = f"{tool_name}|" + json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CallFingerprintGuard:
    """任务级工具调用指纹守卫。"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        # fingerprint -> 缓存的工具执行结果
        self._cache: Dict[str, Any] = {}
        # fingerprint -> 工具可读名（日志/排障）
        self._names: Dict[str, str] = {}
        self.hit_count = 0
        # 最近一次调用是否命中去重（供调用方按需读取，结果本身保持原样不被包装）
        self.last_hit = False

    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        executor: Callable[[], Awaitable[Any]],
    ) -> Any:
        """
        带指纹去重执行工具：
        - 命中：直接返回缓存结果的深拷贝并短路，不真实执行
        - 未命中：真实执行 executor（成功与失败结果都会缓存），
          同一任务内重试等价失败时直接回喂"已尝试过"的结果
        去重标记通过 self.last_hit 暴露，不修改、不包装结果本体。
        """
        fp = make_fingerprint(tool_name, arguments)

        if fp in self._cache:
            self.hit_count += 1
            self.last_hit = True
            logger.info(
                f"[FingerprintGuard] 命中去重，短路工具 '{tool_name}' "
                f"(session={self.session_id}, 累计命中={self.hit_count})"
            )
            return copy.deepcopy(self._cache[fp])

        self.last_hit = False
        result = await executor()
        self._cache[fp] = result
        self._names[fp] = tool_name
        return result

    def stats(self) -> Dict[str, Any]:
        return {
            "distinct_calls": len(self._cache),
            "dedup_hits": self.hit_count,
        }


# ---------------------------------------------------------------------------
# Session 级 Guard 注册表（sub-agent 通过 state["session_id"] 获取共享 Guard）
# ---------------------------------------------------------------------------

_GUARDS: Dict[str, CallFingerprintGuard] = {}


def get_fingerprint_guard(session_id: str) -> CallFingerprintGuard:
    """获取（不存在则创建）某会话的指纹守卫，同一任务内共享。"""
    if session_id not in _GUARDS:
        _GUARDS[session_id] = CallFingerprintGuard(session_id)
    return _GUARDS[session_id]


def reset_fingerprint_guard(session_id: str) -> CallFingerprintGuard:
    """新任务开始时重置守卫，丢弃上一轮的全部指纹。"""
    guard = CallFingerprintGuard(session_id)
    _GUARDS[session_id] = guard
    return guard


def dispose_fingerprint_guard(session_id: str) -> None:
    """任务结束后销毁守卫，释放内存。"""
    _GUARDS.pop(session_id, None)
