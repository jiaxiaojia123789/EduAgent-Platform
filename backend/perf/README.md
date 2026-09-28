# EduAgent 压测方案

## 场景覆盖

| 场景 | 文件位置 | 目标 | 关键指标 |
|---|---|---|---|
| 单 agent 流式 | `locustfile.py: SingleAgentStreamUser` | 真实教师高频单 agent 调用 | p95 端到端时延 |
| 复合并行聚合 | `locustfile.py: ParallelAggregationUser` | Send fan-out + aggregate 扩展性 | sub_results 数量、合并耗时 |
| HITL 暂停/审批 | `locustfile.py: HITLApprovalUser` | pause/resume 状态机正确性 | WAITING→COMPLETED 比例 |
| Prometheus 抓取 | `locustfile.py: MetricsConsumerUser` | /metrics 端点吞吐上限 | 抓取 RPS、错误率 |

## 运行方式

```bash
# 1. 安装 locust
cd backend && pip install locust

# 2. 启动后端（MockLLM 模式，不消耗 DashScope 配额）
export ENVIRONMENT=loadtest
export DASHSCOPE_API_KEY=        # 空 key 触发 mock
export DATABASE_URL=sqlite+aiosqlite:///./data/loadtest.db
export REDIS_HOST=localhost
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3. 启动 locust（Web UI 模式）
locust -f perf/locustfile.py --host=http://localhost:8000
# 浏览器打开 http://localhost:8089 配置并发数

# 4. 无头模式（CI 集成）
locust -f perf/locustfile.py --host=http://localhost:8000 \
  --headless -u 50 -r 5 --run-time 60s \
  --csv=perf/loadtest_report \
  --html=perf/loadtest_report.html
```

## 关键阈值

| 指标 | 阈值 | 报警 |
|---|---|---|
| 单 agent p95 时延 | < 3s | > 5s 阻断发版 |
| 复合任务 p95 时延 | < 8s | > 12s 阻断 |
| HITL WAITING→COMPLETED | 100% | < 95% 阻断 |
| /metrics 抓取错误率 | 0% | > 0% 阻断 |
| 整体错误率 | < 1% | > 5% 阻断 |

## 排查指南

- **单 agent 时延异常**：检查 `prompt_call_metrics.latency_ms` 是否被某个模型拖累
- **复合任务 sub_results 缺失**：检查 LangGraph aggregate 节点是否返回空 dict
- **HITL 审批失败**：检查 `checkpointer_backend()` 是否为 redis（memory 模式跨进程失败）
- **/metrics 超时**：`agent_stats(since_hours=24)` SQL 太慢，给 `prompt_call_metrics.ts` 加索引
