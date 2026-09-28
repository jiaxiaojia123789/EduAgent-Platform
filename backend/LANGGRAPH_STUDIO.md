# LangGraph Studio 集成

## 配置文件

`backend/langgraph.json`：

```json
{
  "dependencies": ["."],
  "graphs": {
    "teaching_graph": "./app/services/agent/teaching_graph.py:get_teaching_graph_sync"
  },
  "env": ".env"
}
```

**入口函数**：`get_teaching_graph_sync()` —— 同步 wrapper，桥接 async 单例 `get_teaching_graph()`，返回编译后的 LangGraph 实例（含 AsyncRedisSaver checkpointer）。

## 启动方式

### 方式 1：本地 Studio（开发调试推荐）

```bash
pip install langgraph-cli[inmem]
cd backend
langgraph dev --config langgraph.json --port 8001

# 浏览器打开：
# https://smith.langchain.com/studio/?baseUrl=http://localhost:8001
```

### 方式 2：Docker 部署（团队共享）

```bash
cd backend
langgraph build --config langgraph.json -t eduagent-studio
docker run -p 8001:8000 \
  -e REDIS_URL=redis://redis:6379 \
  -e DASHSCOPE_API_KEY=sk-xxx \
  eduagent-studio
```

## Studio 能力

| 能力 | 说明 |
|---|---|
| 图拓扑可视化 | 自动渲染 mermaid，节点可点击查看 state diff |
| HITL 暂停调试 | 原生支持 `interrupt()` + `Command(resume=)` 的暂停/续跑 UI |
| trace 回放 | 加载 Redis checkpointer 历史会话，逐节点回放 |
| 状态快照 | 每个节点的 state snapshot（intent → agent → aggregate → quality_review） |
| 实时调用 | 直接在 Studio 提交消息，观察图执行流程 |

## 调试场景

### 场景 1：HITL 暂停/续跑

1. 在 Studio 提交「请为《函数单调性》设计公开课教案，需审批后定稿」
2. 图执行到 `hitl_gate` 节点时暂停（`interrupt()` 触发）
3. Studio 显示暂停状态 + 等待审批的输出
4. 在 Studio UI 点击「Approve」/「Reject」
5. 图通过 `Command(resume={"approved": true})` 续跑

### 场景 2：复合任务并行

1. 提交「请同时完成：1）高一数学《函数单调性》教案 2）相关习题 3）课件大纲 4）评价量规」
2. Studio 显示 `intent_node` 返回 Send fan-out 列表
3. 9 个专业 agent 节点并行执行（visual timeline）
4. aggregate 节点合并 sub_results
5. quality_review 节点评分

### 场景 3：trace 回放

1. 选择历史会话（从 Redis checkpointer 加载）
2. 逐节点查看 state diff
3. 排查「为什么走到了这个分支」「为什么 quality_score 低」

## 故障排查

### Studio 无法加载图

```bash
# 验证入口函数可调用
cd backend
python -c "from app.services.agent.teaching_graph import get_teaching_graph_sync; g = get_teaching_graph_sync(); print(type(g))"
# <class 'langgraph.graph.state.CompiledStateGraph'>
```

### checkpointer 降级

Studio 启动时如果告警 `checkpointer 后端: MemorySaver`：

- 检查 Redis 是否启动（`docker-compose up -d redis`）
- 检查 Redis 版本（需 8+，`docker exec -it eduagent-redis redis-server --version`）
- 检查 Redis 模块（`redis-cli MODULE LIST` 应含 ReJSON + search）

### LangGraph CLI 版本不兼容

```bash
# 项目锁定版本矩阵
pip install langgraph==1.2.11 langgraph-cli==0.0.100 langgraph-checkpoint==4.2
```
