# 运维 API

## 健康探针

| 端点 | 用途 | 是否需认证 |
|---|---|---|
| `GET /healthz` | 存活探针（Kubernetes liveness） | 否 |
| `GET /readyz` | 就绪探针（依赖齐全才 ready） | 否 |
| `GET /metrics` | Prometheus 指标抓取 | 否 |

## LangGraph Studio

### 启动

```bash
pip install langgraph-cli[inmem]
langgraph dev --config backend/langgraph.json --port 8001
```

浏览器打开 `https://smith.langchain.com/studio/?baseUrl=http://localhost:8001`

### 能力

- **图拓扑可视化**：自动渲染 mermaid，节点可点击查看 state diff
- **HITL 暂停调试**：原生支持 `interrupt()` 节点的暂停/续跑 UI
- **trace 回放**：加载 Redis checkpointer 历史会话，逐节点回放
- **状态快照**：每个节点的 state snapshot（intent → agent → aggregate → quality_review）

详见 [系统架构 → LangGraph 工作流](../architecture/langgraph-flow.md)。
