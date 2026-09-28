# 首次运行

## 1. 启动后端 + 依赖服务

```bash
cd docker
docker-compose up -d redis postgres milvus

cd ../backend
.venv\Scripts\activate
python -m alembic upgrade head  # 首次启动自动建表
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 2. 验证服务健康

```bash
# 存活探针
curl http://localhost:8000/healthz
# {"status":"alive"}

# 就绪探针（依赖齐全才 ready）
curl http://localhost:8000/readyz
# {"ready": true, "milvus": "connected", "redis": "connected"}

# OpenAPI 文档
# 浏览器打开 http://localhost:8000/docs
```

## 3. 第一次提交教学任务

### 同步模式（最快验证）

```bash
curl -X POST http://localhost:8000/api/v1/agents/sync-run \
  -H "Content-Type: application/json" \
  -H "X-User-Id: u-001" \
  -d '{
    "message": "请为《导数的几何意义》设计45分钟公开课教案",
    "agent_type": "lesson_plan"
  }'
```

预期返回：

```json
{
  "output": "# 导数的几何意义教案\n\n## 教学目标...",
  "agent_type": "lesson_plan",
  "citations": [],
  "trace_summary": {...}
}
```

### 流式模式（真实体验）

```bash
curl -N -X POST http://localhost:8000/api/v1/agents/sync-run \
  -H "Content-Type: application/json" \
  -H "X-User-Id: u-001" \
  -d '{"message": "初中物理《牛顿第一定律》探究式教学设计", "agent_type": "lesson_plan"}'
```

预期 SSE 流式输出 token 事件。

## 4. 触发 HITL 审批

```bash
# 提交需审批的任务
curl -X POST http://localhost:8000/api/v1/agents/run \
  -H "Content-Type: application/json" \
  -d '{
    "message": "请为高中数学函数单调性设计公开课教案，需审批后定稿",
    "agent_type": "lesson_plan"
  }'

# 响应：
# {"task_id": "task-xxx", "status": "PENDING"}

# 轮询到 WAITING_APPROVAL
curl http://localhost:8000/api/v1/agents/tasks/task-xxx/status

# 审批通过
curl -X POST http://localhost:8000/api/v1/agents/tasks/task-xxx/review \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "comments": "通过"}'
```

## 5. 查看可观测指标

```bash
# Prometheus 指标
curl http://localhost:8000/metrics

# 关键指标：
# edu_agent_llm_calls_total{agent="lesson_plan"} 1
# edu_agent_llm_latency_p95_ms{agent="lesson_plan"} 2340
# edu_agent_dependency_up{dependency="redis"} 1
```

## 6. 启动 Grafana 看板（可选）

```bash
cd docker
docker-compose up -d prometheus grafana
# 浏览器打开 http://localhost:3000（admin/admin）
# 自动加载 eduagent-overview dashboard
```

## 7. 启动 LangGraph Studio（可选）

```bash
pip install langgraph-cli[inmem]
langgraph dev --config backend/langgraph.json --port 8001
# 浏览器打开 https://smith.langchain.com/studio/?baseUrl=http://localhost:8001
```

详见 [LangGraph Studio 集成](../api/admin.md#langgraph-studio)。
