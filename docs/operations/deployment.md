# 部署

## Docker Compose（推荐）

### 完整生产栈

```bash
cd docker
docker-compose up -d
```

服务清单：

| 服务 | 端口 | 镜像 | 用途 |
|---|---|---|---|
| backend | 8000 | eduagent-backend:latest | FastAPI 应用 |
| frontend | 5173 | eduagent-frontend:latest | Vue 前端 |
| redis | 6379 | redis:8-alpine | checkpointer + 任务总线 + 响应缓存 |
| postgres | 5432 | postgres:16-alpine | 会话/审计日志 |
| milvus | 19530 | milvusdb/milvus:v2.4.6 | 向量库 |
| celery-worker | - | eduagent-backend:latest | 异步任务执行 |
| prometheus | 9090 | prom/prometheus:v0.49.1 | 指标抓取 |
| grafana | 3000 | grafana/grafana:10.4.0 | 可视化看板 |

### Grafana 看板

```bash
docker-compose up -d prometheus grafana
# 浏览器打开 http://localhost:3000（admin/admin）
# 自动加载 eduagent-overview dashboard
```

dashboard 文件位置：`docker/grafana/dashboards/eduagent-overview.json`

## Kubernetes（生产推荐）

### 部署清单

```yaml
# k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: eduagent
---
# k8s/backend-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: eduagent-backend
  namespace: eduagent
spec:
  replicas: 3
  selector:
    matchLabels:
      app: eduagent-backend
  template:
    metadata:
      labels:
        app: eduagent-backend
    spec:
      containers:
        - name: backend
          image: eduagent-backend:latest
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef:
                name: eduagent-secrets
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8000
          readinessProbe:
            httpGet:
              path: /readyz
              port: 8000
          resources:
            limits:
              cpu: 2000m
              memory: 2Gi
            requests:
              cpu: 500m
              memory: 512Mi
```

## LangGraph Studio 集成

### 配置文件

`backend/langgraph.json`：

```json
{
  "dependencies": ["./app"],
  "graphs": {
    "teaching_graph": "./app/services/agent/teaching_graph.py:build_teaching_graph"
  },
  "env": ".env"
}
```

### 启动

```bash
# 本地 Studio（开发调试）
pip install langgraph-cli[inmem]
langgraph dev --config backend/langgraph.json --port 8001

# 浏览器打开：
# https://smith.langchain.com/studio/?baseUrl=http://localhost:8001

# Docker 部署（团队共享）
langgraph build --config backend/langgraph.json -t eduagent-studio
docker run -p 8001:8000 -e REDIS_URL=redis://redis:6379 eduagent-studio
```

### Studio 能力

- **图拓扑可视化**：自动渲染 mermaid，节点可点击查看 state diff
- **HITL 暂停调试**：原生支持 `interrupt()` + `Command(resume=)` 的暂停/续跑 UI
- **trace 回放**：加载 Redis checkpointer 历史会话，逐节点回放
- **状态快照**：每个节点的 state snapshot（intent → agent → aggregate → quality_review）

## CI/CD 流水线

详见 [CI/CD 流水线](ci-cd.md)。

## 健康检查

| 探针 | 端点 | 行为 |
|---|---|---|
| liveness | `GET /healthz` | 进程存活即返回 200 |
| readiness | `GET /readyz` | Redis + Milvus 都 connected 才返回 200，否则 503 |
| metrics | `GET /metrics` | Prometheus 指标抓取 |

## 配置

详见 [配置说明](../getting-started/configuration.md)。
