# 故障排查

## 常见问题

### 1. DashScope API 免费额度已耗尽

**现象**：调用 `/api/v1/agents/sync-run` 返回 403 FreeTierOnly。

**原因**：阿里云百炼免费额度用完。

**解决**：

- 充值百炼账户
- 关闭「仅使用免费层」模式
- 配置 OpenAI 兼容供应商兜底（`.env`）：
  ```env
  LLM_PROVIDERS=dashscope,openai_fallback
  OPENAI_FALLBACK_API_KEY=sk-xxx
  OPENAI_FALLBACK_BASE_URL=https://api.openai.com/v1
  OPENAI_FALLBACK_DEFAULT_MODEL=gpt-4o-mini
  ```

### 2. Redis 缺少模块 ReJSON/search

**现象**：日志告警 `Redis 缺少模块 {'ReJSON', 'search'}`，checkpointer 降级 MemorySaver。

**原因**：Redis 7- 不含 RedisJSON/RediSearch 模块。

**解决**：升级到 Redis 8+（`docker-compose.yml` 已配置 `redis:8-alpine`）。

```bash
docker-compose down redis
docker-compose up -d redis
```

### 3. LangGraph InvalidUpdateError

**现象**：并行 agent 执行后报 `InvalidUpdateError`。

**原因**：aggregate 节点返回空 dict `{}`，LangGraph 要求 reducer 输出非空。

**解决**：aggregate 节点始终回写无副作用的 state key：

```python
return {
    "final_markdown_output": final_output,
    "sub_results": sub_results,
    "current_agent": "aggregate",
}
```

### 4. HITL 跨进程恢复 503

**现象**：Celery worker 暂停的 HITL，FastAPI 进程恢复时返回 503。

**原因**：checkpointer 是 MemorySaver（进程内），跨进程无法访问。

**解决**：使用 RedisSaver（`docker-compose.yml` 升级到 Redis 8+）。

验证：

```bash
curl http://localhost:8000/metrics | grep checkpointer_backend
# edu_agent_checkpointer_backend{backend="redis"} 1.0
```

### 5. 复合任务被记忆 prompt 污染

**现象**：复合任务拆解误命中关键词，触发了不该触发的 agent。

**原因**：意图节点用 `messages[-1]` 做意图分类，但 messages[-1] 含记忆 prompt，引入误命中词。

**解决**：意图节点和条件边统一使用原始用户消息：

```python
raw_msg = state.get("raw_user_message") or (
    state["messages"][-1]["content"] if state.get("messages") else ""
)
```

### 6. 数学话题词过度匹配

**现象**：「教案」「试题」中的「函数」「几何」等词触发了 math_solver 节点。

**原因**：数学话题词在教学制品语境下不应触发数学求解器。

**解决**：在 supervisor 中检查上下文是否为教学制品（教案/试题/课件），如果是则跳过 math_solver。

### 7. /metrics 端点超时

**现象**：Prometheus 抓取 `/metrics` 超时。

**原因**：`agent_stats(since_hours=24)` SQL 聚合太慢（prompt_call_metrics 表数据量大）。

**解决**：给 `prompt_call_metrics.ts` 加索引：

```sql
CREATE INDEX idx_metrics_ts_agent ON prompt_call_metrics(ts, agent);
```

### 8. 沙箱 Windows 下 stderr 污染测试断言

**现象**：Windows 上跑 `test_code_grader.py` 失败，stderr 含告警文本。

**原因**：Windows prelude 向 stderr 输出告警，污染了沙箱 stderr 测试断言。

**解决**：Windows prelude 改为静默注释（不输出到 stderr）：

```python
def _windows_prelude(self) -> str:
    return "# [Sandbox] Windows 模式：仅靠 subprocess.timeout 兜底"
```

## 调试技巧

### 启用 debug 日志

```env
LOG_LEVEL=DEBUG
```

### 查看 LangGraph 节点 trace

```bash
# 启动 LangGraph Studio
langgraph dev --config backend/langgraph.json --port 8001
# 浏览器打开 https://smith.langchain.com/studio/?baseUrl=http://localhost:8001
```

### 查看断路器状态

```bash
curl http://localhost:8000/metrics | grep circuit_breaker
```

### 强制重置断路器

```python
from app.harness.circuit_breaker import llm_breaker
llm_breaker.reset()
```

### 清空 LLM 响应缓存

```python
from app.services.llm.response_cache import llm_cache
await llm_cache.clear_all()
```
