"""
OrchestratorAgent — LLM 驱动的总控智能体
三阶段循环：Plan → Execute → Reflect

核心能力：
1. Plan 阶段：LLM 分析用户请求，输出 JSON DAG（任务分解 + 依赖 + 调度策略）
2. Execute 阶段：按 DAG 拓扑排序调度 sub-agent，并发上限 3（asyncio.Semaphore）
3. Reflect 阶段：LLM 评审结果质量，必要时回退补 sub-agent

三种调度模式：
- serial：串行（有依赖链）
- parallel：并行（无依赖的同层任务）
- map_reduce：同一 task 并行跑 N 次，投票聚合（如命制 3 道不同难度题）

设计原则：
- 取代 supervisor.py 的关键词路由，改为 LLM 驱动的动态规划
- 做成通用基类：SubAgentRegistry 动态注册，未来新任务通过 register() 挂载
- context_manager 隔离每个 sub-agent 的上下文（消息 / Token 预算 / TTL）
"""
import asyncio
import json
import logging
import uuid
from typing import Dict, Any, List, Optional, Callable, Awaitable

from app.services.agent.context_manager import context_manager, ContextScope
from app.services.agent.sub_agent import (
    SubAgent, SubAgentResult, SubAgentRegistry, TokenCallback
)
from app.services.agent.intent_gate import intent_gate
from app.services.agent.resource_scheduler import ResourceScheduler, ResourceContext
from app.services.llm.bailian_client import bailian_client
from app.services.llm.router import ModelRouter
from app.prompts.registry import prompt_registry
from app.core.config import settings

logger = logging.getLogger(__name__)

# 事件回调类型
EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class OrchestratorAgent:
    """
    总控智能体
    通过 LLM 动态规划任务 DAG，调度注册表中的 sub-agent 执行
    """

    MAX_CONCURRENCY = 3  # sub-agent 并发上限（硬约束）

    PLAN_SYSTEM_PROMPT = prompt_registry.render("orchestrator.plan")

    REFLECT_SYSTEM_PROMPT = prompt_registry.render("orchestrator.reflect")

    def __init__(self):
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)

    async def run(
        self,
        user_message: str,
        thread_id: str,
        user_id: str = "u-001",
        session_id: str = "",
        kb_ids: Optional[List[str]] = None,
        event_callback: Optional[EventCallback] = None,
        on_token: Optional[TokenCallback] = None,
    ) -> Dict[str, Any]:
        """
        三阶段串联入口：Plan → Execute → Reflect
        返回最终合并的 markdown 输出 + plan_dag + trace
        """
        async def _emit(event: Dict[str, Any]):
            if event_callback:
                try:
                    await event_callback(event)
                except Exception as e:
                    logger.warning(f"[Orchestrator] event 推送失败: {e}")

        # ===== 意图门控：trivial → 单 Agent 直达，绕过 Plan =====
        gate = await intent_gate.classify(user_message)
        if gate.complexity == "trivial" and gate.agent:
            await _emit({
                "event_type": "trace",
                "task_id": session_id,
                "payload": {
                    "node_name": "IntentGate",
                    "title": f"单一意图（{gate.source}），直接派发给【{gate.agent}】，跳过任务规划",
                    "action": "THINKING_DONE",
                    "phase": "intent_gate",
                    "data": {"agent": gate.agent, "reason": gate.reason},
                }
            })
            direct_task = {
                "task_id": "T1",
                "agent": gate.agent,
                "input_summary": user_message,
                "depends_on": [],
                "map_count": 1,
            }
            result = await self._run_single_sub_agent(
                direct_task, user_message, thread_id, session_id, user_id, kb_ids,
                event_callback, on_token,
            )
            return {
                "output": result.output_markdown,
                "agent_type": gate.agent,
                "plan_dag": {
                    "nodes": [{
                        "task_id": "T1", "agent": gate.agent,
                        "input_summary": user_message[:200],
                        "depends_on": [], "map_count": 1, "status": result.status,
                    }],
                    "schedule": "trivial",
                    "total_tasks": 1,
                },
                "sub_results": [self._result_to_dict(result)],
                "reflection": {"skipped": True, "reason": "single-agent direct path"},
            }

        # ===== Plan 阶段 =====
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "node_name": "Orchestrator",
                "title": "【Plan】LLM 正在分析任务并生成执行 DAG",
                "action": "THINKING",
                "phase": "plan",
            }
        })

        plan = await self.plan(user_message, thread_id, session_id)
        tasks = plan.get("plan", [])
        schedule = plan.get("schedule", "serial")
        agg_strategy = plan.get("aggregation_strategy", "concat_with_citations")

        # 推送 plan_dag 给前端
        plan_dag = self._build_plan_dag(tasks, schedule)
        await _emit({
            "event_type": "plan_update",
            "task_id": session_id,
            "payload": {
                "plan_dag": plan_dag,
                "schedule": schedule,
                "aggregation_strategy": agg_strategy,
            }
        })

        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "node_name": "Orchestrator",
                "title": f"【Plan 完成】分解为 {len(tasks)} 个子任务，调度模式: {schedule}",
                "action": "THINKING_DONE",
                "phase": "plan",
                "data": {"task_ids": [t.get("task_id") for t in tasks]},
            }
        })

        # ===== Execute 阶段 =====
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "node_name": "Orchestrator",
                "title": "【Execute】按 DAG 调度 sub-agent 执行",
                "action": "GENERATION",
                "phase": "execute",
            }
        })

        results = await self.execute(
            tasks=tasks,
            thread_id=thread_id,
            session_id=session_id,
            user_id=user_id,
            kb_ids=kb_ids,
            schedule=schedule,
            event_callback=event_callback,
            on_token=on_token,
        )

        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "node_name": "Orchestrator",
                "title": f"【Execute 完成】{len(results)} 个 sub-agent 返回结果",
                "action": "GENERATION_DONE",
                "phase": "execute",
            }
        })

        # ===== Map-Reduce 投票聚合 =====
        if schedule == "map_reduce" or agg_strategy != "concat_with_citations":
            results = self._aggregate_results(results, agg_strategy)
            await _emit({
                "event_type": "trace",
                "task_id": session_id,
                "payload": {
                    "node_name": "Orchestrator",
                    "title": f"【聚合完成】策略: {agg_strategy}",
                    "action": "GENERATION_DONE",
                    "phase": "aggregate",
                }
            })

        # ===== Reflect 阶段 =====
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "node_name": "Orchestrator",
                "title": "【Reflect】质量评审中",
                "action": "THINKING",
                "phase": "reflect",
            }
        })

        reflection = await self.reflect(results, user_message)
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "node_name": "Orchestrator",
                "title": f"【Reflect 完成】质量分: {reflection.get('quality_score', 0)}",
                "action": "THINKING_DONE",
                "phase": "reflect",
                "data": reflection,
            }
        })

        # 如需回退补 sub-agent：最多 MAX_REFLECT_RETRIES 轮，每轮重新评审，
        # 防止"评审不通过→重做→仍不通过"的无限循环
        retry_round = 0
        while reflection.get("needs_retry") and retry_round < settings.MAX_REFLECT_RETRIES:
            retry_tasks = reflection.get("retry_tasks", [])
            valid_retry = [
                t for t in retry_tasks
                if isinstance(t, dict) and t.get("agent")
            ]
            if not valid_retry:
                break

            # 重新编号（R{轮}_{序}），避免与原 DAG 节点 id 撞键
            normalized_retry = [
                {**t, "task_id": f"R{retry_round + 1}_{i}"}
                for i, t in enumerate(valid_retry, 1)
            ]

            await _emit({
                "event_type": "trace",
                "task_id": session_id,
                "payload": {
                    "node_name": "Orchestrator",
                    "title": f"【回退·第 {retry_round + 1}/{settings.MAX_REFLECT_RETRIES} 轮】"
                             f"补充执行 {len(normalized_retry)} 个任务",
                    "action": "GENERATION",
                    "phase": "retry",
                }
            })
            retry_results = await self.execute(
                tasks=normalized_retry,
                thread_id=thread_id,
                session_id=session_id,
                user_id=user_id,
                kb_ids=kb_ids,
                schedule="parallel",
                event_callback=event_callback,
                on_token=on_token,
            )
            results.extend(retry_results)
            retry_round += 1

            # 基于补充后的全部结果重新评审
            reflection = await self.reflect(results, user_message)
            await _emit({
                "event_type": "trace",
                "task_id": session_id,
                "payload": {
                    "node_name": "Orchestrator",
                    "title": f"【Re-Reflect】第 {retry_round} 轮回退后质量分: "
                             f"{reflection.get('quality_score', 0)}"
                             + ("，仍需回退" if reflection.get("needs_retry") else ""),
                    "action": "THINKING_DONE",
                    "phase": "reflect",
                    "data": reflection,
                }
            })

        if retry_round >= settings.MAX_REFLECT_RETRIES and reflection.get("needs_retry"):
            # 达上限仍不满意：输出当前最优结果，不再重做
            logger.warning(
                f"[Orchestrator] Reflect 回退达 {settings.MAX_REFLECT_RETRIES} 轮上限，"
                f"输出当前最优结果（session={session_id}）"
            )
            await _emit({
                "event_type": "trace",
                "task_id": session_id,
                "payload": {
                    "node_name": "Orchestrator",
                    "title": f"回退已达 {settings.MAX_REFLECT_RETRIES} 轮上限，采用当前最优结果",
                    "action": "THINKING_DONE",
                    "phase": "retry_capped",
                }
            })

        # ===== 回填 plan_dag 节点最终状态（sync-run 无 SSE 也能看到结果） =====
        final_status: Dict[str, str] = {}
        for r in results:
            base_id = r.task_id.split("_map")[0] if "_map" in r.task_id else r.task_id
            final_status[base_id] = r.status
        for node in plan_dag.get("nodes", []):
            node["status"] = final_status.get(node["task_id"], node.get("status", "PENDING"))

        # ===== 合并输出 =====
        final_output = self._merge_results(results, tasks)

        return {
            "output": final_output,
            "agent_type": "orchestrator",
            "plan_dag": plan_dag,
            "sub_results": [self._result_to_dict(r) for r in results],
            "reflection": reflection,
        }

    # ==================== Plan 阶段 ====================

    async def plan(
        self, user_message: str, thread_id: str, session_id: str = ""
    ) -> Dict[str, Any]:
        """LLM 驱动的任务分解，输出 JSON DAG"""
        messages = [
            {"role": "system", "content": self.PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": f"用户需求：\n{user_message}\n\n请输出任务分解 JSON DAG。"},
        ]
        resp = await bailian_client.acomplete(
            messages, temperature=0.2, max_tokens=2048,
            response_format={"type": "json_object"},
        )
        raw = resp.get("content", "{}")
        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            # 尝试提取 JSON 块
            import re
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                try:
                    plan = json.loads(m.group())
                except json.JSONDecodeError:
                    plan = self._fallback_plan(user_message)
            else:
                plan = self._fallback_plan(user_message)

        # 校验：确保 plan 是 dict 且 plan 字段是 list
        if not isinstance(plan, dict) or not isinstance(plan.get("plan"), list):
            logger.warning(f"[Orchestrator] plan 格式异常，回退到 fallback: {type(plan)}")
            plan = self._fallback_plan(user_message)

        # 校验：确保每个 task 是 dict 且 agent 在注册表中
        available = set(SubAgentRegistry.list_agents())
        valid_tasks = []
        for task in plan.get("plan", []):
            if not isinstance(task, dict):
                continue
            agent_name = task.get("agent", "")
            if agent_name not in available:
                logger.warning(f"[Orchestrator] 未知 agent '{agent_name}'，回退到 lesson_plan")
                task["agent"] = "lesson_plan" if "lesson_plan" in available else (available.pop() if available else "lesson_plan")
            # 确保 task_id 存在
            if not task.get("task_id"):
                task["task_id"] = f"T{len(valid_tasks) + 1}"
            valid_tasks.append(task)
        plan["plan"] = valid_tasks if valid_tasks else self._fallback_plan(user_message)["plan"]

        return plan

    def _fallback_plan(self, user_message: str) -> Dict[str, Any]:
        """LLM 规划失败时的回退：单任务路由"""
        # 复用 supervisor 的关键词逻辑作为兜底
        from app.services.agent.supervisor import supervisor_agent
        classified = "lesson_plan"
        msg_lower = user_message
        if any(w in msg_lower for w in ["代码", "编程", "算法", "Debug"]):
            classified = "code_grader"
        elif any(w in msg_lower for w in ["考题", "试卷", "题目", "试题"]):
            classified = "exam_quiz"
        elif any(w in msg_lower for w in ["论文", "文献", "学术"]):
            classified = "academic_rag"
        elif any(w in msg_lower for w in ["计算", "推导", "积分", "导数", "公式"]):
            classified = "math_solver"
        elif any(w in msg_lower for w in ["课标", "核心素养"]):
            classified = "curriculum"
        elif any(w in msg_lower for w in ["批改", "作文", "评分"]):
            classified = "rubric"
        elif any(w in msg_lower for w in ["课件", "PPT", "幻灯片"]):
            classified = "slide_outline"
        elif any(w in msg_lower for w in ["引导", "为什么", "启发", "不懂"]):
            classified = "socratic"

        return {
            "plan": [{
                "task_id": "T1",
                "agent": classified,
                "input_summary": user_message,
                "depends_on": [],
                "map_count": 1,
            }],
            "schedule": "serial",
            "aggregation_strategy": "concat_with_citations",
        }

    # ==================== Execute 阶段 ====================

    async def execute(
        self,
        tasks: List[Dict[str, Any]],
        thread_id: str,
        session_id: str,
        user_id: str,
        kb_ids: Optional[List[str]],
        schedule: str,
        event_callback: Optional[EventCallback] = None,
        on_token: Optional[TokenCallback] = None,
    ) -> List[SubAgentResult]:
        """按调度模式执行 sub-agent"""
        if schedule == "map_reduce":
            return await self._execute_map_reduce(
                tasks, thread_id, session_id, user_id, kb_ids, event_callback, on_token
            )
        else:
            # serial / parallel 统一走资源感知分层波次调度
            return await self._execute_by_layers(
                tasks, thread_id, session_id, user_id, kb_ids, event_callback, on_token
            )

    async def _execute_by_layers(
        self,
        tasks: List[Dict[str, Any]],
        thread_id: str,
        session_id: str,
        user_id: str,
        kb_ids: Optional[List[str]],
        event_callback: Optional[EventCallback],
        on_token: Optional[TokenCallback],
    ) -> List[SubAgentResult]:
        """
        资源感知分层波次执行（统一 serial/parallel）：
        - 拓扑分层；同层任务通过 ResourceScheduler 并行（并发≤3）
        - 每个任务经三闸准入，锁等待不阻塞同层兄弟任务
        - 写任务自动提交版本、冲突 rebase（在调度器内闭环）
        - input_summary 中的 T1/T2 引用替换为前置波次实际输出
        """
        sorted_layers = self._topological_sort(tasks)
        results: List[SubAgentResult] = []
        output_map: Dict[str, str] = {}
        scheduler = ResourceScheduler(self._semaphore)

        for layer in sorted_layers:
            # 解析本波每个任务的输入（引用前置输出）
            summary_map: Dict[str, str] = {}
            for task in layer:
                input_summary = task.get("input_summary", "")
                for dep_id, dep_output in output_map.items():
                    input_summary = input_summary.replace(dep_id, dep_output[:2000])
                summary_map[task["task_id"]] = input_summary

            async def _run_one(task: Dict[str, Any], resource_ctx: ResourceContext):
                input_summary = summary_map.get(
                    task.get("task_id"), task.get("input_summary", "")
                )
                result = await self._run_single_sub_agent(
                    task, input_summary, thread_id, session_id, user_id, kb_ids,
                    event_callback, on_token, resource_ctx,
                )
                # 写任务：提取 Agent 产出的文件内容，交调度器提交版本
                if resource_ctx.mode == "write":
                    file_content = self._extract_file_content(result)
                    if file_content:
                        result.written_file_content = file_content
                return result

            wave_results = await scheduler.execute_wave(
                layer, _run_one, owner_id=user_id
            )
            results.extend(wave_results)
            for res in wave_results:
                if res.status == "DONE":
                    output_map[res.task_id] = res.output_markdown

        return results

    @staticmethod
    def _extract_file_content(result: SubAgentResult) -> Optional[str]:
        """
        从子 Agent 产出中提取待写入文件的完整内容（前向兼容契约）：
        artifacts 中任一 dict 带 file_content 键，或 metadata.file_content。
        没有则返回 None（该任务不产生文件版本）。
        """
        for art in result.artifacts or []:
            if isinstance(art, dict) and art.get("file_content"):
                return art["file_content"]
        if isinstance(result.metadata, dict):
            return result.metadata.get("file_content")
        return None

    async def _execute_map_reduce(
        self,
        tasks: List[Dict[str, Any]],
        thread_id: str,
        session_id: str,
        user_id: str,
        kb_ids: Optional[List[str]],
        event_callback: Optional[EventCallback],
        on_token: Optional[TokenCallback],
    ) -> List[SubAgentResult]:
        """
        Map-Reduce 模式：
        - map_count > 1 的 task 并行跑 N 次（每次不同 scope）
        - Reduce：投票聚合（confidence 加权 / 去重 / 取最高）
        """
        all_results: List[SubAgentResult] = []

        for task in tasks:
            map_count = task.get("map_count", 1)
            input_summary = task.get("input_summary", "")

            if map_count <= 1:
                # 退化为单次执行
                result = await self._run_single_sub_agent(
                    task, input_summary, thread_id, session_id, user_id, kb_ids,
                    event_callback, on_token,
                )
                all_results.append(result)
                continue

            # 并行跑 map_count 次
            async def _run_map_instance(idx: int):
                async with self._semaphore:
                    # 每个 instance 略微调整 input_summary 以增加多样性
                    instance_summary = f"{input_summary}\n（变体 {idx + 1}/{map_count}，请生成不同角度/难度版本）"
                    instance_task = {**task, "task_id": f"{task['task_id']}_map{idx}"}
                    return await self._run_single_sub_agent(
                        instance_task, instance_summary, thread_id, session_id,
                        user_id, kb_ids, event_callback, on_token,
                    )

            map_tasks = [_run_map_instance(i) for i in range(map_count)]
            map_results = await asyncio.gather(*map_tasks, return_exceptions=True)

            map_sub_results: List[SubAgentResult] = []
            for res in map_results:
                if isinstance(res, Exception):
                    map_sub_results.append(SubAgentResult(
                        task_id=task["task_id"],
                        agent_name=task.get("agent", "unknown"),
                        scope_id="",
                        status="FAILED",
                        error_message=str(res),
                    ))
                else:
                    map_sub_results.append(res)

            all_results.extend(map_sub_results)

        return all_results

    async def _run_single_sub_agent(
        self,
        task: Dict[str, Any],
        input_summary: str,
        thread_id: str,
        session_id: str,
        user_id: str,
        kb_ids: Optional[List[str]],
        event_callback: Optional[EventCallback],
        on_token: Optional[TokenCallback],
        resource_ctx: Optional[ResourceContext] = None,
    ) -> SubAgentResult:
        """执行单个 sub-agent（含 scope 创建 + 状态推送 + 执行前压缩守卫）"""
        agent_name = task.get("agent", "lesson_plan")
        task_id = task.get("task_id", f"T_{uuid.uuid4().hex[:6]}")

        async def _emit(event: Dict[str, Any]):
            if event_callback:
                try:
                    await event_callback(event)
                except Exception:
                    pass

        # 推送 task 状态为 RUNNING
        await _emit({
            "event_type": "plan_update",
            "task_id": session_id,
            "payload": {"task_id": task_id, "status": "RUNNING", "agent": agent_name},
        })

        # 资源上下文（文件/版本）写入 scope metadata，供子 Agent 按需使用
        resource_meta: Dict[str, Any] = {}
        if resource_ctx and resource_ctx.file_id:
            resource_meta = {
                "file_id": resource_ctx.file_id,
                "file_mode": resource_ctx.mode,
                "base_version": resource_ctx.base_version,
                "file_region": list(resource_ctx.region),
            }

        # 创建独立 scope
        scope = await context_manager.create_scope(
            thread_id=thread_id,
            scope_id=f"{thread_id}:sub:{task_id}",
            messages=[{"role": "user", "content": input_summary}],
            token_budget=4096,
            metadata={
                "user_id": user_id,
                "session_id": session_id,
                "kb_ids": kb_ids or [],
                "agent_name": agent_name,
                "task_id": task_id,
                "task_summary": input_summary[:200],
                **resource_meta,
            },
        )

        # 获取 sub-agent 实例
        sub_agent = SubAgentRegistry.get(agent_name)
        if sub_agent is None:
            await _emit({
                "event_type": "plan_update",
                "task_id": session_id,
                "payload": {"task_id": task_id, "status": "FAILED", "error": f"未注册的 agent: {agent_name}"},
            })
            return SubAgentResult(
                task_id=task_id, agent_name=agent_name, scope_id=scope.scope_id,
                status="FAILED", error_message=f"未注册的 agent: {agent_name}",
            )

        # 执行前守卫：scope 已携带较长历史（恢复/追加）时自动三级压缩
        compress_stats = await context_manager.maybe_compress_scope(scope)
        if compress_stats:
            await _emit({
                "event_type": "trace",
                "task_id": session_id,
                "payload": {
                    "node_name": "ContextCompressor",
                    "title": f"执行前上下文压缩 {compress_stats['before_tokens']}→"
                             f"{compress_stats['after_tokens']} tokens（{','.join(compress_stats['levels'])}）",
                    "action": "THINKING_DONE",
                }
            })

        # 执行
        try:
            result = await sub_agent.run(task, scope, on_token=on_token)
            await _emit({
                "event_type": "plan_update",
                "task_id": session_id,
                "payload": {
                    "task_id": task_id,
                    "status": "DONE" if result.status == "DONE" else "FAILED",
                    "agent": agent_name,
                    "output_preview": result.output_markdown[:200] if result.output_markdown else "",
                },
            })
            return result
        except Exception as e:
            logger.error(f"[Orchestrator] sub-agent {agent_name} 执行异常: {e}", exc_info=True)
            await _emit({
                "event_type": "plan_update",
                "task_id": session_id,
                "payload": {"task_id": task_id, "status": "FAILED", "error": str(e)},
            })
            return SubAgentResult(
                task_id=task_id, agent_name=agent_name, scope_id=scope.scope_id,
                status="FAILED", error_message=str(e),
            )

    # ==================== Reflect 阶段 ====================

    async def reflect(
        self, results: List[SubAgentResult], user_message: str
    ) -> Dict[str, Any]:
        """LLM 质量评审"""
        # 构建评审输入
        results_summary = []
        for r in results:
            preview = (r.output_markdown or "")[:500]
            results_summary.append({
                "task_id": r.task_id,
                "agent": r.agent_name,
                "status": r.status,
                "confidence": r.confidence,
                "output_preview": preview,
                "error": r.error_message,
            })

        messages = [
            {"role": "system", "content": self.REFLECT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({
                "user_request": user_message[:1000],
                "sub_agent_results": results_summary,
            }, ensure_ascii=False, indent=2)},
        ]

        try:
            resp = await bailian_client.acomplete(
                messages, temperature=0.2, max_tokens=1024,
                response_format={"type": "json_object"},
            )
            raw = resp.get("content", "{}")
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"[Orchestrator] Reflect 阶段失败: {e}")
            return {
                "quality_score": 0.7,
                "issues": [f"评审失败: {e}"],
                "needs_retry": False,
                "retry_tasks": [],
            }

    # ==================== 聚合与合并 ====================

    def _aggregate_results(
        self, results: List[SubAgentResult], strategy: str
    ) -> List[SubAgentResult]:
        """
        Map-Reduce 投票聚合
        - best_confidence: 取 confidence 最高的
        - vote_dedup: 相似内容合并
        - concat_with_citations: 不聚合（直接返回）
        """
        if strategy == "concat_with_citations" or not results:
            return results

        if strategy == "best_confidence":
            # 按 task_id 分组（去掉 _mapN 后缀），取每组 confidence 最高
            groups: Dict[str, SubAgentResult] = {}
            for r in results:
                # 归一化 task_id：T1_map0 → T1
                base_id = r.task_id.split("_map")[0] if "_map" in r.task_id else r.task_id
                if base_id not in groups or r.confidence > groups[base_id].confidence:
                    groups[base_id] = r
            return list(groups.values())

        if strategy == "vote_dedup":
            # 简单去重：按前 100 字符相似度合并
            merged: List[SubAgentResult] = []
            seen_signatures = set()
            for r in results:
                sig = (r.output_markdown or "")[:100].strip()
                if sig and sig in seen_signatures:
                    continue
                if sig:
                    seen_signatures.add(sig)
                merged.append(r)
            return merged

        return results

    def _merge_results(
        self, results: List[SubAgentResult], tasks: List[Dict[str, Any]]
    ) -> str:
        """合并所有 sub-agent 结果为最终 markdown 输出"""
        if not results:
            return "未生成有效输出。"

        if len(results) == 1 and results[0].status == "DONE":
            return results[0].output_markdown

        # 多结果：拼接带标注
        parts: List[str] = []
        for r in results:
            if r.status != "DONE" or not r.output_markdown:
                continue
            task = next((t for t in tasks if t.get("task_id") == r.task_id.split("_map")[0]), None)
            label = task.get("agent", r.agent_name) if task else r.agent_name
            parts.append(f"## 📌 {label}（{r.task_id}）\n\n{r.output_markdown}")

        if not parts:
            # 全部失败时返回错误信息
            errors = [f"- {r.task_id}: {r.error_message}" for r in results if r.error_message]
            return f"所有 sub-agent 执行失败：\n" + "\n".join(errors)

        return "\n\n---\n\n".join(parts)

    # ==================== 辅助方法 ====================

    def _topological_sort(self, tasks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        拓扑排序：按 depends_on 分层
        返回 [[layer0_tasks], [layer1_tasks], ...]
        同层内的 task 互相无依赖，可并行
        """
        task_map = {t.get("task_id", f"T{i}"): t for i, t in enumerate(tasks)}
        resolved: set = set()
        layers: List[List[Dict[str, Any]]] = []

        remaining = list(tasks)
        while remaining:
            layer = []
            for task in remaining:
                deps = task.get("depends_on", [])
                if all(d in resolved for d in deps):
                    layer.append(task)
            if not layer:
                # 循环依赖兜底：把剩余全放进当前层
                layer = remaining
                logger.warning(f"[Orchestrator] 检测到循环依赖，强制展开: {[t.get('task_id', '?') for t in layer]}")
            for t in layer:
                resolved.add(t.get("task_id", ""))
                remaining.remove(t)
            layers.append(layer)

        return layers

    def _build_plan_dag(self, tasks: List[Dict[str, Any]], schedule: str) -> Dict[str, Any]:
        """构建前端可渲染的 Plan DAG 结构"""
        nodes = []
        for t in tasks:
            nodes.append({
                "task_id": t.get("task_id", ""),
                "agent": t.get("agent", ""),
                "input_summary": t.get("input_summary", "")[:200],
                "depends_on": t.get("depends_on", []),
                "map_count": t.get("map_count", 1),
                "status": "PENDING",
            })
        return {
            "nodes": nodes,
            "schedule": schedule,
            "total_tasks": len(nodes),
        }

    def _result_to_dict(self, r: SubAgentResult) -> Dict[str, Any]:
        return {
            "task_id": r.task_id,
            "agent_name": r.agent_name,
            "scope_id": r.scope_id,
            "output_preview": (r.output_markdown or "")[:300],
            "citations": r.citations,
            "artifacts": r.artifacts,
            "confidence": r.confidence,
            "token_used": r.token_used,
            "status": r.status,
            "error_message": r.error_message,
        }


# 全局单例
orchestrator_agent = OrchestratorAgent()
