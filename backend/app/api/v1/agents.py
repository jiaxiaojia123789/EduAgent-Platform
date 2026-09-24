import uuid
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from app.schemas.agent import AgentRunRequest, AgentTaskStatus
from app.services.task_queue.task_manager import task_manager
from app.services.agent.teaching_graph import teaching_graph_runner
from app.services.chat.conversation_storage import conversation_storage

router = APIRouter(prefix="/agents", tags=["Agent Workflow Engine"])


class HITLDecisionBody(BaseModel):
    """教师对 HITL 暂停任务的审批决策（task_id 走路径参数）"""
    approved: bool
    comments: Optional[str] = None

EDUCATION_AGENT_MATRIX = [
    {
        "id": "supervisor",
        "name": "中台总控智能体",
        "avatar": "🧭",
        "badge": "智能中枢",
        "description": "智能识别教学需求意图，自动分流至最适合的学科专家智能体",
        "greeting": "您好，老师！我是智学中台的【总控智能体】。我会先听懂您的教学意图，再把任务拆解、分派给最合适的学科专家，并统筹它们协同产出。无论您要备课、命题、研读文献还是批改作业，直接告诉我需求即可，我已为您挂载专属教学记忆与 Agent Harness 安全底座。",
        "sample_prompts": ["高中数学导数第二课时教学设计", "2024年高考物理受力分析试题命制", "关于大模型促进乡村教育的学术文献研读"]
    },
    {
        "id": "lesson_plan",
        "name": "教案大师智能体",
        "avatar": "📚",
        "badge": "教学设计",
        "description": "严格依循国家学科新课程标准，输出包含素养目标、学习环节、板书设计的完整导学案",
        "greeting": "老师好，我是【教案大师】📚。我会严格对标最新课程标准，帮您产出包含核心素养目标、学情分析、教学环节、课堂活动与板书设计的完整教案/导学案。请告诉我课题、学段课时，以及您偏好的课型（如新授课、复习课、公开课）。",
        "sample_prompts": ["请为《导数的几何意义》设计一份45分钟精品公开课教案", "初中物理《牛顿第一定律》探究式教学设计"]
    },
    {
        "id": "academic_rag",
        "name": "学术研读智能体",
        "avatar": "🔬",
        "badge": "文献综述",
        "description": "基于 Milvus 2.4 与 Hybrid Search 深度阅读学术期刊论文，提供严密学术论证与引用标注",
        "greeting": "您好，我是【学术研读智能体】🔬。我可以帮您检索、研读学术期刊论文，输出文献综述、观点梳理与课题论证，每一处结论都会绑定规范引用并做事实核验（降低幻觉）。请告诉我研究主题，或指定要查阅的知识库范围。",
        "sample_prompts": ["大模型赋能个性化自适应学习的有效性学术综述", "项目式学习在高中化学课改中的实证研究证据"]
    },
    {
        "id": "exam_quiz",
        "name": "命题组卷专家",
        "avatar": "📝",
        "badge": "自适应题库",
        "description": "自适应命制梯度试题，自带标准评分细则、分步采分点与学生常见易错点剖析",
        "greeting": "老师好，我是【命题组卷专家】📝。我能按知识点与难度梯度命制选择、填空、解答题，并附上分步采分细则、参考答案和学生常见易错点分析。请说明学科、章节、题型题量与目标难度，我也支持多份平行卷与组卷。",
        "sample_prompts": ["命制3道高中数学导数单调性与极值典型压轴题", "生成初中英语定语从句易错填空题与详解"]
    },
    {
        "id": "socratic",
        "name": "苏格拉底答疑专家",
        "avatar": "💡",
        "badge": "启发式引导",
        "description": "不直接给出答案，通过循循善诱的反问与生活类比引导学生自主思维破冰",
        "greeting": "你好呀，我是【苏格拉底答疑老师】💡。我不会马上把答案塞给你，而是通过一个个问题和生活中的例子，陪你把思路一步步理顺。把你卡住的地方告诉我，我们一起把它想明白——先说说你现在是怎么想的？",
        "sample_prompts": ["老师，为什么曲线的割线极限就是切线？我不太理解", "负负得正为什么在数轴上是成立的？"]
    },
    {
        "id": "math_solver",
        "name": "数理推导专家",
        "avatar": "📐",
        "badge": "符号严密演算",
        "description": "严谨分步推导微积分、几何解析与理论物理公式，确保 100% LaTeX 语法规范",
        "greeting": "您好，我是【数理推导专家】📐。我会对微积分、解析几何与物理公式进行严谨的分步推导，明确定义域与边界条件，所有公式均用规范 LaTeX 呈现。请给出题目或需要证明/求解的表达式。",
        "sample_prompts": ["详细推导函数 f(x) = e^x / x 在 x > 0 时的单调区间与极值", "带电粒子在匀强交变电场中的运动轨迹分步计算"]
    },
    {
        "id": "curriculum",
        "name": "课标素养对标专家",
        "avatar": "🎯",
        "badge": "新课标审查",
        "description": "对教学目标与试卷进行学科核心素养达成度审查，输出量化雷达与修改建议",
        "greeting": "您好，我是【课标素养对标专家】🎯。我会依据学科课程标准，审查您的教学目标、课堂活动或试卷在核心素养上的达成度，输出量化评估与具体修改建议。请提供需要审查的教案、试题或评价方案。",
        "sample_prompts": ["审查这份化学教学设计是否充分落实了宏观辨识与微观探析核心素养"]
    },
    {
        "id": "rubric",
        "name": "主观题智能批改专家",
        "avatar": "✍️",
        "badge": "量规诊断",
        "description": "针对作文与解答题进行多维度量规打分，指出失分点并提供升格润色范例",
        "greeting": "老师好，我是【主观题智能批改专家】✍️。我会从内容、结构、表达、思维等维度对作文或解答题量规打分，标出失分点并给出升格润色范例。请把学生作答（及评分标准，如有）发给我。",
        "sample_prompts": ["对这篇高中议论文进行高考一类卷标准诊断与采分点批阅"]
    },
    {
        "id": "slide_outline",
        "name": "课件与教学大纲专家",
        "avatar": "📊",
        "badge": "课件大纲",
        "description": "将教案快速转化为结构化 PPT 课件大纲，注明每页重点与教师讲授台词",
        "greeting": "您好，我是【课件与教学大纲专家】📊。我能把教案或备课要点转化为结构化 PPT 大纲，规划每页标题、版式建议、图示互动与教师讲授台词（speaker notes）。请提供教案或主题，并说明希望的页数。",
        "sample_prompts": ["将《导数的几何意义》教案提炼为12页公开课PPT课件大纲"]
    },
    {
        "id": "code_grader",
        "name": "代码批改与沙箱实测专家",
        "avatar": "💻",
        "badge": "沙箱真机评测",
        "description": "基于AST安全防护与隔离子进程沙箱，真机跑测学生代码用例，输出时空复杂度诊断与规范重构方案",
        "greeting": "你好，我是【代码批改与沙箱实测专家】💻。我会在隔离沙箱中真机运行学生代码、自动跑测试用例，诊断正确性、边界条件与时空复杂度，并给出规范的重构方案。请贴出代码（含题目或测试要求）。",
        "sample_prompts": [
            "批改二分查找算法代码：\ndef binary_search(nums, target):\n    left, right = 0, len(nums) - 1\n    while left <= right:\n        mid = (left + right) // 2\n        if nums[mid] == target: return mid\n        elif nums[mid] < target: left = mid + 1\n        else: right = mid - 1\n    return -1",
            "批改两数之和 Python 解法并评估时间复杂度与边界条件",
            "测试以下递归求斐波那契数列的代码并给出记忆化优化重构建议"
        ]
    }
]


@router.get("/matrix")
async def get_agent_matrix():
    """Returns the matrix of educational specialized agents."""
    return {"agents": EDUCATION_AGENT_MATRIX}


@router.post("/run")
async def run_agent_async(payload: AgentRunRequest, background_tasks: BackgroundTasks):
    """
    Submits an agent task asynchronously to prevent HTTP 504 timeout.
    Returns task_id immediately.

    重构说明：
    - 优先投递到 Celery 队列，由独立 worker 进程执行
    - Celery 不可用时降级到 FastAPI BackgroundTasks（仍走 Redis Pub/Sub 推流）
    - 任务状态持久化到 Redis，重启不丢失
    - 幂等锁防止用户连击
    """
    session_id = payload.session_id or str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    agent_type = payload.agent_type or "supervisor"
    user_id = payload.user_id or "u-001"
    user_message = payload.message

    # 1. 创建任务并持久化到 Redis
    task_id = await task_manager.create_task(
        session_id=session_id,
        thread_id=thread_id,
        agent_type=agent_type,
        user_id=user_id,
        user_message=user_message,
    )

    # 2. 投递到执行队列（conversation_id 在执行器开场绑定：沿用传入会话或新建，
    #    会话创建与用户消息落库由执行器完成，避免连击 duplicate 时产生空垃圾会话）
    submit_result = await task_manager.submit_task(
        task_id=task_id,
        user_message=user_message,
        session_id=session_id,
        thread_id=thread_id,
        user_id=user_id,
        user_role="teacher",
        agent_type=agent_type,
        kb_ids=payload.kb_ids,
        conversation_id=payload.conversation_id,
        sub_agent_mode=payload.sub_agent_mode,
    )

    dispatcher = submit_result.get("dispatcher")
    if dispatcher == "fallback":
        # Celery 不可用，走进程内 fallback
        background_tasks.add_task(
            task_manager.execute_task_async,
            task_id=task_id,
            user_message=user_message,
            user_id=user_id,
            user_role="teacher",
            kb_ids=payload.kb_ids,
            conversation_id=payload.conversation_id,
            sub_agent_mode=payload.sub_agent_mode,
        )
    elif dispatcher == "duplicate":
        # 幂等拦截
        return {
            "task_id": task_id,
            "session_id": session_id,
            "thread_id": thread_id,
            "status": "DUPLICATE",
            "message": submit_result.get("message", "任务已在执行中"),
            "dispatcher": dispatcher,
        }

    return {
        "task_id": task_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "status": "PENDING",
        "dispatcher": dispatcher,
        "message": "任务已成功提交至异步调度队列",
        "stream_url": f"/api/v1/agents/tasks/{task_id}/stream",
        "status_url": f"/api/v1/agents/tasks/{task_id}/status",
    }


@router.post("/sync-run")
async def run_agent_sync(payload: AgentRunRequest):
    """
    Synchronous execution endpoint for quick testing and API debugging.

    历史对话落库：
    - 携带 conversation_id：在对应历史对话中继续
    - 不携带：新建一条归属当前 agent 的历史对话
    - 用户消息与 assistant 结果（含 plan_dag / artifact）实时写入 SQLite

    错误处理说明：
    - LLM 调用失败（如 DashScope 配额耗尽、网络异常）时，抛出 HTTPException 503
      而不是让 RuntimeError 漏到 ASGI 层变成 500。
    - 这样 CORS 中间件能正常附加响应头，前端拿到明确的错误信息而非模糊的 CORS 失败。
    """
    user_id = payload.user_id or "u-001"
    agent_type = payload.agent_type or "supervisor"

    # 1. 解析历史对话：继续已有对话，或创建新对话
    conversation_id = payload.conversation_id
    if conversation_id:
        conv = conversation_storage.get_session(conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="历史对话不存在或已被删除")
        thread_id = conv.get("thread_id") or str(uuid.uuid4())
        session_id = payload.session_id or thread_id
    else:
        thread_id = str(uuid.uuid4())
        session_id = payload.session_id or str(uuid.uuid4())
        conv = conversation_storage.create_session(
            user_id=user_id,
            agent_type=agent_type,
            session_id=session_id,
            thread_id=thread_id,
        )
        conversation_id = conv["id"]

    # 2. 用户消息落库，并在首条消息时自动生成对话标题
    conversation_storage.auto_title_if_needed(conversation_id, payload.message)
    conversation_storage.append_message(
        conversation_id, role="user", content=payload.message
    )

    try:
        result = await teaching_graph_runner.run_workflow(
            user_message=payload.message,
            session_id=session_id,
            thread_id=thread_id,
            user_id=user_id,
            explicit_agent=payload.agent_type,
            kb_ids=payload.kb_ids,
            sub_agent_mode=payload.sub_agent_mode,
            auto_approve=True,  # 同步链路保持旧行为：不中断等待审批
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        # LLM 上游错误（如 DashScope 403 配额耗尽）→ 503 让前端拿到明确提示
        err_msg = str(e)
        if "AllocationQuota" in err_msg or "FreeTierOnly" in err_msg:
            raise HTTPException(
                status_code=503,
                detail="阿里云百炼 API 免费额度已耗尽：请在百炼控制台充值或关闭「仅使用免费层」模式后重试。"
            )
        raise HTTPException(status_code=503, detail=f"LLM 调用失败：{err_msg}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"工作流执行异常：{e}")

    # 3. assistant 结果落库（含协同视窗、Plan DAG 等扩展数据）
    result_agent_type = result.get("agent_type") or agent_type
    agent_meta = next(
        (a for a in EDUCATION_AGENT_MATRIX if a["id"] == result_agent_type), None
    )
    extra = {
        "plan_dag": result.get("plan_dag"),
        "sub_results": result.get("sub_results"),
        "artifact": result.get("artifact"),
        "artifact_type": result.get("artifact_type"),
        "agent_name": agent_meta["name"] if agent_meta else result_agent_type,
        "agent_avatar": agent_meta["avatar"] if agent_meta else "🤖",
    }
    conversation_storage.append_message(
        conversation_id,
        role="assistant",
        content=result.get("output", ""),
        citations=result.get("citations") or [],
        extra=extra,
    )

    result["conversation_id"] = conversation_id
    return result


@router.get("/tasks/{task_id}/status")
async def get_task_status(task_id: str):
    """Polls async task status and trace execution steps."""
    status = await task_manager.get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return status


@router.get("/graph/visualization")
async def get_graph_visualization(format: str = "mermaid"):
    """
    教学图结构可视化：
    - mermaid（默认）：Mermaid.js 源码，前端可直接渲染
    - ascii：终端 ASCII 图
    - png：经 mermaid.ink 渲染 PNG（需出网；不可达返回 503）
    """
    from app.services.agent.teaching_graph import get_teaching_graph

    graph = await get_teaching_graph()
    drawn = graph.get_graph()
    fmt = format.lower()

    if fmt == "mermaid":
        return {"format": "mermaid", "diagram": drawn.draw_mermaid()}
    if fmt == "ascii":
        return {"format": "ascii", "diagram": drawn.draw_text()}
    if fmt == "png":
        import base64
        import httpx
        from fastapi import Response

        encoded = base64.urlsafe_b64encode(
            drawn.draw_mermaid().encode("utf-8")
        ).decode()
        url = f"https://mermaid.ink/img/{encoded}?type=png"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"PNG 渲染失败（mermaid.ink 不可达）：{e}",
            )
        return Response(content=resp.content, media_type="image/png")
    raise HTTPException(status_code=400, detail="format 仅支持 mermaid / ascii / png")


@router.post("/tasks/{task_id}/approval")
async def review_task(task_id: str, body: HITLDecisionBody, background_tasks: BackgroundTasks):
    """
    教师对 HITL 暂停任务提交审批决策。
    - approved=true：图从 hitl_gate 续跑到 END，随后正常推 artifact/done 事件
    - approved=false：带教师意见进入 revision 返工，返工后可再次审批
    续跑结果仍通过原 SSE 通道（/tasks/{task_id}/stream）推送，前端保持订阅即可。
    """
    status = await task_manager.get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    if status.get("status") != "WAITING_APPROVAL":
        raise HTTPException(
            status_code=409,
            detail=f"任务当前状态为 {status.get('status')}，无需审批（仅 WAITING_APPROVAL 可审批）",
        )

    # 恢复路径：
    # - 暂停态在本进程注册表：直接恢复
    # - checkpointer 为 RedisSaver：跨进程从 checkpoint 恢复（Celery worker 重启/异机均可）
    # - MemorySaver 且本地无暂停态：无法恢复，明确返回 503
    from app.services.agent.teaching_graph import checkpointer_backend

    if not teaching_graph_runner.get_paused_run(task_id):
        if checkpointer_backend() != "redis":
            raise HTTPException(
                status_code=503,
                detail="暂停态不在当前进程且 checkpointer 非 RedisSaver，无法跨进程恢复",
            )

    background_tasks.add_task(
        task_manager.resume_task,
        task_id,
        body.approved,
        body.comments,
    )
    return {
        "task_id": task_id,
        "status": "RESUMING",
        "approved": body.approved,
        "stream_url": f"/api/v1/agents/tasks/{task_id}/stream",
    }


@router.get("/tasks/{task_id}/stream")
async def stream_task_events(task_id: str):
    """
    SSE Stream endpoint pushing real-time thinking traces and tokens.

    实现说明：
    - 订阅 Redis Pub/Sub channel 'sse:task:{task_id}'
    - 自动补偿：客户端断线重连时先回放 List 中最近 64 条历史事件
    - 推送 5 类事件：trace / token / artifact / done / error
    """
    return StreamingResponse(
        task_manager.stream_task_events(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
            "Access-Control-Allow-Origin": "*",
        },
    )
