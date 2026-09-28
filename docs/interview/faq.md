# 常见追问

## 架构类

### Q: 9 个 agent 是怎么划分的？为什么不合并成更少的 agent？

> 按教师工作场景划分，每个 agent 对应一种教学制品：教案/学术检索/试卷/苏格拉底提问/数学求解/课程大纲/量规/课件/代码批改。每个 agent 的 prompt、RAG 知识库、输出 schema 都不同，合并会降低质量。并行执行时 9 agent 同时跑，aggregate 合并，性能不损失。

### Q: LangGraph 图怎么保证幂等性？

> 节点用 `_with_node_metrics` 包装，所有副作用写入 state（不修改外部资源）。checkpointer 在每个节点边界自动 snapshot，崩溃重启能从最近 checkpoint 恢复。`interrupt()` 暂停的 HITL 节点也走 checkpoint，跨进程恢复时通过 `conversation_id` 关联。

### Q: 异步任务为什么用 Celery 不用 FastAPI BackgroundTasks？

> BackgroundTasks 无法跨进程扩展、无法持久化、worker 崩溃任务丢失。Celery 提供：① 跨进程 worker 池（K8s 多副本）② Redis broker 持久化（崩溃自动重投递）③ 任务路由（长任务走专用队列）④ Flower 监控 ⑤ `task_acks_late=True` 任务完成才 ack。BackgroundTasks 仅作 Celery 不可用时的进程内兜底。

## 性能类

### Q: LLM 调用慢怎么排查？

> 三步：① `/metrics` 看 `edu_agent_llm_latency_p95_ms` 找瓶颈 agent ② structlog 日志按 `trace_id` 串联，看是 LLM 本身慢还是 RAG 检索/aggregate 慢 ③ LangGraph Studio 加载历史 trace，逐节点看 elapsed_ms。常见瓶颈：DashScope qwen-max 延迟 2-3s，可切 qwen-turbo（快 3 倍但质量降）。

### Q: 多 agent 并行会不会比串行快？

> 不一定。并行的好处是「同时跑多个 agent，总耗时 = max(agent 耗时)」vs 串行「sum(agent 耗时)」。但 LLM API 有 QPS 限制，9 个 agent 同时打满可能触发限流。项目用 ProviderRouter 跨供应商分摊 QPS，DashScope 打一个，OpenAI 兜底打另一个。

## 安全类

### Q: 沙箱怎么防逃逸？

> 三层：① AST 静态分析拦截 os/sys/subprocess/socket/eval/exec 等危险导入 ② Linux setrlimit 限制 CPU 5s / 内存 256MB / 进程数 1 / 文件 1MB ③ `unshare --net` 隔离网络命名空间，子进程没有网络接口。Windows 降级为软限制（仅 subprocess.timeout 兜底），生产环境必须在 Linux 跑。

### Q: 多租户怎么保证数据不串？

> 三层：① TenantContextMiddleware 从 JWT claim 提取 tenant_id，绑定 contextvar ② 配额检查超限直接 429 阻断 ③ 业务查询带 `WHERE tenant_id = ?`。生产建议配合 PostgreSQL Row-Level Security 加强，避免 ORM 漏写 WHERE。

### Q: JWT token 泄露怎么办？

> ① 短过期时间（24h）② 用户登出时把 token 加 Redis 黑名单（jti 失效）③ 异常登录触发审计日志告警 ④ 支持 API Key 模式（服务间调用，不暴露给用户）。

## 故障类

### Q: Redis 挂了系统还能跑吗？

> 能跑，但部分降级：① checkpointer 降级 MemorySaver（HITL 仅进程内，不能跨进程恢复）② 响应缓存降级进程内 LRU ③ Celery 不可用 → BackgroundTasks 进程内兜底 ④ 任务事件总线降级 InProcEventBus（仅单进程）。`/metrics` 端点会暴露 `edu_agent_dependency_up{dependency="redis"} == 0`，Grafana 告警触发。

### Q: DashScope 配额耗尽怎么办？

> ProviderRouter 自动故障转移：① DashScope 断路器连续失败 5 次 → OPEN ② 路由到下一个供应商（如 OpenAI 兼容兜底）③ 全部供应商熔断 → 返回 RuntimeError。前端收到 403 FreeTierOnly 时显示「阿里云百炼 API 免费额度已耗尽」中文提示。

## 面试技巧

### 没问到的话怎么主动展示？

- 「我做了 LLM 成本可观测，发现 code_grader p95 8s，通过响应缓存优化到 3s」
- 「我设计了断路器 + ProviderRouter 故障转移，单供应商故障不影响可用性」
- 「我用 LangGraph Studio 可视化图拓扑，节点级 state diff 让排查效率提升 3 倍」
- 「我做了 A/B 实验框架，能对比不同 prompt/agent 组合的质量分和成本」

### 被问到「为什么不选 X」时怎么回答？

- 先肯定 X 的优点（展示开放心态）
- 再说项目硬指标为什么 X 不满足（具体到功能/性能）
- 最后说选的方案有什么代价（展示权衡能力）

例：「AutoGen 的多 agent 对话很灵活，但 HITL 审批要自己实现状态持久化，跨进程恢复时容易丢状态。LangGraph 的代价是图定义更严格，灵活性低，但生产场景下 HITL 的可靠性更重要。」
