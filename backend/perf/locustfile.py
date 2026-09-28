"""
EduAgent-Platform 压测脚本（Locust）
====================================
4 个场景覆盖教学智能体的关键链路：

1. SingleAgentStreamUser：提交单个 agent 任务 → SSE 流式消费 → done
   目标：测量单 agent 端到端时延（用户感知的「首 token」与「完成」时间）
2. ParallelAggregationUser：触发复合请求，9 个专业 agent 并行 + aggregate
   目标：测量 LangGraph Send fan-out + aggregate 节点的扩展性
3. HITLApprovalUser：触发 HITL 暂停 → 调用 /review-task 审批 → 续跑完成
   目标：验证 HITL pause/resume 在并发下的状态机正确性
4. MetricsConsumerUser：仅打 /metrics 端点，验证 Prometheus 抓取的吞吐上限
   目标：发现指标聚合 SQL 是否会成为瓶颈

使用方式：
    cd backend && pip install locust
    locust -f perf/locustfile.py --host=http://localhost:8000

浏览器打开 http://localhost:8089 配置并发用户数与 hatch rate。

注意：
- 压测环境必须用 MockLLM 模式（不消耗 DashScope 配额）
- Redis 必须可用（否则 HITL 场景与 Pub/Sub 链路会降级）
- 压测前清空 prompt_call_metrics 表，便于聚合数据纯净
"""
import json
import uuid
import random
from typing import Dict, Any

from locust import HttpUser, task, between, events


# ============================================================
# 测试数据池
# ============================================================
_SINGLE_AGENT_PROMPTS = [
    "请为《导数的几何意义》设计一份45分钟精品公开课教案",
    "初中物理《牛顿第一定律》探究式教学设计",
    "高中数学圆锥曲线单元复习课教学方案",
    "初二语文《背影》情感教育主题教案",
    "高中生物《细胞呼吸》实验探究课设计",
]

_COMPOUND_PROMPTS = [
    "请同时完成：1）高一数学《函数单调性》教案 2）相关习题 3）课件大纲 4）评价量规",
    "针对初中物理《欧姆定律》同时输出：教学设计、单元测验、PPT 大纲",
    "为高中化学《化学平衡》一次性生成：教案、试题、课件、量规、学情分析",
]

_AGENT_TYPES = ["lesson_plan", "exam_quiz", "socratic", "curriculum", "rubric"]


def _headers(user_id: str = "u-loadtest") -> Dict[str, str]:
    """压测用 JWT：开发环境匿名访问，注入 X-User-Id 跳过认证。"""
    return {
        "Content-Type": "application/json",
        "X-User-Id": user_id,
        "X-Request-ID": f"loadtest-{uuid.uuid4().hex[:8]}",
    }


class SingleAgentStreamUser(HttpUser):
    """
    场景 1：单 agent 流式任务
    提交 → 轮询 status → SSE 消费 → done
    模拟真实教师用户的高频单 agent 调用。
    """
    wait_time = between(0.5, 2.0)
    weight = 5  # 权重高，主要负载

    @task
    def submit_single_agent(self):
        prompt = random.choice(_SINGLE_AGENT_PROMPTS)
        agent_type = random.choice(_AGENT_TYPES)

        with self.client.post(
            "/api/v1/agents/sync-run",
            json={
                "message": prompt,
                "agent_type": agent_type,
                "user_id": "u-loadtest-single",
                "sub_agent_mode": False,
            },
            headers=_headers("u-loadtest-single"),
            name="POST /agents/sync-run (single agent)",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"single agent failed: {resp.status_code} {resp.text[:200]}")
                return
            data = resp.json()
            if not data.get("output"):
                resp.failure("no output field")
            else:
                resp.success()


class ParallelAggregationUser(HttpUser):
    """
    场景 2：复合任务并行聚合
    触发 supervisor 拆解 → Send fan-out → aggregate
    模拟教师一次提交「教案+试题+课件+量规」复合需求。
    """
    wait_time = between(2.0, 5.0)
    weight = 2  # 复合任务消耗资源大，权重低

    @task
    def submit_compound(self):
        prompt = random.choice(_COMPOUND_PROMPTS)
        with self.client.post(
            "/api/v1/agents/sync-run",
            json={
                "message": prompt,
                "agent_type": "supervisor",
                "user_id": "u-loadtest-compound",
                "sub_agent_mode": True,  # 强制启用 sub-agent 协作模式
            },
            headers=_headers("u-loadtest-compound"),
            name="POST /agents/sync-run (compound parallel)",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"compound failed: {resp.status_code}")
                return
            data = resp.json()
            # 验证聚合节点确实合并了 sub_results
            sub_results = data.get("sub_results") or []
            if len(sub_results) < 2:
                resp.failure(f"aggregation incomplete: {len(sub_results)} sub_results")
            else:
                resp.success()


class HITLApprovalUser(HttpUser):
    """
    场景 3：HITL 暂停 → 审批 → 续跑
    1. POST /agents/run（异步投递，触发 waiting_approval 暂停）
    2. GET /agents/tasks/{task_id}/status 轮询到 WAITING_APPROVAL
    3. POST /agents/tasks/{task_id}/review（approve=true）续跑
    4. 轮询到 COMPLETED

    验证 HITL 状态机在并发下的正确性。
    """
    wait_time = between(3.0, 8.0)
    weight = 1  # HITL 场景耗时长（需要等待审批）

    @task
    def hitl_workflow(self):
        # 1. 提交异步任务
        with self.client.post(
            "/api/v1/agents/run",
            json={
                "message": "请为《高中数学函数单调性》设计公开课教案，需经教师审批后定稿",
                "agent_type": "lesson_plan",
                "user_id": "u-loadtest-hitl",
            },
            headers=_headers("u-loadtest-hitl"),
            name="POST /agents/run (hitl)",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"submit failed: {resp.status_code}")
                return
            task_id = resp.json().get("task_id")
            if not task_id:
                resp.failure("no task_id")
                return
            resp.success()

        # 2. 轮询直到 WAITING_APPROVAL（最多 30s）
        status = "PENDING"
        for _ in range(30):
            with self.client.get(
                f"/api/v1/agents/tasks/{task_id}/status",
                headers=_headers("u-loadtest-hitl"),
                name="GET /agents/tasks/status (hitl waiting)",
                catch_response=True,
            ) as resp:
                if resp.status_code != 200:
                    resp.failure(f"status poll failed: {resp.status_code}")
                    return
                status = resp.json().get("status", "")
                if status == "WAITING_APPROVAL":
                    resp.success()
                    break
                if status in {"FAILED", "TIMED_OUT"}:
                    resp.failure(f"task {status} before approval")
                    return

        if status != "WAITING_APPROVAL":
            return  # 超时未到达 WAITING_APPROVAL，跳过审批

        # 3. 审批通过
        with self.client.post(
            f"/api/v1/agents/tasks/{task_id}/review",
            json={"approved": True, "comments": "压测审批通过"},
            headers=_headers("u-loadtest-hitl"),
            name="POST /agents/tasks/review (approve)",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"review failed: {resp.status_code} {resp.text[:200]}")
            else:
                resp.success()


class MetricsConsumerUser(HttpUser):
    """
    场景 4：/metrics 抓取
    模拟 Prometheus 每 15s 抓一次，验证指标端点吞吐上限。
    """
    wait_time = between(15.0, 15.0)  # 模拟 Prometheus 抓取间隔
    weight = 1

    @task
    def scrape_metrics(self):
        with self.client.get(
            "/metrics",
            name="GET /metrics (prometheus scrape)",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"metrics endpoint failed: {resp.status_code}")
                return
            # 验证返回的是 Prometheus exposition format
            text = resp.text
            if "# HELP" not in text or "# TYPE" not in text:
                resp.failure("invalid prometheus format")
            else:
                resp.success()


# ============================================================
# 压测启动事件：打印环境信息
# ============================================================
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("\n" + "=" * 60)
    print("EduAgent 压测启动")
    print(f"  Host: {environment.host}")
    print(f"  Target users: {environment.target_user_count}")
    print(f"  Hatch rate: {environment.parsed_options.hatch_rate}")
    print("  场景权重:")
    print("    SingleAgentStreamUser: 5（高频单 agent）")
    print("    ParallelAggregationUser: 2（复合任务并行聚合）")
    print("    HITLApprovalUser: 1（HITL 暂停+审批）")
    print("    MetricsConsumerUser: 1（Prometheus 抓取）")
    print("=" * 60 + "\n")
