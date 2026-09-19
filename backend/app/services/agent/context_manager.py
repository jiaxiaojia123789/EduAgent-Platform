"""
Sub-Agent 上下文隔离管理器
核心职责：
1. 为每个 sub-agent 分配独立的 ContextScope（消息 / Token 预算 / TTL 三层隔离）
2. Redis 持久化（24h TTL），防止进程重启丢失
3. sub-agent 完成后压缩为 SubAgentResult 归还主线程（不泄漏内部 messages）

设计要点：
- 主线程（Orchestrator）与 sub-agent 之间通过 scope_id 关联，但消息列表互相不可见
- token_budget 用于防止 sub-agent 失控耗尽额度：sub 默认 4k，Orchestrator 8k
- artifacts 是 sub-agent 产出的结构化数据（如教案 JSON、试题列表），供主线程引用
"""
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional

from app.core.redis_client import redis_manager

logger = logging.getLogger(__name__)


@dataclass
class ContextScope:
    """
    独立上下文作用域
    每个作用域对应一个 sub-agent 的独立执行环境
    """
    scope_id: str                                    # 作用域唯一 ID，如 "thread_x:sub:T1"
    thread_id: str                                   # 所属会话线程
    parent_scope_id: Optional[str] = None            # 父作用域（Orchestrator 的 scope_id）
    messages: List[Dict[str, str]] = field(default_factory=list)  # 独立消息列表，与主线程隔离
    token_budget: int = 4096                         # Token 预算上限（sub 默认 4k，Orchestrator 8k）
    token_used: int = 0                              # 已消耗 Token 数
    artifacts: List[Dict[str, Any]] = field(default_factory=list)  # 产出结构化数据
    ttl_seconds: int = 86400                         # TTL 24h（对齐 project_memory 硬约束）
    created_at: float = field(default_factory=time.time)
    status: str = "PENDING"                          # PENDING / RUNNING / DONE / FAILED
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据（如 agent_name、task_summary）


class ContextManager:
    """
    上下文隔离管理器
    通过 Redis hash 存储每个 scope，key 规范：thread_id:scope:{scope_id}
    """

    def _scope_key(self, thread_id: str, scope_id: str) -> str:
        return f"{thread_id}:scope:{scope_id}"

    def _scope_index_key(self, thread_id: str) -> str:
        """某 thread 下所有 scope 的索引列表（便于清理 / 审计）"""
        return f"{thread_id}:scope_index"

    async def create_scope(
        self,
        thread_id: str,
        parent_scope_id: Optional[str] = None,
        scope_id: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        token_budget: int = 4096,
        ttl_seconds: int = 86400,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ContextScope:
        """创建独立上下文作用域"""
        scope = ContextScope(
            scope_id=scope_id or f"{thread_id}:sub:{uuid.uuid4().hex[:8]}",
            thread_id=thread_id,
            parent_scope_id=parent_scope_id,
            messages=messages or [],
            token_budget=token_budget,
            ttl_seconds=ttl_seconds,
            metadata=metadata or {},
        )
        await self.save_scope(scope)
        # 加入索引
        try:
            existing = await redis_manager.get(self._scope_index_key(thread_id))
            idx_list = json.loads(existing) if existing else []
            if scope.scope_id not in idx_list:
                idx_list.append(scope.scope_id)
            await redis_manager.set(
                self._scope_index_key(thread_id),
                json.dumps(idx_list),
                ex=ttl_seconds,
            )
        except Exception as e:
            logger.warning(f"[ContextManager] 更新 scope 索引失败: {e}")
        return scope

    async def save_scope(self, scope: ContextScope) -> bool:
        """持久化 scope 到 Redis"""
        key = self._scope_key(scope.thread_id, scope.scope_id)
        mapping = {
            "scope_id": scope.scope_id,
            "thread_id": scope.thread_id,
            "parent_scope_id": scope.parent_scope_id or "",
            "messages": json.dumps(scope.messages, ensure_ascii=False),
            "token_budget": str(scope.token_budget),
            "token_used": str(scope.token_used),
            "artifacts": json.dumps(scope.artifacts, ensure_ascii=False),
            "ttl_seconds": str(scope.ttl_seconds),
            "created_at": str(scope.created_at),
            "status": scope.status,
            "metadata": json.dumps(scope.metadata, ensure_ascii=False),
        }
        ok = await redis_manager.hset(mapping=mapping, ex=scope.ttl_seconds, name=key)
        # hset 的 name 字段对应 hash name，但 RedisManager.hset 签名是 hset(name, mapping, ex)
        # 上方调用参数顺序需对齐：第一个是 name，但 hset 的签名是 hset(name, mapping, ex)
        # 这里已按 name= 形式传，ok
        return ok

    async def load_scope(self, scope_id: str, thread_id: str) -> Optional[ContextScope]:
        """从 Redis 加载 scope"""
        key = self._scope_key(thread_id, scope_id)
        data = await redis_manager.hget_all(key)
        if not data:
            return None
        try:
            return ContextScope(
                scope_id=data.get("scope_id", scope_id),
                thread_id=data.get("thread_id", thread_id),
                parent_scope_id=data.get("parent_scope_id") or None,
                messages=json.loads(data.get("messages", "[]")),
                token_budget=int(data.get("token_budget", 4096)),
                token_used=int(data.get("token_used", 0)),
                artifacts=json.loads(data.get("artifacts", "[]")),
                ttl_seconds=int(data.get("ttl_seconds", 86400)),
                created_at=float(data.get("created_at", time.time())),
                status=data.get("status", "PENDING"),
                metadata=json.loads(data.get("metadata", "{}")),
            )
        except Exception as e:
            logger.error(f"[ContextManager] 加载 scope 反序列化失败 {scope_id}: {e}")
            return None

    async def update_status(
        self, scope_id: str, thread_id: str, status: str, token_used: Optional[int] = None
    ) -> bool:
        """更新 scope 状态（RUNNING / DONE / FAILED）"""
        key = self._scope_key(thread_id, scope_id)
        mapping: Dict[str, Any] = {"status": status}
        if token_used is not None:
            mapping["token_used"] = str(token_used)
        return await redis_manager.hupdate(mapping=mapping, ex=86400, name=key)

    async def append_artifact(
        self, scope_id: str, thread_id: str, artifact: Dict[str, Any]
    ) -> bool:
        """向 scope 追加产出"""
        scope = await self.load_scope(scope_id, thread_id)
        if not scope:
            return False
        scope.artifacts.append(artifact)
        return await self.save_scope(scope)

    async def delete_scope(self, scope_id: str, thread_id: str) -> int:
        """删除 scope"""
        key = self._scope_key(thread_id, scope_id)
        return await redis_manager.delete(key)

    async def list_scopes(self, thread_id: str) -> List[str]:
        """列出某 thread 下所有 scope_id"""
        try:
            existing = await redis_manager.get(self._scope_index_key(thread_id))
            return json.loads(existing) if existing else []
        except Exception:
            return []

    # ------------------------------------------------------------------
    # 消息追加与上下文压缩
    # ------------------------------------------------------------------
    async def append_messages(
        self,
        scope_id: str,
        thread_id: str,
        new_messages: List[Dict[str, str]],
        auto_compress: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        向 scope 追加消息；auto_compress=True 时按水位自动三级压缩。
        返回压缩统计（未触发压缩时为 None）。
        """
        scope = await self.load_scope(scope_id, thread_id)
        if not scope:
            return None
        scope.messages.extend(new_messages)

        stats = None
        if auto_compress:
            from app.services.agent.context_compressor import make_compressor_for_scope
            compressor = make_compressor_for_scope(scope)
            scope.messages, stats = await compressor.maybe_compress(scope.messages)

        await self.save_scope(scope)
        return stats

    async def maybe_compress_scope(
        self, scope: ContextScope
    ) -> Optional[Dict[str, Any]]:
        """
        执行前守卫：scope 已存在历史（如恢复/多轮追加）且超过水位时压缩。
        返回压缩统计；未触发时为 None。
        """
        from app.services.agent.context_compressor import make_compressor_for_scope
        compressor = make_compressor_for_scope(scope)
        before = len(scope.messages)
        scope.messages, stats = await compressor.maybe_compress(scope.messages)
        if stats.get("levels"):
            await self.save_scope(scope)
            logger.info(
                f"[ContextManager] scope {scope.scope_id} 执行前压缩："
                f"{before}→{len(scope.messages)} 条消息"
            )
            return stats
        return None


# 全局单例
context_manager = ContextManager()
