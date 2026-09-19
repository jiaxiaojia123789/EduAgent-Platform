"""
ResourceScheduler — 资源感知的波次调度器

在 Orchestrator 的每个 DAG 波次内，对任务执行三闸准入，全过才派发：
  ① 健康闸  ：LLM/依赖熔断器是否允许调用（P1 接入；None=跳过）
  ② 并发槽  ：信号量（波次总并发 ≤3，硬约束）
  ③ 文件锁  ：read → pin 快照版本（MVCC，永不阻塞）
               write → 获取区域 X 锁，区域忙则等待，超时快速失败

写任务执行完成后由本调度器统一提交版本；提交冲突（base 落后且区域
重叠）时自动 rebase：以最新版本为新 base，只重跑该任务节点，最多
MAX_REBASE_ATTEMPTS 次。

无论成功、失败、超时，锁与并发槽都在 finally 中释放（与 P0 统一
熔断出口配合，防泄漏）。
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, Dict, List, Optional, Tuple

from app.services.agent.file_resource_manager import (
    file_resource_manager,
    FileLockHandle,
    WHOLE_REGION,
)
from app.services.agent.file_version_store import (
    file_version_store,
    FileConflictError,
)
from app.services.agent.sub_agent import SubAgentResult

logger = logging.getLogger(__name__)

# run_one 回调：async def(task, resource_ctx) -> SubAgentResult
RunOneCallback = Callable[[Dict[str, Any], "ResourceContext"], Awaitable[SubAgentResult]]
# 健康检查：async def() -> (ok: bool, reason: str)
HealthCheck = Callable[[], Awaitable[Tuple[bool, str]]]

LOCK_WAIT_TIMEOUT = 10.0   # 写锁最长等待（秒）
MAX_REBASE_ATTEMPTS = 2     # 提交冲突后最多 rebase 次数


@dataclass
class ResourceContext:
    """传给 run_one 的资源上下文（None 表示该任务不操作文件）。"""
    file_id: Optional[str]
    mode: Optional[str]                 # "read" / "write" / None
    base_version: Optional[int]
    region: Tuple[int, int]
    lock_handle: Optional[FileLockHandle] = None


def parse_task_resource(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    解析任务声明的文件资源：
    task["resources"] = {"file_id": "...", "mode": "read|write",
                         "section_range": [s, e] | null}
    无 resources / file_id → None（任务不操作文件，只走并发槽）。
    """
    res = task.get("resources")
    if not isinstance(res, dict) or not res.get("file_id"):
        return None
    mode = res.get("mode", "read")
    if mode not in ("read", "write"):
        mode = "read"
    rng = res.get("section_range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        region = (int(rng[0]), int(rng[1]))
    else:
        region = WHOLE_REGION
    return {"file_id": res["file_id"], "mode": mode, "region": region}


class ResourceScheduler:
    """资源感知波次调度器。"""

    def __init__(
        self,
        semaphore: asyncio.Semaphore,
        health_check: Optional[HealthCheck] = None,
        lock_wait_timeout: float = LOCK_WAIT_TIMEOUT,
        max_rebase: int = MAX_REBASE_ATTEMPTS,
    ):
        self._semaphore = semaphore
        self._health_check = health_check
        self._lock_wait_timeout = lock_wait_timeout
        self._max_rebase = max_rebase

    async def execute_wave(
        self,
        tasks: List[Dict[str, Any]],
        run_one: RunOneCallback,
        owner_id: str = "orchestrator",
    ) -> List[SubAgentResult]:
        """执行一个波次：任务相互独立，锁等待不阻塞兄弟任务。"""
        if not tasks:
            return []
        coros = [self._run_with_gates(task, run_one, owner_id) for task in tasks]
        return await asyncio.gather(*coros)

    # ------------------------------------------------------------------
    async def _run_with_gates(
        self,
        task: Dict[str, Any],
        run_one: RunOneCallback,
        owner_id: str,
    ) -> SubAgentResult:
        task_id = task.get("task_id", "?")
        agent_name = task.get("agent", "unknown")
        resource = parse_task_resource(task)

        # ① 健康闸：快速失败，不占任何资源
        if self._health_check is not None:
            try:
                ok, reason = await self._health_check()
            except Exception as e:
                ok, reason = False, f"健康检查异常: {e}"
            if not ok:
                logger.info(f"[ResourceScheduler] {task_id} 健康闸拦截: {reason}")
                return self._fail(task_id, agent_name, f"依赖熔断中: {reason}")

        # ② 并发槽
        async with self._semaphore:
            # ③ 文件锁 + 版本 pin
            ctx = ResourceContext(
                file_id=None, mode=None, base_version=None,
                region=WHOLE_REGION,
            )
            if resource is not None:
                try:
                    ctx = await self._admit_locks(resource, owner_id)
                except _LockBusyError as e:
                    return self._fail(task_id, agent_name, str(e))

            try:
                # 执行；写任务含提交/rebase 闭环
                return await self._execute_and_commit(task, ctx, run_one)
            finally:
                if ctx.lock_handle is not None:
                    await file_resource_manager.release(ctx.lock_handle)

    async def _admit_locks(self, resource: Dict[str, Any], owner_id: str) -> ResourceContext:
        file_id, mode, region = resource["file_id"], resource["mode"], resource["region"]

        if mode == "read":
            # MVCC：先 pin 最新版本号，再注册读锁（顺序保证不遗漏新版本）
            base_version = await asyncio.to_thread(file_version_store.latest_version, file_id)
            handle = await file_resource_manager.acquire_read(file_id, owner_id, region)
            if base_version is None:
                raise _LockBusyError(f"文件 '{file_id}' 尚未入库，无可读版本")
            return ResourceContext(file_id, "read", base_version, region, handle)

        # write：区域 X 锁，忙则等待至超时
        handle = await file_resource_manager.acquire_write(
            file_id, owner_id, region, wait_timeout=self._lock_wait_timeout
        )
        if handle is None:
            raise _LockBusyError(
                f"文件 '{file_id}' 区域 {region if region != WHOLE_REGION else 'WHOLE'} "
                f"被占用，等待 {self._lock_wait_timeout}s 超时"
            )
        base_version = await asyncio.to_thread(file_version_store.latest_version, file_id)
        return ResourceContext(file_id, "write", base_version or 0, region, handle)

    # ------------------------------------------------------------------
    async def _execute_and_commit(
        self,
        task: Dict[str, Any],
        ctx: ResourceContext,
        run_one: RunOneCallback,
    ) -> SubAgentResult:
        """执行任务；写任务在成功后提交版本，冲突则 rebase 重跑。"""
        result = await run_one(task, ctx)

        if ctx.mode != "write":
            return result
        if result.status != "DONE":
            return result  # 执行失败不提交

        content = getattr(result, "written_file_content", None)
        if content is None:
            # 子 Agent 未产出文件内容，无需版本提交
            return result

        base = ctx.base_version or 0
        for attempt in range(self._max_rebase + 1):
            try:
                new_version = await asyncio.to_thread(
                    file_version_store.commit,
                    ctx.file_id, content,
                    result.agent_name, base, ctx.region,
                    f"任务 {task.get('task_id')} 提交",
                )
                result.committed_version = new_version
                return result
            except FileConflictError as conflict:
                if attempt >= self._max_rebase:
                    logger.warning(
                        f"[ResourceScheduler] {task.get('task_id')} rebase 达上限，"
                        f"放弃提交: {conflict}"
                    )
                    return self._fail(
                        task.get("task_id", "?"), result.agent_name,
                        f"文件版本冲突且 rebase 失败: {conflict}",
                    )
                logger.info(
                    f"[ResourceScheduler] {task.get('task_id')} 提交冲突，"
                    f"rebase 到 v{conflict.current_version} 重跑（第 {attempt + 1} 次）"
                )
                # 以最新版本为新 base，重跑同一个任务节点
                base = conflict.current_version
                ctx.base_version = base
                result = await run_one(task, ctx)
                if result.status != "DONE":
                    return result
                content = getattr(result, "written_file_content", None)
                if content is None:
                    return result

        return result

    @staticmethod
    def _fail(task_id: str, agent_name: str, message: str) -> SubAgentResult:
        return SubAgentResult(
            task_id=task_id, agent_name=agent_name, scope_id="",
            status="FAILED", error_message=message,
        )


class _LockBusyError(RuntimeError):
    """文件锁等待超时或文件不可读。"""
