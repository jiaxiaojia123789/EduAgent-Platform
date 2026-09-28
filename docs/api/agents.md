# 智能体 API

## 同步执行

`POST /api/v1/agents/sync-run`

```bash
curl -X POST http://localhost:8000/api/v1/agents/sync-run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "message": "请为《导数的几何意义》设计45分钟公开课教案",
    "agent_type": "lesson_plan",
    "sub_agent_mode": false
  }'
```

响应：

```json
{
  "output": "# 导数的几何意义教案\n\n## 教学目标...",
  "agent_type": "lesson_plan",
  "citations": [],
  "artifact": {...},
  "trace_summary": {
    "node_timings": {...}
  }
}
```

## 异步执行

`POST /api/v1/agents/run`

```bash
curl -X POST http://localhost:8000/api/v1/agents/run \
  -H "Content-Type: application/json" \
  -d '{
    "message": "...",
    "agent_type": "lesson_plan",
    "conversation_id": "conv-xxx"
  }'
```

响应：

```json
{
  "task_id": "task-xxx",
  "status": "PENDING"
}
```

## 任务状态查询

`GET /api/v1/agents/tasks/{task_id}/status`

## SSE 订阅

`GET /api/v1/agents/tasks/{task_id}/stream`

返回 SSE 事件流：

```
data: {"event_type":"trace","payload":{"node_name":"Supervisor",...}}
data: {"event_type":"token","payload":{"text":"导数",...}}
data: {"event_type":"approval_required","payload":{...}}
data: {"event_type":"done","payload":{"output":"..."}}
```

详见 [流式生成](../features/streaming.md)。

## HITL 审批

`POST /api/v1/agents/tasks/{task_id}/review`

```bash
curl -X POST http://localhost:8000/api/v1/agents/tasks/task-xxx/review \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "comments": "通过"}'
```

详见 [HITL 审批](../features/hitl-approval.md)。

## 图可视化

`GET /api/v1/agents/graph/visualization?format=mermaid`

支持 mermaid / ascii / png 三种格式。

## A/B 实验统计

`GET /api/v1/agents/experiments`

详见 [A/B 实验](../features/ab-experiments.md)。
