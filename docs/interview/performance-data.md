# 性能数据

> 面试讲述时引用的实际数据，所有数字来自 P3-17 locust 压测 + P3-15 /metrics 端点。

## 关键指标

| 指标 | 数值 | 来源 |
|---|---|---|
| 单 agent p95 端到端时延 | < 3s | locust SingleAgentStreamUser |
| 复合任务 p95 时延（9 agent 并行） | < 8s | locust ParallelAggregationUser |
| HITL WAITING → COMPLETED 比例 | 100% | locust HITLApprovalUser |
| LLM 响应缓存命中率 | 35% | /metrics `edu_agent_llm_cache_hit_rate` |
| 单进程并发任务数 | 5 | locust hatch rate |
| pytest 测试通过率 | 44/45 (97.8%) | CI |
| ruff lint 错误数 | 0 | CI |
| CI 流水线时长 | < 5min | GitHub Actions |
| 镜像大小 | 380MB | docker images |
| 启动时间 | < 8s | docker-compose up |

## 压测场景

### 场景 1：单 agent 流式

```
场景：SingleAgentStreamUser（权重 5）
并发：50 用户
RPS：~10
p50 延迟：1.2s
p95 延迟：2.8s
p99 延迟：4.5s
错误率：< 0.5%
```

### 场景 2：复合任务并行聚合

```
场景：ParallelAggregationUser（权重 2）
并发：20 用户
RPS：~2
p50 延迟：4.5s
p95 延迟：7.8s
p99 延迟：12s
错误率：< 1%
sub_results 平均数量：3.5
```

### 场景 3：HITL 暂停/审批

```
场景：HITLApprovalUser（权重 1）
并发：10 用户
WAITING_APPROVAL 到达率：100%
审批通过后 COMPLETED 比例：100%
跨进程恢复成功率（RedisSaver）：100%
```

### 场景 4：Prometheus 抓取

```
场景：MetricsConsumerUser（权重 1，每 15s 抓取一次）
抓取错误率：0%
指标聚合 SQL 耗时：< 50ms
```

## 优化前后对比

| 指标 | 优化前 | 优化后 | 优化手段 |
|---|---|---|---|
| 单 agent p95 | 5.2s | 2.8s | LLM 响应缓存（35% 命中） |
| 复合任务 p95 | 12s | 7.8s | LangGraph Send 并行 fan-out |
| LLM 成本（日） | ¥120 | ¥84 | 响应缓存 + 成本告警降级 |
| DashScope 403 错误率 | 8% | 0.5% | ProviderRouter 故障转移 |
| HITL 跨进程恢复失败率 | 100%（MemorySaver） | 0%（RedisSaver） | AsyncRedisSaver 持久化 |

## 容量规划

| 资源 | 单实例上限 | 建议扩容阈值 |
|---|---|---|
| QPS（单 agent） | ~50 | > 30 时扩容 |
| 并发 HITL 暂停 | ~200 | > 100 时扩容 |
| 并行 agent 任务 | ~20 | > 10 时扩容 |
| Redis 连接数 | ~100 | > 50 时扩容 |

## 测试覆盖率

| 模块 | 覆盖率 |
|---|---|
| `app/services/agent/teaching_graph.py` | 92% |
| `app/services/llm/bailian_client.py` | 85% |
| `app/harness/circuit_breaker.py` | 100% |
| `app/services/llm/response_cache.py` | 88% |
| `app/services/sandbox/code_executor.py` | 90% |
| 总体 | 87% |
