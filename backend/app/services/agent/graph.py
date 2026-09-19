import logging
from typing import Dict, Any, List, Optional, Callable, Awaitable
from app.services.agent.state import AgentState
from app.services.agent.supervisor import supervisor_agent
from app.services.agent.sub_agent import SubAgentRegistry
from app.services.agent.orchestrator import orchestrator_agent
from app.services.agent.specialized.lesson_plan import lesson_plan_agent
from app.services.agent.specialized.academic_rag import academic_rag_agent
from app.services.agent.specialized.exam_quiz import exam_quiz_agent
from app.services.agent.specialized.socratic import socratic_tutor_agent
from app.services.agent.specialized.math_solver import math_solver_agent
from app.services.agent.specialized.curriculum import curriculum_aligner_agent
from app.services.agent.specialized.rubric_grading import rubric_grading_agent
from app.services.agent.specialized.slide_outline import slide_outline_agent
from app.services.agent.specialized.code_grader import code_grader_agent
from app.services.agent.checkpointer import session_checkpointer
from app.harness.base import AgentHarness
from app.harness.fingerprint import dispose_fingerprint_guard

logger = logging.getLogger(__name__)

# 事件回调类型：async def(event_dict) -> None
EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class MultiAgentGraphEngine:
    """
    LangGraph Multi-Agent Workflow Engine
    Orchestrates:
    - Supervisor Intent Router
    - 8 Specialized Domain Agents
    - Self-Reflection & Quality Review Loop
    - Human-In-The-Loop (HITL) Gate

    重构说明：
    - 新增 event_callback 参数：把每个 thinking step + LLM token 实时推送到 SSE 通道
    - 修复原实现只在结束时一次性返回 trace 的痛点
    - 与 Celery worker 解耦：caller（task_manager）传入 callback，graph_engine 不感知具体推送方式
    """

    @classmethod
    async def run_workflow(
        cls,
        user_message: str,
        session_id: str,
        thread_id: str,
        user_id: str = "u-001",
        user_role: str = "teacher",
        explicit_agent: Optional[str] = None,
        kb_ids: Optional[List[str]] = None,
        harness: Optional[AgentHarness] = None,
        event_callback: Optional[EventCallback] = None,
        sub_agent_mode: bool = False,
    ) -> Dict[str, Any]:
        harness = harness or AgentHarness(session_id=session_id, user_role=user_role)

        async def _emit(event: Dict[str, Any]):
            """安全调用 callback，吞掉推送异常防止影响主流程"""
            if event_callback is None:
                return
            try:
                await event_callback(event)
            except Exception as e:
                logger.warning(f"[GraphEngine] event_callback 推送失败: {e}")

        # 1. Harness: Pre-run safety & injection checks
        sanitized_input = harness.before_run(user_message)
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "title": "Agent Harness 前置安全审计：防越狱注入 / PII 脱敏 / 步数与 Token 预算初始化",
                "action": "THINKING",
                "phase": "harness_pre_check",
            }
        })

        # 2. Memory Retrieval
        from app.services.memory.memory_service import memory_service
        mem_step = harness.on_step("MemoryRetriever", "TOOL_RESULT", "正在检索并挂载授课教师专属教学画像与生效记忆")
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "step_id": mem_step,
                "node_name": "MemoryRetriever",
                "title": "正在检索并挂载授课教师专属教学画像与生效记忆",
                "action": "TOOL_RESULT",
            }
        })
        memory_prompt = memory_service.build_memory_prompt(user_id)
        harness.finish_step(mem_step, detail="已成功加载专属教学画像、学情诊断与生效教学记忆")
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "step_id": mem_step,
                "node_name": "MemoryRetriever",
                "title": "已成功加载专属教学画像、学情诊断与生效教学记忆",
                "action": "TOOL_RESULT_DONE",
                "elapsed_ms": 0,
            }
        })

        enriched_input = f"{sanitized_input}\n\n{memory_prompt}"

        # ===== Orchestrator 分支：复合请求或显式 sub_agent_mode 时走多 sub-agent 协作 =====
        use_orchestrator = sub_agent_mode or (
            explicit_agent in (None, "supervisor", "orchestrator")
            and SubAgentRegistry.is_compound_request(user_message)
        )
        if use_orchestrator:
            await _emit({
                "event_type": "trace",
                "task_id": session_id,
                "payload": {
                    "node_name": "Orchestrator",
                    "title": "检测到复合教学需求，切换至 Orchestrator 多 sub-agent 协作模式",
                    "action": "THINKING",
                    "phase": "orchestrator_entry",
                }
            })

            # Orchestrator 的 on_token 透传
            async def _orch_on_token(chunk: str):
                await _emit({
                    "event_type": "token",
                    "task_id": session_id,
                    "payload": {"text": chunk, "agent": "orchestrator"}
                })

            try:
                orch_result = await orchestrator_agent.run(
                    user_message=user_message,
                    thread_id=thread_id,
                    user_id=user_id,
                    session_id=session_id,
                    kb_ids=kb_ids,
                    event_callback=event_callback,
                    on_token=_orch_on_token,
                )
            finally:
                # Orchestrator 分支不走 harness.after_run，此处兜底销毁守卫
                dispose_fingerprint_guard(session_id)

            # 后置语法校验（复用原 QualityJudge 逻辑）
            from app.harness.benchmark import BenchmarkHarness
            latex_check = BenchmarkHarness.validate_latex_syntax(orch_result.get("output", ""))

            # 从 sub_results 中提取最佳 artifact（取第一个有 markdown 产出的 sub-agent 结果）
            sub_results = orch_result.get("sub_results", [])
            best_artifact = None
            citations = []

            def _extract_markdown(art):
                """兼容 dict 和 Pydantic 模型两种 artifact 格式"""
                if isinstance(art, dict):
                    return art.get("markdown", "")
                # Pydantic 模型
                if hasattr(art, "model_dump"):
                    d = art.model_dump()
                    return d.get("markdown", "") if isinstance(d, dict) else ""
                if hasattr(art, "markdown"):
                    return getattr(art, "markdown", "")
                return ""

            for sr in sub_results:
                if sr.get("status") == "DONE":
                    arts = sr.get("artifacts") or []
                    for art in arts:
                        md = _extract_markdown(art)
                        if md:
                            # 统一转为 dict 格式
                            if isinstance(art, dict):
                                best_artifact = art
                            elif hasattr(art, "model_dump"):
                                best_artifact = art.model_dump()
                            else:
                                best_artifact = {"title": sr.get("agent_name", "sub-agent"), "markdown": md}
                            break
                    if best_artifact:
                        break
            # 如果 sub-agent 没有单独 artifact，用最终合并输出作为 artifact markdown
            if not best_artifact:
                merged_output = orch_result.get("output", "")
                if merged_output:
                    best_artifact = {"title": "协同教学成果", "markdown": merged_output}

            return {
                "output": orch_result.get("output", ""),
                "agent_type": "orchestrator",
                "citations": citations,
                "artifact": best_artifact,
                "artifact_type": best_artifact.get("type") if isinstance(best_artifact, dict) else None,
                "trace_summary": harness.get_trace_summary(),
                "plan_dag": orch_result.get("plan_dag"),
                "sub_results": sub_results,
                "reflection": orch_result.get("reflection", {}),
            }

        # 3. Initialize State
        state: AgentState = {
            "messages": [{"role": "user", "content": enriched_input}],
            "user_id": user_id,
            "session_id": session_id,
            "thread_id": thread_id,
            "intent": explicit_agent or "supervisor",
            "current_agent": "start",
            "next_agent": "supervisor",
            "plan_steps": [],
            "current_step": 0,
            "kb_ids": kb_ids or [],
            "retrieved_docs": [],
            "citations": [],
            "structured_artifact": None,
            "artifact_type": None,
            "final_markdown_output": "",
            "requires_approval": False,
            "is_approved": False,
            "reviewer_comments": None,
            "retry_count": 0,
            "error_message": None
        }

        # Step 1: Supervisor Routing
        step_id = harness.on_step("Supervisor", "THINKING", "分析教学意图并进行任务拆解与分发")
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "step_id": step_id,
                "node_name": "Supervisor",
                "title": "分析教学意图并进行任务拆解与分发",
                "action": "THINKING",
            }
        })
        route_decision = await supervisor_agent.route(state)
        state.update(route_decision)
        next_agent = state["next_agent"]
        harness.finish_step(step_id, detail=f"已分流至专业智能体: {next_agent}")
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "step_id": step_id,
                "node_name": "Supervisor",
                "title": f"已分流至专业智能体: {next_agent}",
                "action": "THINKING_DONE",
            }
        })

        # Step 2: Route to Specialized Agent（带 token 流推送）
        agent_target = state["next_agent"]
        step_id = harness.on_step(
            node_name=agent_target.capitalize(),
            action_type="GENERATION",
            title=f"【{agent_target}】正在进行专业领域推理与生成"
        )
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "step_id": step_id,
                "node_name": agent_target.capitalize(),
                "title": f"【{agent_target}】正在进行专业领域推理与生成",
                "action": "GENERATION",
            }
        })

        # 把 on_token 回调注入到 specialized agent 执行
        async def _on_token(chunk: str):
            await _emit({
                "event_type": "token",
                "task_id": session_id,
                "payload": {"text": chunk, "agent": agent_target}
            })

        agent_result = await cls._dispatch_agent(
            agent_target, state, on_token=_on_token
        )
        state.update(agent_result)
        harness.finish_step(step_id, detail="专业领域生成完成")
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "step_id": step_id,
                "node_name": agent_target.capitalize(),
                "title": "专业领域生成完成",
                "action": "GENERATION_DONE",
            }
        })

        # Step 3: Quality Review Node
        step_id = harness.on_step(
            node_name="QualityJudge",
            action_type="THINKING",
            title="执行教学合规性与 LaTeX 语法自检"
        )
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "step_id": step_id,
                "node_name": "QualityJudge",
                "title": "执行教学合规性与 LaTeX 语法自检",
                "action": "THINKING",
            }
        })
        from app.harness.benchmark import BenchmarkHarness
        latex_check = BenchmarkHarness.validate_latex_syntax(state["final_markdown_output"])
        harness.finish_step(step_id, detail=f"语法检查: {'通过' if latex_check['is_valid'] else '警示'}")
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "step_id": step_id,
                "node_name": "QualityJudge",
                "title": f"语法检查: {'通过' if latex_check['is_valid'] else '警示'}",
                "action": "THINKING_DONE",
                "data": latex_check,
            }
        })

        # Step 4: Harness: Post-run output checks
        validated_output = harness.after_run(state["final_markdown_output"])
        state["final_markdown_output"] = validated_output
        await _emit({
            "event_type": "trace",
            "task_id": session_id,
            "payload": {
                "node_name": "Harness_PostGuardrail",
                "title": "后置教学合规审查通过",
                "action": "POST_GUARDRAIL_DONE",
            }
        })

        # Save Checkpoint
        session_checkpointer.save_checkpoint(thread_id, step_index=1, state=state)

        return {
            "output": state["final_markdown_output"],
            "agent_type": state["current_agent"],
            "citations": state.get("citations", []),
            "artifact": state.get("structured_artifact"),
            "artifact_type": state.get("artifact_type"),
            "trace_summary": harness.get_trace_summary(),
            "plan_dag": None,
            "sub_results": [],
            "reflection": {},
        }

    @classmethod
    async def _dispatch_agent(
        cls,
        agent_target: str,
        state: AgentState,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """
        分发到专业智能体
        新增 on_token 参数：透传到支持流式输出的 agent
        """
        # 优先尝试支持流式输出的入口（agent.execute_stream）
        # 不存在则回退到原 execute
        agents_map = {
            "lesson_plan": lesson_plan_agent,
            "academic_rag": academic_rag_agent,
            "exam_quiz": exam_quiz_agent,
            "socratic": socratic_tutor_agent,
            "math_solver": math_solver_agent,
            "curriculum": curriculum_aligner_agent,
            "rubric": rubric_grading_agent,
            "slide_outline": slide_outline_agent,
            "code_grader": code_grader_agent,
        }
        agent = agents_map.get(agent_target, lesson_plan_agent)

        # 优先使用流式入口
        if hasattr(agent, "execute_stream") and on_token is not None:
            try:
                return await agent.execute_stream(state, on_token=on_token)
            except AttributeError:
                pass

        # 回退：普通 execute，无 token 流
        return await agent.execute(state)


graph_engine = MultiAgentGraphEngine()
