# API 总览

## 基础信息

- **Base URL**: `http://localhost:8000`
- **API 前缀**: `/api/v1`
- **认证**: JWT (HS256, 24h 过期) / API Key / 匿名访问 (dev)
- **格式**: JSON (请求/响应) + SSE (流式事件)
- **文档**: http://localhost:8000/docs (Swagger UI) / http://localhost:8000/redoc (ReDoc)

## 端点分组

| 分组 | 端点 | 用途 |
|---|---|---|
| 智能体 | `POST /agents/sync-run` | 同步执行单 agent |
| 智能体 | `POST /agents/run` | 异步执行（返回 task_id） |
| 智能体 | `GET /agents/tasks/{id}/status` | 查询任务状态 |
| 智能体 | `GET /agents/tasks/{id}/stream` | SSE 订阅任务事件流 |
| 智能体 | `POST /agents/tasks/{id}/review` | HITL 审批决策 |
| 智能体 | `GET /agents/graph/visualization` | 图拓扑可视化 (mermaid/ascii/png) |
| 智能体 | `GET /agents/experiments` | A/B 实验统计 |
| 会话 | `POST /chat/sessions` | 创建历史会话 |
| 会话 | `GET /chat/sessions/{id}/messages` | 拉取会话消息 |
| 知识库 | `POST /rag/kb` | 创建知识库 |
| 知识库 | `POST /rag/kb/{id}/documents` | 上传文档 |
| 元信息 | `GET /healthz` / `GET /readyz` | 存活/就绪探针 |
| 元信息 | `GET /metrics` | Prometheus 指标抓取 |

## 认证方式

### JWT（推荐）

```bash
# 登录获取 token
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "teacher", "password": "xxx"}'

# 响应：{"access_token": "eyJ...", "token_type": "bearer"}

# 带 token 调用
curl -H "Authorization: Bearer eyJ..." http://localhost:8000/api/v1/agents/sync-run
```

### API Key（服务间调用）

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/v1/agents/sync-run
```

### 匿名访问（dev/ci）

```bash
curl -H "X-User-Id: u-001" http://localhost:8000/api/v1/agents/sync-run
```

## 错误码

| HTTP | code | 说明 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 请求参数校验失败 |
| 401 | `UNAUTHORIZED` | 未认证或 token 过期 |
| 403 | `FORBIDDEN` | 无权限访问资源 |
| 404 | `NOT_FOUND` | 资源不存在 |
| 429 | `TENANT_QUOTA_EXCEEDED` | 租户配额超限 |
| 429 | `RATE_LIMITED` | 触发限流 |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 |
| 503 | `SERVICE_UNAVAILABLE` | 依赖不可用（如 Redis） |

统一错误响应：

```json
{
  "detail": {
    "code": "TENANT_QUOTA_EXCEEDED",
    "message": "租户配额已超限：日 token 总量超限：2000000/2000000"
  },
  "request_id": "req-abc123"
}
```

## LangGraph Studio

详见 [运维 → 部署](../operations/deployment.md#langgraph-studio)。
